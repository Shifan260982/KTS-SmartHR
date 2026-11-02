"""Evaluate SmartHR ranking on company resumes, with Kaggle resumes mixed in as distractors.

Proxy labels: a resume is "positive" for a job if it sits in that job's resume folder; everything else is negative.
Loads through the running API (idempotent, safe to re-run), then scores offline from stored evidence.

    .venv/Scripts/python scripts/evaluate.py          # server must be running on :8000
"""
import csv
import hashlib
import itertools
import json
import sys
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import db, matching, reader  # noqa: E402

API = "http://localhost:8000/api/v1"
DATA = ROOT / "data" / "Resume-dataset-and-company-recieved"
JD_DIR = ROOT / "data" / "JD"
KAGGLE_CSV = DATA / "dataset-kaggle" / "Resume" / "Resume.csv"
KAGGLE_FULL_CATEGORIES = {"INFORMATION-TECHNOLOGY", "ENGINEERING"}  # closest to our roles: the hardest distractors
KAGGLE_PER_OTHER_CATEGORY = 10
GRID_MET = [0.3, 0.5, 0.7, 0.9]
GRID_PARTIAL = [0.55, 0.575, 0.6, 0.625, 0.65, 0.675, 0.7, 0.725, 0.75]


def call(method: str, path: str, body: bytes | None = None, content_type: str = "application/json") -> tuple[int, dict]:
    request = Request(API + path, body, {"Content-Type": content_type}, method=method)
    try:
        with urlopen(request, timeout=600) as response:
            return response.status, json.load(response)
    except HTTPError as e:
        return e.code, json.load(e)


def upload(job_id: int, filename: str, data: bytes) -> int:
    boundary = uuid.uuid4().hex
    safe_name = filename.replace('"', "'")
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return call("POST", f"/jobs/{job_id}/resumes", body, f"multipart/form-data; boundary={boundary}")[0]


def ensure_job(spec: dict) -> int:
    existing = {j["code"]: j for j in call("GET", "/jobs")[1]}
    if spec["code"] in existing:
        return existing[spec["code"]]["id"]
    description = reader.extract_text(spec["jd_file"], (JD_DIR / spec["jd_file"]).read_bytes())
    status, job = call("POST", "/jobs", json.dumps({"code": spec["code"], "title": spec["title"],
                                                    "description": description[:20000]}).encode())
    assert status == 201, job
    status, body = call("PUT", f"/jobs/{job['id']}/rubric", json.dumps({"requirements": spec["requirements"]}).encode())
    assert status == 200, body
    assert call("POST", f"/jobs/{job['id']}/rubric:approve")[0] == 200
    return job["id"]


def kaggle_sample() -> list[tuple[str, bytes]]:
    csv.field_size_limit(10**9)
    rows = [r for r in csv.DictReader(KAGGLE_CSV.open(encoding="utf-8")) if len(r["Resume_str"].strip()) >= reader.MIN_TEXT_CHARS]
    picked, per_category = [], Counter()
    for r in rows:
        category = r["Category"]
        if category in KAGGLE_FULL_CATEGORIES or per_category[category] < KAGGLE_PER_OTHER_CATEGORY:
            per_category[category] += 1
            picked.append((f"kaggle_{r['ID']}_{category}.txt", r["Resume_str"].encode("utf-8")))
    return picked


def load(specs: list[dict]) -> dict[str, set[str]]:
    """Upload every file to every job. Returns sha256 -> set of folders it came from (its proxy labels)."""
    files = [(f.name, f.read_bytes(), spec["resume_folder"])
             for spec in specs for f in sorted((DATA / spec["resume_folder"]).iterdir()) if f.is_file()]
    files += [(name, data, "kaggle") for name, data in kaggle_sample()]
    origin = defaultdict(set)
    for _, data, folder in files:
        origin[hashlib.sha256(data).hexdigest()].add(folder)

    job_ids = [ensure_job(spec) for spec in specs]
    statuses, started = Counter(), time.time()
    for n, ((name, data, _), job_id) in enumerate(itertools.product(files, job_ids), 1):
        statuses[upload(job_id, name, data)] += 1
        if n % 200 == 0:
            print(f"  uploaded {n}/{len(files) * len(job_ids)} ({time.time() - started:.0f}s) {dict(statuses)}", flush=True)
    print(f"Upload done: {dict(statuses)} (201 new, 200 already there, 422 unreadable file)")
    return origin


def auc(pos: list[float], neg: list[float]) -> float:
    """Probability that a random positive outranks a random negative."""
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


def precision_at_k(scored: list[tuple[float, bool]]) -> float:
    k = sum(label for _, label in scored)
    return sum(label for _, label in sorted(scored, key=lambda x: -x[0])[:k]) / k


