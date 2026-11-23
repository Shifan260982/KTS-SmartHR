import hashlib
import logging
import os
import re
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Response, UploadFile, status
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app import db, graph, jd, llm, matching, reader
from app.schemas import (EMAIL_PATTERN, ApplicationOut, ApplicationSummary, DocumentText, EmailIn, InterviewIn, JobCreate,
                         JobDraft, JobOut, JobSettings, MailboxCheck, MailboxStatus, OutreachSettings, RubricUpdate, Slot)

log = logging.getLogger("smarthr")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@asynccontextmanager
async def lifespan(_: FastAPI):
    matching.warm_up()
    db.init(matching.dimensions())
    _rebuild_stale_chunks(db.database())
    stop = threading.Event()
    if graph.configured() and INGEST_MINUTES > 0:
        threading.Thread(target=_ingest_forever, args=(stop,), name="mail-ingestor", daemon=True).start()
        log.info("Checking %s for resumes every %s minutes", graph.MAILBOX, INGEST_MINUTES)
    yield
    stop.set()


app = FastAPI(
    title="SmartHR API",
    version="0.2.0",
    description="Resume shortlisting: MongoDB Atlas Vector Search + Atlas Search (BM25) find the evidence lines, and a "
                "local LLM (Ollama) reads the whole resume to judge each requirement. Quotes are checked against the resume. "
                "Resumes also arrive by email to SmartHR's mailbox, which sends the shortlisted emails and Teams invites.",
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
    if not application.get("candidate_email"):
        document = d.documents.find_one({"_id": application["document_id"]}, {"text": 1}) or {}
        application["candidate_email"] = graph.find_email(document.get("text", ""))
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
    lines = list(d.resume_chunks.find({"document_id": application["document_id"]},
                                      {"text": 1, "section": 1, "start": 1, "end": 1}).sort("start", 1))
    evidence, judge = llm.review(evidence, lines)
    score = matching.compute_score(evidence, settings)
    d.applications.update_one({"_id": application_id}, {"$set": {
        "status": "SCORED", **score, "evidence": evidence, "rubric_version": job["rubric_version"],
        "models": {**matching.models(), "judge": judge or "search (LLM unavailable)"}}})
    log.info("Scored application %s: %s (%s)", application_id, score["fit_score"], score["band"])


def _search(d: Database, document_id: int, text: str, vector: list[float]):
    return db.search_chunks(d, document_id, text, vector, matching.CANDIDATES_PER_REQUIREMENT)


def _rescore_job(d: Database, job_id: int) -> None:
    for application in d.applications.find({"job_id": job_id}, {"_id": 1}):
        _score(d, application["_id"])


def _store_document(d: Database, filename: str, data: bytes, sha256: str) -> dict:
    try:
        text = reader.extract_text(filename, data)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    except Exception:
        log.exception("Could not read %s", sha256)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "File could not be read")

    chunks = reader.chunk(text)
    quality = reader.quality(text, chunks)
    # Named by fingerprint so a candidate's filename can never touch the folder layout.
    (db.ORIGINALS_DIR / f"{sha256}{Path(filename).suffix.lower()}").write_bytes(data)
    document = {"_id": db.next_id(d, "documents"), "sha256": sha256, "filename": Path(filename or "resume").name,
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
    application, created = _apply(d, job_id, file.filename or "", file.file.read(MAX_UPLOAD_BYTES + 1))
    if not created:
        response.status_code = status.HTTP_200_OK
    return application


def _apply(d: Database, job_id: int, filename: str, data: bytes, details: dict | None = None) -> tuple[dict, bool]:
    """Store, read and score one resume for one job (upload or email). Returns (application, newly created)."""
    job = _get_job(d, job_id)
    if not job["rubric_approved"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Approve the job's rubric before uploading resumes")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File exceeds 10 MB")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "File is empty")

    sha256 = hashlib.sha256(data).hexdigest()
    document = d.documents.find_one({"sha256": sha256}) or _store_document(d, filename, data, sha256)
    existing = d.applications.find_one({"job_id": job_id, "document_id": document["_id"]})
    if existing:
        return _application_out(existing), False

    if document["quality"] == "ok":
        probe = d.resume_chunks.find_one({"document_id": document["_id"]}, {"embedding": 1})["embedding"]
        if not db.wait_until_searchable(d, document["_id"], probe):
            # Scoring now would find no evidence and give a wrongly low score; the file is stored, so a retry is quick.
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "The search index is still catching up with this resume. Upload it again in a moment.")

    application = {"_id": db.next_id(d, "applications"), "job_id": job_id, "document_id": document["_id"],
                   "filename": document["filename"], "quality": document["quality"], "status": "NEEDS_ATTENTION",
                   "band": None, "fit_score": None, "coverage": None, "semantic": None, "evidence": None,
                   "rubric_version": None, "models": None, "created_at": db.now(),
                   # from the website's application email: candidate_email, candidate_name and the form fields
                   **{k: v for k, v in (details or {}).items() if v}}
    try:
        d.applications.insert_one(application)
    except DuplicateKeyError:
        return _application_out(d.applications.find_one({"job_id": job_id, "document_id": document["_id"]})), False
    _score(d, application["_id"])
    return _get_application(d, application["_id"]), True


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


@api.delete("/jobs/{job_id}/applications", status_code=status.HTTP_204_NO_CONTENT, tags=["applications"],
            summary="TEMPORARY: delete every application for this job, plus resumes no other job uses")
def delete_applications(job_id: int, d: Database = Db):
    # shortcut: hard delete with no auth or audit trail, for re-testing models; remove before production.
    _get_job(d, job_id)
    document_ids = d.applications.distinct("document_id", {"job_id": job_id})
    d.applications.delete_many({"job_id": job_id})
    still_used = set(d.applications.distinct("document_id", {"document_id": {"$in": document_ids}}))
    for document in d.documents.find({"_id": {"$in": [i for i in document_ids if i not in still_used]}}, {"sha256": 1}):
        d.resume_chunks.delete_many({"document_id": document["_id"]})
        d.documents.delete_one({"_id": document["_id"]})
        for original in db.ORIGINALS_DIR.glob(f"{document['sha256']}*"):
            original.unlink(missing_ok=True)
    log.info("Deleted applications for job %s (%d resumes)", job_id, len(document_ids))


# ---------- outreach and mailbox (SmartHR's own mailbox via Microsoft Graph, app-only) ----------

SEND_LOCK_SECONDS = 120


def _graph_failed(error: graph.GraphError) -> HTTPException:
    if error.status == 503:
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error))
    if error.status in (401, 403):
        return HTTPException(status.HTTP_403_FORBIDDEN,
                             f"Microsoft 365 refused this: {error}. Check the mailbox permissions (docs/SmartHR_Email_Setup.pdf).")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, f"Outlook could not do this: {error}")


