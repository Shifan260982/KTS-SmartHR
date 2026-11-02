import hashlib
import logging
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Response, UploadFile, status
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app import db, jd, matching, reader
from app.schemas import (ApplicationOut, ApplicationSummary, DocumentText, JobCreate, JobDraft, JobOut, JobSettings,
                         RubricUpdate)

log = logging.getLogger("smarthr")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@asynccontextmanager
async def lifespan(_: FastAPI):
    matching.warm_up()
    db.init(matching.dimensions())
    _rebuild_stale_chunks(db.database())
    yield


app = FastAPI(
    title="SmartHR API",
    version="0.2.0",
    description="Resume shortlisting by semantic matching: MongoDB Atlas Vector Search + Atlas Search (BM25), "
                "fused and re-checked by a cross-encoder reranker. No LLM stage yet.",
    lifespan=lifespan,
)
api = APIRouter(prefix="/api/v1")
Db = Depends(db.get_db)


# ---------- helpers ----------

def _get_job(d: Database, job_id: int) -> dict:
    job = d.jobs.find_one({"_id": job_id})
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Job {job_id} not found")
    return job


def _job_out(d: Database, job: dict) -> dict:
    return {**job, "id": job["_id"],
            "application_count": d.applications.count_documents({"job_id": job["_id"]}),
            "attention_count": d.applications.count_documents({"job_id": job["_id"], "status": "NEEDS_ATTENTION"})}


def _application_out(application: dict) -> dict:
    evidence = application.get("evidence")
    return {**application, "id": application["_id"],
            "marks": [{"requirement_id": e["requirement_id"], "verdict": e["verdict"]} for e in evidence] if evidence else None}


def _get_application(d: Database, application_id: int) -> dict:
    application = d.applications.find_one({"_id": application_id})
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Application {application_id} not found")
    return _application_out(application)


def _score(d: Database, application_id: int) -> None:
    """Score one application against its job's approved rubric and persist the result."""
    application = d.applications.find_one({"_id": application_id})
    job = _get_job(d, application["job_id"])
    unscored = {"band": None, "fit_score": None, "coverage": None, "semantic": None, "evidence": None, "models": None}

    if application["quality"] != "ok":
        # An unreadable resume gets no score, never a low score.
        d.applications.update_one({"_id": application_id}, {"$set": {
            **unscored, "status": "NEEDS_ATTENTION", "rubric_version": job["rubric_version"]}})
        return

    settings = JobSettings(**job["settings"]).model_dump()  # fills defaults for fields added after the job was saved
    search = partial(_search, d, application["document_id"])
    evidence = matching.match_requirements(job["rubric"], settings, search)
    score = matching.compute_score(evidence, settings)
    d.applications.update_one({"_id": application_id}, {"$set": {
        "status": "SCORED", **score, "evidence": evidence, "rubric_version": job["rubric_version"],
        "models": matching.models()}})
    log.info("Scored application %s: %s (%s)", application_id, score["fit_score"], score["band"])


def _search(d: Database, document_id: int, text: str, vector: list[float]):
    return db.search_chunks(d, document_id, text, vector, matching.CANDIDATES_PER_REQUIREMENT)


def _rescore_job(d: Database, job_id: int) -> None:
    for application in d.applications.find({"job_id": job_id}, {"_id": 1}):
        _score(d, application["_id"])


def _store_document(d: Database, file: UploadFile, data: bytes, sha256: str) -> dict:
    try:
        text = reader.extract_text(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    except Exception:
        log.exception("Could not read %s", sha256)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "File could not be read")

    chunks = reader.chunk(text)
    quality = reader.quality(text, chunks)
    # Named by fingerprint so a candidate's filename can never touch the folder layout.
    (db.ORIGINALS_DIR / f"{sha256}{Path(file.filename or '').suffix.lower()}").write_bytes(data)
    document = {"_id": db.next_id(d, "documents"), "sha256": sha256, "filename": Path(file.filename or "resume").name,
                "text": text, "quality": quality, "created_at": db.now()}
    try:
        d.documents.insert_one(document)
    except DuplicateKeyError:  # the same file arrived twice at once; use the copy that won
        return d.documents.find_one({"sha256": sha256})
    if quality == "ok":
        _insert_chunks(d, document["_id"], chunks)
    return document


