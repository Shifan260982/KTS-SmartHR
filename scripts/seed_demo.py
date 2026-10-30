"""Seed a demo database with no company candidates: real JDs and rubrics, synthetic samples, anonymised Kaggle resumes.

    SMARTHR_DB_PATH=data/demo.db .venv/Scripts/python -m uvicorn app.main:app --port 8000   # in one terminal
    .venv/Scripts/python scripts/seed_demo.py
"""
import json
from pathlib import Path

from evaluate import JD_DIR, ROOT, ensure_job, kaggle_sample, upload

KAGGLE_COUNT = 30

specs = json.loads((JD_DIR / "jobs.json").read_text(encoding="utf-8"))["jobs"]
files = [(f.name, f.read_bytes()) for f in sorted((ROOT / "samples").glob("resume_*.txt"))]
files += [(name, data) for name, data in kaggle_sample() if "INFORMATION-TECHNOLOGY" in name][:KAGGLE_COUNT]
for spec in specs:
    job_id = ensure_job(spec)
    statuses = [upload(job_id, name, data) for name, data in files]
    print(f"{spec['code']}: {len(files)} uploads, statuses {sorted(set(statuses))}")