def _send_once(d: Database, application_id: int, kind: str, send) -> dict:
    """Run send(pending, remember) at most once per application and kind, even on a double click or a crash mid-send.

    pending is a Graph id saved before the irreversible step (the draft id, or the event's transactionId); a retry
    after a crash reuses it, so Microsoft 365 never sends twice. The result is recorded in outreach.<kind>.
    """
    _get_application(d, application_id)
    stale = (datetime.now(UTC) - timedelta(seconds=SEND_LOCK_SECONDS)).isoformat(timespec="seconds")
    claimed = d.applications.find_one_and_update(
        {"_id": application_id,
         # a test-mode send never blocks the real one later
         "$and": [{"$or": [{f"outreach.{kind}": {"$exists": False}}, {f"outreach.{kind}.test": True}]},
                  {"$or": [{f"sending.{kind}": {"$exists": False}}, {f"sending.{kind}": {"$lt": stale}}]}]},
        {"$set": {f"sending.{kind}": db.now()}}, projection={"pending": 1})
    if claimed is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This was already sent (or is being sent) for this candidate")

    def remember(pending_id: str) -> None:
        d.applications.update_one({"_id": application_id}, {"$set": {f"pending.{kind}": pending_id}})

    try:
        record = send((claimed.get("pending") or {}).get(kind), remember)
    except graph.GraphError as error:
        raise _graph_failed(error) from error
    finally:
        d.applications.update_one({"_id": application_id}, {"$unset": {f"sending.{kind}": ""}})
    d.applications.update_one({"_id": application_id}, {
        "$set": {f"outreach.{kind}": {**record, "sent_at": db.now(), "test": graph.REDIRECT_TO is not None,
                                      "delivered_to": graph.REDIRECT_TO or record["to"]},
                 "candidate_email": record["to"]},
        "$unset": {f"pending.{kind}": ""}})
    log.info("Application %s: %s sent%s", application_id, kind, " (test mode, redirected)" if graph.REDIRECT_TO else "")
    return _get_application(d, application_id)


@api.get("/outreach/settings", response_model=OutreachSettings, tags=["outreach"],
         summary="The SmartHR mailbox (null when not set up) and test mode (redirect_to)")
def outreach_settings():
    return {"mailbox": graph.MAILBOX if graph.configured() else None, "redirect_to": graph.REDIRECT_TO}


@api.get("/calendar/free-slots", response_model=list[Slot], tags=["outreach"],
         summary="Next free slots in the interviewer's Outlook calendar (working hours, coming week)")
def free_slots(interviewer: str = Query(pattern=EMAIL_PATTERN, max_length=254), minutes: int = Query(30, ge=15, le=240)):
    try:
        return graph.free_slots(interviewer, minutes)
    except graph.GraphError as error:
        raise _graph_failed(error) from error


@api.post("/applications/{application_id}/emails:shortlisted", response_model=ApplicationOut, tags=["outreach"],
          summary="Send the shortlisted email from the SmartHR mailbox (once per application)")
def send_shortlisted(application_id: int, body: EmailIn, d: Database = Db):
    def send(draft_id, remember):
        if not (draft_id and graph.was_sent(draft_id)):  # a crash after sending must not send again
            draft_id = graph.create_draft(body.to, body.subject, body.body)
            remember(draft_id)
            graph.send_draft(draft_id)
        return {"to": body.to}
    return _send_once(d, application_id, "shortlisted", send)