def _insert_chunks(d: Database, document_id: int, chunks: list[dict]) -> None:
    vectors = matching.embed([reader.embedding_text(c) for c in chunks])
    d.resume_chunks.insert_many([{**c, "document_id": document_id, "embedding": v.tolist()} for c, v in zip(chunks, vectors)])


def _rebuild_stale_chunks(d: Database) -> None:
    """Chunks built by an older chunker or embedding model are rebuilt from the stored text, then jobs rescored."""
    built_with = {"chunker": reader.CHUNKER_VERSION, "embed_model": matching.EMBED_MODEL}
    meta = d.meta.find_one({"_id": "chunks"}) or {}
    if all(meta.get(k) == v for k, v in built_with.items()):
        return
    documents = list(d.documents.find({"quality": "ok"}, {"text": 1}))
    log.info("Rebuilding chunks for %s resumes (built with %s, now %s)", len(documents),
             {k: meta.get(k) for k in built_with}, built_with)
    for document in documents:
        d.resume_chunks.delete_many({"document_id": document["_id"]})
        _insert_chunks(d, document["_id"], reader.chunk(document["text"]))
    d.meta.update_one({"_id": "chunks"}, {"$set": built_with}, upsert=True)
    for document in documents:
        probe = d.resume_chunks.find_one({"document_id": document["_id"]}, {"embedding": 1})
        if probe and not db.wait_until_searchable(d, document["_id"], probe["embedding"]):
            log.warning("Resume %s not searchable yet; its scores refresh on the next rescore", document["_id"])
    for job in d.jobs.find({"rubric_approved": True}, {"_id": 1}):
        _rescore_job(d, job["_id"])


# ---------- jobs ----------

@api.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED, tags=["jobs"])
def create_job(body: JobCreate, d: Database = Db):
    job = {"_id": db.next_id(d, "jobs"), "code": body.code, "title": body.title, "description": body.description,
           "rubric": [], "rubric_version": 0, "rubric_approved": False, "settings": body.settings.model_dump(),
           "created_at": db.now()}
    try:
        d.jobs.insert_one(job)
    except DuplicateKeyError:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Job code '{body.code}' already exists")
    return _job_out(d, job)


@api.post("/jds:extract", response_model=JobDraft, tags=["jobs"],
          summary="Read a JD file (PDF, DOCX or TXT) and draft a title, description and checklist. Nothing is saved.")
def extract_job_description(file: UploadFile):
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File exceeds 10 MB")
    try:
        text = reader.extract_text(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    except Exception:
        log.exception("Could not read JD file")
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "File could not be read")
    if len(text.strip()) < 50:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "No text found in this file. Scanned PDFs are not supported yet; paste the JD instead.")
    return jd.draft_job(text, Path(file.filename or "Untitled job").stem)


@api.get("/jobs", response_model=list[JobOut], tags=["jobs"])
def list_jobs(d: Database = Db):
    return [_job_out(d, j) for j in d.jobs.find().sort("_id", -1)]


