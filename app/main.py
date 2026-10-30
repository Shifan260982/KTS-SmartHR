import hashlib
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Response, UploadFile, status

from app import db, jd, matching, reader
from app.schemas import (ApplicationOut, ApplicationSummary, DocumentText, JobCreate, JobDraft, JobOut, JobSettings,
                         RubricUpdate)

log = logging.getLogger("smarthr")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    matching.warm_up()
    yield


app = FastAPI(
    title="SmartHR API",
    version="0.1.0",
    description="Resume shortlisting by semantic matching (hybrid BM25 + embedding vectors, cross-encoder reranker). No LLM stage yet.",
    lifespan=lifespan,
)
api = APIRouter(prefix="/api/v1")
Conn = Depends(db.get_conn)


# ---------- helpers ----------

def _job_out(row: sqlite3.Row) -> dict:
    return {**dict(row), "rubric": json.loads(row["rubric"]), "rubric_approved": bool(row["rubric_approved"]),
            "settings": json.loads(row["settings"])}


JOB_SELECT = """
    SELECT j.*,
        (SELECT COUNT(*) FROM applications a WHERE a.job_id = j.id) AS application_count,
        (SELECT COUNT(*) FROM applications a WHERE a.job_id = j.id AND a.status = 'NEEDS_ATTENTION') AS attention_count
    FROM jobs j
"""


def _get_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row:
    row = conn.execute(JOB_SELECT + " WHERE j.id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Job {job_id} not found")
    return row


APPLICATION_SELECT = """
    SELECT a.*, d.filename, d.quality FROM applications a JOIN documents d ON d.id = a.document_id
"""


def _application_out(row: sqlite3.Row) -> dict:
    evidence = json.loads(row["evidence"]) if row["evidence"] else None
    return {**dict(row), "evidence": evidence,
            "marks": [{"requirement_id": e["requirement_id"], "verdict": e["verdict"]} for e in evidence] if evidence else None,
            "models": json.loads(row["models"]) if row["models"] else None}