@api.post("/applications/{application_id}/interviews", response_model=ApplicationOut, tags=["outreach"],
          summary="Book the first-round interview as a Teams meeting from the SmartHR mailbox; invites candidate and interviewer")
def schedule_interview(application_id: int, body: InterviewIn, d: Database = Db):
    def send(transaction_id, remember):
        if not transaction_id:
            transaction_id = f"smarthr-{application_id}-{uuid.uuid4()}"
            remember(transaction_id)
        meeting = graph.create_interview(body.to, body.interviewer, body.subject, body.body, body.start, body.end,
                                         transaction_id)
        # ISO strings keep the offset; pymongo would hand datetimes back naive and the portal would read them as local
        return {"to": body.to, "interviewer": body.interviewer, "start": body.start.isoformat(),
                "end": body.end.isoformat(), **meeting}
    return _send_once(d, application_id, "interview", send)


JOB_CODE = re.compile(r"\[([A-Za-z0-9-]{2,30})\]")  # design: the job code in the subject, e.g. "[JOB-1023] Application"
PROCESSED_FOLDER, ATTENTION_FOLDER = "SmartHR Processed", "SmartHR Needs attention"
INGEST_MINUTES = float(os.getenv("SMARTHR_INGEST_MINUTES", "2"))


def _poll_mailbox(d: Database) -> dict:
    """Design steps 1-2: turn each inbox email's resume attachments into applications, then move the email out of the
    inbox (Processed, or Needs attention when nothing could be used) so it is never read twice."""
    counts = {"emails": 0, "applications": 0, "needs_attention": 0, "retry_later": 0}
    for message in graph.inbox():
        counts["emails"] += 1
        code = JOB_CODE.search(message.get("subject") or "")
        job = d.jobs.find_one({"code": code.group(1)}) if code else None
        details = graph.applicant(message)
        ready = job is not None and job["rubric_approved"] and message.get("hasAttachments")
        files = graph.resume_attachments(message["id"]) if ready else []
        used, retry = False, False
        for name, data in files:
            try:
                _, created = _apply(d, job["_id"], name, data, details)
                used = True
                counts["applications"] += created
            except HTTPException as error:
                retry |= error.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
                log.warning("Emailed resume %r for job %s not used: %s", name, job["code"], error.detail)
        if retry:
            counts["retry_later"] += 1  # stays in the inbox; the stored file makes the next try quick
            continue
        if not used:
            counts["needs_attention"] += 1
            log.info("Email %r needs attention (job code %s, %d resume files)", message.get("subject"),
                     code.group(1) if code else "missing", len(files))
        graph.move(message["id"], PROCESSED_FOLDER if used else ATTENTION_FOLDER)
    return counts


def _ingest_forever(stop: threading.Event) -> None:
    # shortcut: one in-process poller, fine for one API instance; the design's separate locked worker is for scale-out.
    while not stop.wait(INGEST_MINUTES * 60):
        try:
            counts = _poll_mailbox(db.database())
            if counts["emails"]:
                log.info("Mailbox check: %s", counts)
        except Exception:
            log.exception("Mailbox check failed; trying again next time")


@api.get("/mailbox", response_model=MailboxStatus, tags=["mailbox"],
         summary="Whether SmartHR can sign in and reach its mailbox")
def mailbox_status():
    if not graph.configured():
        return {"mailbox": None, "connected": False, "detail": "Not set up: see docs/SmartHR_Email_Setup.pdf",
                "check_every_minutes": None}
    try:
        return {"mailbox": graph.check(), "connected": True, "detail": None,
                "check_every_minutes": INGEST_MINUTES or None}
    except graph.GraphError as error:
        return {"mailbox": graph.MAILBOX, "connected": False, "detail": str(error), "check_every_minutes": None}


@api.post("/mailbox:check", response_model=MailboxCheck, tags=["mailbox"],
          summary="Read the inbox now instead of waiting for the next scheduled check")
def check_mailbox(d: Database = Db):
    try:
        return _poll_mailbox(d)
    except graph.GraphError as error:
        raise _graph_failed(error) from error


@app.get("/health", tags=["ops"])
def health():
    try:
        db.database().command("ping")
    except Exception:
        log.exception("Database ping failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Database unreachable")
    return {"status": "ok", "database": "ok", "models": {**matching.models(), "judge": llm.LLM_MODEL}}


app.include_router(api)


class _Portal(StaticFiles):
    """Serves the built React portal; unknown paths fall back to index.html so client-side routes survive a reload."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code != status.HTTP_404_NOT_FOUND:
                raise
            return await super().get_response("index.html", scope)


PORTAL_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if PORTAL_DIR.is_dir():  # packaged app: one port, no Vite. Mounted last so /api, /docs and /health win.
    app.mount("/", _Portal(directory=PORTAL_DIR, html=True), name="portal")
