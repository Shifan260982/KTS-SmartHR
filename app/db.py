"""MongoDB Atlas storage: collections, indexes, Atlas Vector Search and Atlas Search over resume chunks."""
import logging
import os
import time
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.database import Database
from pymongo.operations import SearchIndexModel

log = logging.getLogger("smarthr")
load_dotenv()  # MONGODB_URI lives in .env, never in code; real environment variables win

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ORIGINALS_DIR = DATA_DIR / "resumes" / "originals"
VECTOR_INDEX = "chunk_vector"
TEXT_INDEX = "chunk_text"
INDEX_READY_TIMEOUT_S = 180
CHUNK_VISIBLE_TIMEOUT_S = 30


@cache
def _client() -> MongoClient:
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise RuntimeError("MONGODB_URI is not set. Copy .env.example to .env and add your Atlas connection string.")
    return MongoClient(uri, appname="smarthr", serverSelectionTimeoutMS=15000, tz_aware=True)


def database() -> Database:
    return _client()[os.getenv("MONGODB_DB", "smarthr")]


def get_db() -> Database:
    """FastAPI dependency. The client pools connections, so there is nothing to close per request."""
    return database()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def next_id(db: Database, name: str) -> int:
    """Integer ids keep the API stable (jobs/1, applications/42) on top of MongoDB."""
    return db.counters.find_one_and_update({"_id": name}, {"$inc": {"seq": 1}}, upsert=True,
                                           return_document=ReturnDocument.AFTER)["seq"]


def init(dimensions: int) -> None:
    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    db = database()
    db.jobs.create_index("code", unique=True)
    db.documents.create_index("sha256", unique=True)
    db.applications.create_index([("job_id", ASCENDING), ("document_id", ASCENDING)], unique=True)
    db.applications.create_index([("job_id", ASCENDING), ("fit_score", DESCENDING)])
    db.resume_chunks.create_index("document_id")
    _ensure_search_indexes(db, dimensions)


def _ensure_search_indexes(db: Database, dimensions: int) -> None:
    if "resume_chunks" not in db.list_collection_names():
        db.create_collection("resume_chunks")  # search indexes need the collection to exist
    existing = {i["name"]: i for i in db.resume_chunks.list_search_indexes()}
    current_dims = next((f.get("numDimensions") for f in existing.get(VECTOR_INDEX, {}).get("latestDefinition", {}).get("fields", [])
                         if f.get("type") == "vector"), dimensions)
    if current_dims != dimensions:
        # A different embedding model: the old vectors are rebuilt at startup, so the index is recreated to match.
        log.warning("Vector index has %s dimensions, the model produces %s: recreating '%s'", current_dims, dimensions, VECTOR_INDEX)
        db.resume_chunks.drop_search_index(VECTOR_INDEX)
        deadline = time.monotonic() + INDEX_READY_TIMEOUT_S
        while VECTOR_INDEX in {i["name"] for i in db.resume_chunks.list_search_indexes()} and time.monotonic() < deadline:
            time.sleep(3)
        existing.pop(VECTOR_INDEX)
    wanted = [
        SearchIndexModel(name=VECTOR_INDEX, type="vectorSearch", definition={"fields": [
            {"type": "vector", "path": "embedding", "numDimensions": dimensions, "similarity": "cosine"},
            {"type": "filter", "path": "document_id"},
        ]}),
        SearchIndexModel(name=TEXT_INDEX, type="search", definition={"mappings": {"dynamic": False, "fields": {
            "text": {"type": "string", "analyzer": "lucene.standard"},
            "document_id": {"type": "number"},
        }}}),
    ]
    missing = [m for m in wanted if m.document["name"] not in existing]
    if missing:
        db.resume_chunks.create_search_indexes(missing)
        log.info("Creating Atlas search indexes: %s", [m.document["name"] for m in missing])

    deadline = time.monotonic() + INDEX_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        statuses = {i["name"]: i.get("queryable", False) for i in db.resume_chunks.list_search_indexes()}
        if statuses.get(VECTOR_INDEX) and statuses.get(TEXT_INDEX):
            return
        time.sleep(3)
    log.warning("Atlas search indexes are not queryable yet; scoring waits for them on first upload.")


def search_chunks(db: Database, document_id: int, text: str, vector: list[float], limit: int) -> tuple[list[dict], list[dict]]:
    """Best chunks of one resume for one requirement: (by meaning via Vector Search, by keywords via Atlas Search)."""
    projection = {"text": 1, "start": 1, "end": 1, "embedding": 1}
    by_meaning = list(db.resume_chunks.aggregate([
        {"$vectorSearch": {"index": VECTOR_INDEX, "path": "embedding", "queryVector": vector, "numCandidates": 100,
                           "limit": limit, "filter": {"document_id": document_id}}},
        {"$project": projection},
    ]))
    by_keyword = list(db.resume_chunks.aggregate([
        {"$search": {"index": TEXT_INDEX, "compound": {
            "must": [{"text": {"query": text, "path": "text"}}],
            "filter": [{"equals": {"path": "document_id", "value": document_id}}],
        }}},
        {"$limit": limit},
        {"$project": {**projection, "keyword": {"$meta": "searchScore"}}},
    ]))
    return by_meaning, by_keyword


def wait_until_searchable(db: Database, document_id: int, probe: list[float]) -> bool:
    """Atlas Search indexes update a few seconds after a write; scoring before that would find no evidence."""
    # shortcut: polls per upload (adds ~1-5 s each); move scoring to a background queue for bulk volume.
    deadline = time.monotonic() + CHUNK_VISIBLE_TIMEOUT_S
    while time.monotonic() < deadline:
        hits_vector = list(db.resume_chunks.aggregate([
            {"$vectorSearch": {"index": VECTOR_INDEX, "path": "embedding", "queryVector": probe, "numCandidates": 10,
                               "limit": 1, "filter": {"document_id": document_id}}}]))
        hits_text = list(db.resume_chunks.aggregate([
            {"$search": {"index": TEXT_INDEX, "equals": {"path": "document_id", "value": document_id}}}, {"$limit": 1}]))
        if hits_vector and hits_text:
            return True
        time.sleep(1)
    return False