@api.get("/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
def get_job(job_id: int, d: Database = Db):
    return _job_out(d, _get_job(d, job_id))


@api.put("/jobs/{job_id}/rubric", response_model=JobOut, tags=["jobs"],
         summary="Save edited checklist as a new draft version")
def save_rubric(job_id: int, body: RubricUpdate, d: Database = Db):
    _get_job(d, job_id)
    d.jobs.update_one({"_id": job_id}, {"$set": {"rubric": [r.model_dump() for r in body.requirements],
                                                 "rubric_approved": False}, "$inc": {"rubric_version": 1}})
    return _job_out(d, _get_job(d, job_id))


@api.post("/jobs/{job_id}/rubric:approve", response_model=JobOut, tags=["jobs"],
          summary="Lock the checklist and (re)score every application for this job")
def approve_rubric(job_id: int, d: Database = Db):
    job = _get_job(d, job_id)
    if job["rubric_version"] == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Save a rubric before approving it")
    d.jobs.update_one({"_id": job_id}, {"$set": {"rubric_approved": True}})
    _rescore_job(d, job_id)
    return _job_out(d, _get_job(d, job_id))


@api.put("/jobs/{job_id}/settings", response_model=JobOut, tags=["jobs"],
         summary="Change thresholds and weights, then rescore every application")
def update_settings(job_id: int, body: JobSettings, d: Database = Db):
    job = _get_job(d, job_id)
    d.jobs.update_one({"_id": job_id}, {"$set": {"settings": body.model_dump()}})
    if job["rubric_approved"]:
        _rescore_job(d, job_id)
    return _job_out(d, _get_job(d, job_id))


# ---------- applications ----------

@api.post("/jobs/{job_id}/resumes", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED,
          tags=["applications"], responses={200: {"description": "Same file already applied to this job"},
                                            503: {"description": "Search index still catching up; retry the upload"}},
          summary="Upload a resume (PDF, DOCX or TXT) and score it against the approved rubric")
def upload_resume(job_id: int, file: UploadFile, response: Response, d: Database = Db):
    job = _get_job(d, job_id)
    if not job["rubric_approved"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Approve the job's rubric before uploading resumes")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File exceeds 10 MB")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "File is empty")

    sha256 = hashlib.sha256(data).hexdigest()
    document = d.documents.find_one({"sha256": sha256}) or _store_document(d, file, data, sha256)
    existing = d.applications.find_one({"job_id": job_id, "document_id": document["_id"]})
    if existing:
        response.status_code = status.HTTP_200_OK
        return _application_out(existing)

    if document["quality"] == "ok":
        probe = d.resume_chunks.find_one({"document_id": document["_id"]}, {"embedding": 1})["embedding"]
        if not db.wait_until_searchable(d, document["_id"], probe):
            # Scoring now would find no evidence and give a wrongly low score; the file is stored, so a retry is quick.
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "The search index is still catching up with this resume. Upload it again in a moment.")

    application = {"_id": db.next_id(d, "applications"), "job_id": job_id, "document_id": document["_id"],
                   "filename": document["filename"], "quality": document["quality"], "status": "NEEDS_ATTENTION",
                   "band": None, "fit_score": None, "coverage": None, "semantic": None, "evidence": None,
                   "rubric_version": None, "models": None, "created_at": db.now()}
    try:
        d.applications.insert_one(application)
    except DuplicateKeyError:
        response.status_code = status.HTTP_200_OK
        return _application_out(d.applications.find_one({"job_id": job_id, "document_id": document["_id"]}))
    _score(d, application["_id"])
    return _get_application(d, application["_id"])


@api.get("/jobs/{job_id}/applications", response_model=list[ApplicationSummary], tags=["applications"],
         summary="Ranked list: fit score, then coverage, then arrival time")
def list_applications(job_id: int, band: str | None = None, limit: int = Query(50, ge=1, le=200),
                      offset: int = Query(0, ge=0), d: Database = Db):
    _get_job(d, job_id)
    query = {"job_id": job_id, **({"band": band} if band else {})}
    # MongoDB orders null below numbers, so unscored resumes fall to the bottom of a descending sort.
    cursor = (d.applications.find(query, {"evidence": 0})
              .sort([("fit_score", -1), ("coverage", -1), ("created_at", 1), ("_id", 1)]).skip(offset).limit(limit))
    return [_application_out(a) for a in cursor]


@api.get("/applications/{application_id}", response_model=ApplicationOut, tags=["applications"],
         summary="Application with per-requirement evidence and match candidates")
def get_application(application_id: int, d: Database = Db):
    return _get_application(d, application_id)


@api.get("/applications/{application_id}/text", response_model=DocumentText, tags=["applications"],
         summary="Extracted resume text; evidence start/end offsets index into it")
def get_application_text(application_id: int, d: Database = Db):
    application = _get_application(d, application_id)
    return {"text": d.documents.find_one({"_id": application["document_id"]}, {"text": 1})["text"]}


@api.post("/applications/{application_id}:rescore", response_model=ApplicationOut, tags=["applications"])
def rescore_application(application_id: int, d: Database = Db):
    application = _get_application(d, application_id)
    if not _get_job(d, application["job_id"])["rubric_approved"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Job rubric is not approved")
    _score(d, application_id)
    return _get_application(d, application_id)


@app.get("/health", tags=["ops"])
def health():
    try:
        db.database().command("ping")
    except Exception:
        log.exception("Database ping failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Database unreachable")
    return {"status": "ok", "database": "ok", "models": matching.models()}


app.include_router(api)
