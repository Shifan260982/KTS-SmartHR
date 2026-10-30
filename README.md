# SmartHR backend: semantic matching (no LLM)

FastAPI service for steps 3, 4, 5 and 7 of the SmartHR design: read the resume, apply the HR rubric, run hybrid semantic matching and score the result.
It has no LLM. The verdict for each requirement comes from the reranker score, and the quote is the matched resume chunk, so it always exists in the resume.

## Pipeline

1. **Read**: PDF (PyMuPDF, with white or tiny hidden text removed), DOCX or TXT is turned into text. The text is split into chunks of 60 to 400 characters, and each chunk keeps its exact `start`/`end` offsets.
2. **Find**: for each requirement, BM25 keyword search and `bge-small-en-v1.5` vector search run over the resume chunks, and the two rankings are merged with Reciprocal Rank Fusion. The top 5 chunks go forward.
3. **Re-check**: the `ms-marco-MiniLM-L-6-v2` reranker scores each (requirement, chunk) pair. A score at or above `met_threshold` (0.5) is `met`. The reranker scores near 0 for paraphrases, so when it isn't confident the chunk with the highest vector similarity is used instead: at or above `partial_similarity` (0.6) it is `partial`, and below that it is `not_found`.
4. **Score**: `Fit = 100 × (0.8 × Coverage + 0.2 × Semantic)`. If any must-have is not met, the band is `must_have_missing`. Otherwise it is `strong_fit`, `possible_fit` or `low_match`.

Both models run locally on CPU through ONNX (fastembed). They are downloaded on the first start (about 150 MB). The design's larger models (`BAAI/bge-base-en-v1.5`, `BAAI/bge-reranker-base`, about 1.3 GB) can be switched in with the `SMARTHR_EMBED_MODEL` and `SMARTHR_RERANK_MODEL` environment variables. A different model may need different `met_threshold` and `partial_similarity` values. The current values were set from 3 sample resumes only and still need tuning on real labelled resumes.

## Run

```bash
py -3.14 -m venv .venv
```
```bash
.venv/Scripts/python -m pip install -r requirements.txt
```
```bash
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

- Swagger UI: http://localhost:8000/docs
- For Postman: Import → Link → `http://localhost:8000/openapi.json`
- Tests: `.venv/Scripts/python -m pytest -q`

## HR portal (frontend)

React + TypeScript + Vite in `frontend/`. Vite proxies `/api` to the API on port 8000, so start the API first.

```bash
npm --prefix frontend install
```
```bash
npm --prefix frontend run dev
```

Open http://localhost:5173. Screens: the jobs rail, a ranked shortlist with a marks grid per requirement (filled square = met, hatched = partial, empty = not found, red outline = must-have), the candidate review with resume lines underlined and marked in the margin, upload, checklist editor and thresholds. Press J and K to move through the list.

After changing the API, regenerate the TypeScript types while the API is running: `npm --prefix frontend run gen:api`.

**Demo data without real candidates:** run the API with `SMARTHR_DB_PATH=data/demo.db`, then `.venv/Scripts/python scripts/seed_demo.py`. This loads the two real JDs, the synthetic samples and anonymised Kaggle resumes. Use it for screenshots and demos.

## Try it (the order matters)

1. `POST /api/v1/jobs` with the `job` object from `samples/job.json`
2. `PUT /api/v1/jobs/{id}/rubric` with the `rubric` object
3. `POST /api/v1/jobs/{id}/rubric:approve`
4. `POST /api/v1/jobs/{id}/resumes` as form-data with key `file`, once for each file in `samples/`
5. `GET /api/v1/jobs/{id}/applications` returns the ranked list. `GET /api/v1/applications/{id}` returns the evidence and the top 5 candidate chunks for each requirement.
6. Tune thresholds with `PUT /api/v1/jobs/{id}/settings`. This rescores every application for the job.

## Not built yet (from the design)

LLM verify and quote check, Graph mail ingestion, ClamAV, OCR, years of experience calculated from dates, MongoDB Atlas (SQLite is used for now), auth and roles, HR decisions, outbox and audit log.