def _get_application(conn: sqlite3.Connection, application_id: int) -> dict:
    row = conn.execute(APPLICATION_SELECT + " WHERE a.id = ?", (application_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Application {application_id} not found")
    return _application_out(row)


def _score(conn: sqlite3.Connection, application_id: int) -> None:
    """Score one application against its job's approved rubric and persist the result."""
    app_row = conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
    job = _get_job(conn, app_row["job_id"])
    doc = conn.execute("SELECT * FROM documents WHERE id = ?", (app_row["document_id"],)).fetchone()

    if doc["quality"] != "ok":
        # An unreadable resume gets no score, never a low score.
        conn.execute("UPDATE applications SET status='NEEDS_ATTENTION', band=NULL, fit_score=NULL, coverage=NULL, "
                     "semantic=NULL, evidence=NULL, rubric_version=?, models=NULL WHERE id=?",
                     (job["rubric_version"], application_id))
        return

    chunks = json.loads(doc["chunks"])
    vectors = np.frombuffer(doc["embeddings"], dtype=np.float32).reshape(len(chunks), -1)
    settings = json.loads(job["settings"])
    evidence = matching.match_requirements(json.loads(job["rubric"]), chunks, vectors, settings)
    score = matching.compute_score(evidence, settings)
    conn.execute(
        "UPDATE applications SET status='SCORED', band=?, fit_score=?, coverage=?, semantic=?, evidence=?, "
        "rubric_version=?, models=? WHERE id=?",
        (score["band"], score["fit_score"], score["coverage"], score["semantic"], json.dumps(evidence),
         job["rubric_version"], json.dumps(matching.models()), application_id),
    )
    log.info("Scored application %s: %s (%s)", application_id, score["fit_score"], score["band"])


def _rescore_job(conn: sqlite3.Connection, job_id: int) -> None:
    for (application_id,) in conn.execute("SELECT id FROM applications WHERE job_id = ?", (job_id,)).fetchall():
        _score(conn, application_id)


def _store_document(conn: sqlite3.Connection, file: UploadFile, data: bytes, sha256: str) -> int:
    try:
        text = reader.extract_text(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    except Exception:
        log.exception("Could not read %s", sha256)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "File could not be read")

    chunks = reader.chunk(text)
    quality = reader.quality(text, chunks)
    vectors = matching.embed([c["text"] for c in chunks]).tobytes() if quality == "ok" else None
    # Named by fingerprint so a candidate's filename can never touch the folder layout.
    (db.ORIGINALS_DIR / f"{sha256}{Path(file.filename or '').suffix.lower()}").write_bytes(data)
    cursor = conn.execute(
        "INSERT INTO documents(sha256, filename, text, chunks, embeddings, quality) VALUES (?, ?, ?, ?, ?, ?)",
        (sha256, Path(file.filename or "resume").name, text, json.dumps(chunks), vectors, quality),
    )
    return cursor.lastrowid


# ---------- jobs ----------

@api.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED, tags=["jobs"])
def create_job(body: JobCreate, conn: sqlite3.Connection = Conn):
    try:
        with conn:
            cursor = conn.execute("INSERT INTO jobs(code, title, description, settings) VALUES (?, ?, ?, ?)",
                                  (body.code, body.title, body.description, body.settings.model_dump_json()))
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Job code '{body.code}' already exists")
    return _job_out(_get_job(conn, cursor.lastrowid))


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
def list_jobs(conn: sqlite3.Connection = Conn):
    return [_job_out(r) for r in conn.execute(JOB_SELECT + " ORDER BY j.id DESC")]


@api.get("/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
def get_job(job_id: int, conn: sqlite3.Connection = Conn):
    return _job_out(_get_job(conn, job_id))


@api.put("/jobs/{job_id}/rubric", response_model=JobOut, tags=["jobs"],
         summary="Save edited checklist as a new draft version")
def save_rubric(job_id: int, body: RubricUpdate, conn: sqlite3.Connection = Conn):
    _get_job(conn, job_id)
    with conn:
        conn.execute("UPDATE jobs SET rubric = ?, rubric_version = rubric_version + 1, rubric_approved = 0 WHERE id = ?",
                     (json.dumps([r.model_dump() for r in body.requirements]), job_id))
    return _job_out(_get_job(conn, job_id))


@api.post("/jobs/{job_id}/rubric:approve", response_model=JobOut, tags=["jobs"],
          summary="Lock the checklist and (re)score every application for this job")
def approve_rubric(job_id: int, conn: sqlite3.Connection = Conn):
    job = _get_job(conn, job_id)
    if job["rubric_version"] == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Save a rubric before approving it")
    with conn:
        conn.execute("UPDATE jobs SET rubric_approved = 1 WHERE id = ?", (job_id,))
        _rescore_job(conn, job_id)
    return _job_out(_get_job(conn, job_id))


@api.put("/jobs/{job_id}/settings", response_model=JobOut, tags=["jobs"],
         summary="Change thresholds and weights, then rescore every application")
def update_settings(job_id: int, body: JobSettings, conn: sqlite3.Connection = Conn):
    job = _get_job(conn, job_id)
    with conn:
        conn.execute("UPDATE jobs SET settings = ? WHERE id = ?", (body.model_dump_json(), job_id))
        if job["rubric_approved"]:
            _rescore_job(conn, job_id)
    return _job_out(_get_job(conn, job_id))


# ---------- applications ----------

@api.post("/jobs/{job_id}/resumes", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED,
          tags=["applications"], responses={200: {"description": "Same file already applied to this job"}},
          summary="Upload a resume (PDF, DOCX or TXT) and score it against the approved rubric")
def upload_resume(job_id: int, file: UploadFile, response: Response, conn: sqlite3.Connection = Conn):
    job = _get_job(conn, job_id)
    if not job["rubric_approved"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Approve the job's rubric before uploading resumes")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File exceeds 10 MB")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "File is empty")

    sha256 = hashlib.sha256(data).hexdigest()
    with conn:
        doc = conn.execute("SELECT id FROM documents WHERE sha256 = ?", (sha256,)).fetchone()
        document_id = doc["id"] if doc else _store_document(conn, file, data, sha256)
        existing = conn.execute("SELECT id FROM applications WHERE job_id = ? AND document_id = ?",
                                (job_id, document_id)).fetchone()
        if existing:
            response.status_code = status.HTTP_200_OK
            return _get_application(conn, existing["id"])
        application_id = conn.execute("INSERT INTO applications(job_id, document_id, status) VALUES (?, ?, 'NEEDS_ATTENTION')",
                                      (job_id, document_id)).lastrowid
        _score(conn, application_id)
    return _get_application(conn, application_id)


@api.get("/jobs/{job_id}/applications", response_model=list[ApplicationSummary], tags=["applications"],
         summary="Ranked list: fit score, then coverage, then arrival time")
def list_applications(job_id: int, band: str | None = None, limit: int = Query(50, ge=1, le=200),
                      offset: int = Query(0, ge=0), conn: sqlite3.Connection = Conn):
    _get_job(conn, job_id)
    sql = APPLICATION_SELECT + " WHERE a.job_id = ?" + (" AND a.band = ?" if band else "")
    sql += " ORDER BY a.fit_score DESC NULLS LAST, a.coverage DESC, a.created_at, a.id LIMIT ? OFFSET ?"
    params = (job_id, band, limit, offset) if band else (job_id, limit, offset)
    return [_application_out(r) for r in conn.execute(sql, params)]


@api.get("/applications/{application_id}", response_model=ApplicationOut, tags=["applications"],
         summary="Application with per-requirement evidence and match candidates")
def get_application(application_id: int, conn: sqlite3.Connection = Conn):
    return _get_application(conn, application_id)


@api.get("/applications/{application_id}/text", response_model=DocumentText, tags=["applications"],
         summary="Extracted resume text; evidence start/end offsets index into it")
def get_application_text(application_id: int, conn: sqlite3.Connection = Conn):
    row = conn.execute("SELECT d.text FROM applications a JOIN documents d ON d.id = a.document_id WHERE a.id = ?",
                       (application_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Application {application_id} not found")
    return {"text": row["text"]}


@api.post("/applications/{application_id}:rescore", response_model=ApplicationOut, tags=["applications"])
def rescore_application(application_id: int, conn: sqlite3.Connection = Conn):
    application = _get_application(conn, application_id)
    if not _get_job(conn, application["job_id"])["rubric_approved"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Job rubric is not approved")
    with conn:
        _score(conn, application_id)
    return _get_application(conn, application_id)


@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok", "models": matching.models()}


app.include_router(api)