def rescore(evidence: list[dict], settings: dict, met: float, partial: float) -> dict:
    """Mirror of matching.match_requirements' verdict rule, so thresholds can be tuned without re-running models."""
    reverdicted = [{**e, "verdict": "met" if e["match_score"] >= met
                    else "partial" if max(c["vector_similarity"] for c in e["candidates"]) >= partial
                    else "not_found"} for e in evidence]
    return matching.compute_score(reverdicted, settings)


def report(specs: list[dict], origin: dict[str, set[str]]) -> None:
    d = db.database()
    for spec in specs:
        job = d.jobs.find_one({"code": spec["code"]})
        shas = {doc["_id"]: doc["sha256"] for doc in d.documents.find({}, {"sha256": 1})}
        rows = [{**a, "sha256": shas[a["document_id"]]} for a in d.applications.find({"job_id": job["_id"]})]
        scored = [r for r in rows if r["status"] == "SCORED"]
        labels = [spec["resume_folder"] in origin.get(r["sha256"], set()) for r in scored]
        settings = job["settings"]
        n_pos = sum(labels)
        print(f"\n=== {spec['title']} ({spec['code']})")
        print(f"resumes scored: {len(scored)} | positives (from '{spec['resume_folder']}'): {n_pos} | "
              f"negatives: {len(scored) - n_pos} | unreadable (needs attention): {len(rows) - len(scored)}")

        def show(name: str, scores: list[float]) -> None:
            pairs = list(zip(scores, labels))
            print(f"  {name:<44} AUC {auc([s for s, l in pairs if l], [s for s, l in pairs if not l]):.3f}   "
                  f"precision@{n_pos} {precision_at_k(pairs):.3f}")

        # Baseline: whole JD vs whole resume, mean chunk embedding cosine.
        jd_text = reader.extract_text(spec["jd_file"], (JD_DIR / spec["jd_file"]).read_bytes())
        jd_vector = matching.embed([c["text"] for c in reader.chunk(jd_text)]).mean(axis=0)
        baseline = []
        for r in scored:
            v = np.array([c["embedding"] for c in d.resume_chunks.find({"document_id": r["document_id"]}, {"embedding": 1})],
                         dtype=np.float32).mean(axis=0)
            baseline.append(float(v @ jd_vector / (np.linalg.norm(v) * np.linalg.norm(jd_vector))))
        show("baseline: whole JD vs whole resume", baseline)
        show(f"rubric fit, current (met {settings['met_threshold']}, partial {settings['partial_similarity']})",
             [r["fit_score"] for r in scored])

        evidences = [r["evidence"] for r in scored]
        best = max(((m, p) for m in GRID_MET for p in GRID_PARTIAL),
                   key=lambda mp: auc(*[[rescore(e, settings, *mp)["fit_score"] for e, l in zip(evidences, labels) if l == want]
                                       for want in (True, False)]))
        tuned = [rescore(e, settings, *best) for e in evidences]
        show(f"rubric fit, tuned   (met {best[0]}, partial {best[1]})", [t["fit_score"] for t in tuned])

        for name, bands in (("current", [r["band"] for r in scored]), ("tuned", [t["band"] for t in tuned])):
            print(f"  bands ({name}): positives {dict(Counter(b for b, l in zip(bands, labels) if l))} | "
                  f"negatives {dict(Counter(b for b, l in zip(bands, labels) if not l))}")

        # Which kinds of negatives outrank positives (no personal data: only their source).
        top = sorted(zip([t["fit_score"] for t in tuned], labels, scored), key=lambda x: -x[0])[:n_pos]
        intruders = Counter(", ".join(sorted(origin.get(r["sha256"], {"?"}))) for _, label, r in top if not label)
        print(f"  negatives inside the top {n_pos} (tuned): {dict(intruders) or 'none'}")

        # Per-requirement verdict rates show which rubric lines separate the classes.
        print("  requirement met/partial rate, positives vs negatives (tuned):")
        for i, req in enumerate(spec["requirements"]):
            met, partial = best
            rate = lambda want: sum(
                (e[i]["match_score"] >= met or max(c["vector_similarity"] for c in e[i]["candidates"]) >= partial)
                for e, l in zip(evidences, labels) if l == want) / max(1, sum(l == want for l in labels))
            print(f"    {req['id']:<4} {req['type']:<4} {rate(True):>5.0%} vs {rate(False):>5.0%}  {req['text']}")


if __name__ == "__main__":
    specs = json.loads((JD_DIR / "jobs.json").read_text(encoding="utf-8"))["jobs"]
    report(specs, load(specs))
