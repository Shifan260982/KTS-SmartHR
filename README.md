# SmartHR backend: semantic search + local LLM judge

FastAPI service for steps 3 to 7 of the SmartHR design: read the resume, apply the HR rubric, find evidence with hybrid semantic search, let a local LLM judge each requirement, and score the result.
The LLM runs on this machine through Ollama, so resumes never leave it. It cites a numbered resume line, so every quote is real resume text.

## Pipeline

1. **Read**: PDF (PyMuPDF, with white or tiny hidden text removed), DOCX or TXT is turned into text. The chunker follows the resume's structure. Each chunk is one unit of at most 400 characters (a bullet, a `Label:` line, a job/project/education entry or a group of sentences), and it keeps its exact `start`/`end` offsets. A chunk is embedded together with its section, for example `Education: BTech, …`.
2. **Find**: for each requirement, **Atlas Vector Search** (`bge-small-en-v1.5` embeddings) and **Atlas Search** (BM25 keywords) run over that resume's chunks in MongoDB, and the two rankings are merged with Reciprocal Rank Fusion. The top 5 chunks go forward.
3. **Re-check (fallback)**: compound requirements are split into parts, and an NLI entailment model (`nli-deberta-v3-small`) and the `ms-marco-MiniLM-L-6-v2` reranker scores each (requirement, chunk) pair. A score at or above `met_threshold` (0.5) is `met`. The reranker undervalues paraphrases and skill lists, so meaning similarity also counts: at or above `met_similarity` (0.72) it is `met`, at or above `partial_similarity` (0.66) it is `partial`, and below that it is `not_found`. These defaults were calibrated offline on 177 company resumes with `scripts/calibrate.py`. These verdicts are used only when the LLM is unavailable.
4. **Judge (LLM)**: `qwen3:4b` in Ollama judges each requirement, and each part of a compound requirement ("Python programming" and "NumPy, Pandas or TensorFlow") separately. Each call gets the whole resume (every line, numbered and with its section) and then the one part, so evidence that search ranked low is not missed. The resume comes first, so Ollama reuses that prompt across one resume's calls. It answers met / partial / not found, the number of the line that proves it, and a reason of up to 20 words (temperature 0, JSON schema output). A requirement is `met` only when every part is. The cited line becomes the quote, so a quote is always real resume text. A `met` that cites no valid line drops to `partial`. The prompt tells the model that a specific instance proves a general requirement (any AI topic counts as AI skill, B.Tech counts as a bachelor's degree) and that a shared word is not a match ("Programming Languages" is not communication). If Ollama is down or answers badly, the search verdicts from step 3 stand for that resume and `models.judge` says so.
5. **Score**: `Fit = 100 × (0.8 × Coverage + 0.2 × Semantic)`. If any must-have is not met, the band is `must_have_missing`. Otherwise it is `strong_fit`, `possible_fit` or `low_match`.

Both models run locally on CPU through ONNX (fastembed). They are downloaded on the first start (about 150 MB). The design's larger models (`BAAI/bge-base-en-v1.5`, `BAAI/bge-reranker-base`, about 1.3 GB) can be switched in with the `SMARTHR_EMBED_MODEL` and `SMARTHR_RERANK_MODEL` environment variables. A different model needs re-calibrated thresholds. In offline tests on company resumes, the larger models were not better.

**LLM limits** (tested on 2 AI intern and 2 .NET resumes against the AI checklist; all four came out right):
- **Speed:** about 2 minutes per resume on CPU (one call of 10–20 s per requirement part). An upload waits that long, and rescoring a job takes that long for every resume.
- **Prompt-sensitive:** a 4B model swings noticeably with small prompt changes, so re-test on real resumes after any prompt edit.
- **Judgement calls vary:** for example, whether a CGPA counts as "strong academic record".

Use the LLM settings below to change the model (a larger one such as `qwen3:8b` is stricter but slower), or run Ollama on a GPU machine.

| Variable | Default |
|---|---|
| `SMARTHR_OLLAMA_URL` | `http://localhost:11434` |
| `SMARTHR_LLM_MODEL` | `qwen3:4b` |

**Re-chunking on start:** if `CHUNKER_VERSION` or the embedding model changes, the API re-chunks every stored resume from its saved text on the next start, then rescores approved jobs. This start takes longer.

## Storage: MongoDB Atlas

Collections: `jobs`, `documents` (extracted text), `resume_chunks` (chunk text, offsets and embedding), `applications` (score, band and evidence) and `counters` (integer ids). On start the API creates the normal indexes and two Atlas search indexes on `resume_chunks`: `chunk_vector` (vectorSearch, cosine, filtered by `document_id`) and `chunk_text` (Atlas Search, `lucene.standard`). The free M0 tier supports both. Original files stay on this server in `data/resumes/originals/`.

1. Create a cluster on cloud.mongodb.com, plus a database user, and allow this machine's IP under Network Access.
2. Copy `.env.example` to `.env` and put the connection string in `MONGODB_URI`. `.env` is git-ignored; never commit it or paste it into chat.

Atlas search indexes update a few seconds after a write. An upload therefore waits until the new resume's chunks are searchable before scoring it. It answers 503 if that takes more than 30 seconds, and re-uploading the same file then continues.

## Run

```bash
py -3.14 -m venv .venv
```
```bash
.venv/Scripts/python -m pip install -r requirements.txt
```
Install Ollama (ollama.com), then download the model (about 2.5 GB):

```bash
ollama pull qwen3:4b
```
```bash
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

- Swagger UI: http://localhost:8000/docs
- For Postman: Import → Link → `http://localhost:8000/openapi.json`
- Tests: `.venv/Scripts/python -m pytest -q`
- Calibration (offline, needs the company resumes in `data/`): `.venv/Scripts/python scripts/calibrate.py collect BAAI/bge-small-en-v1.5 Xenova/ms-marco-MiniLM-L-6-v2`, then `scripts/calibrate.py report`

## HR portal (frontend)

React + TypeScript + Vite in `frontend/`. Vite proxies `/api` to the API on port 8000, so start the API first.

```bash
npm --prefix frontend install
```
```bash
npm --prefix frontend run dev
```

Open http://localhost:5173. Screens:
- **Jobs rail.**
- **Shortlist:** candidate name, band and fit score. Click a candidate to open the reasons, which show met and partial requirements with the quoted resume line, the requirements not found, and a must-have summary.
- **Upload.**
- **New job:** upload a JD as PDF or DOCX to draft the title, description and checklist, or enter them manually.
- **Checklist editor and thresholds.**

After changing the API, regenerate the TypeScript types while the API is running: `npm --prefix frontend run gen:api`.

**Demo data without real candidates:** run the API with `MONGODB_DB=smarthr_demo` (the `smarthr-api-demo` launch config), then `.venv/Scripts/python scripts/seed_demo.py`. This loads the two real JDs, the synthetic samples and anonymised Kaggle resumes. Use it for screenshots and demos.

## Try it (the order matters)

1. `POST /api/v1/jobs` with the `job` object from `samples/job.json`
2. `PUT /api/v1/jobs/{id}/rubric` with the `rubric` object
3. `POST /api/v1/jobs/{id}/rubric:approve`
4. `POST /api/v1/jobs/{id}/resumes` as form-data with key `file`, once for each file in `samples/`
5. `GET /api/v1/jobs/{id}/applications` returns the ranked list. `GET /api/v1/applications/{id}` returns the evidence and the top 5 candidate chunks for each requirement.
6. Tune thresholds with `PUT /api/v1/jobs/{id}/settings`. This rescores every application for the job.

## Mailbox: receive resumes, send emails, schedule interviews

SmartHR works through one Microsoft 365 mailbox, `noreply@kiaratech.ai` (set by `SMARTHR_MAILBOX`). It signs in as its own app with a certificate, and Exchange limits that app to this one mailbox. **This needs a one-time admin setup:** see [docs/SmartHR_Email_Setup.pdf](docs/SmartHR_Email_Setup.pdf) for who does what and exactly which access is needed. To change the guide, edit `scripts/build_mailbox_guide.py` and run it.

- **Receive:** every 2 minutes (`SMARTHR_INGEST_MINUTES`), SmartHR reads the inbox.
  - An email with `[JOB-CODE]` in the subject and a PDF, DOCX or TXT attached becomes an application, scored like an upload.
  - Applications come from the careers website's form, sent by the website from `noreply@` to `noreply@`. So the candidate's email is taken from the Reply-To, or else the form's `Email:` line, and never from the sender. The name comes from `First name`/`Last name`.
  - Every `Label: value` line of the form is kept, whatever that job's form asked, and shown to HR above the reasons.
  - The email format the website must send, and the two separate apps (website: send only; SmartHR: read/write, send, calendar), are in the setup PDF.
  - Handled emails move to *SmartHR Processed*. Unusable ones move to *SmartHR Needs attention*: no code, an unapproved job or no resume.
  - `POST /api/v1/mailbox:check` reads the inbox right away, and `GET /api/v1/mailbox` shows whether the connection works.
- **Contact candidate** (under each candidate's reasons; HR reviews and clicks send, nothing goes out on its own):
  - **Shortlisted email:** an editable draft. Following the design, SmartHR creates a draft in the mailbox, saves its id and then sends it, so a crash can never send twice.
  - **First-round interview:**
    - HR enters the interviewer's email, which is remembered in this browser.
    - SmartHR reads that person's free/busy and offers the 3 soonest free slots in working hours over the next 7 days (`SMARTHR_WORK_HOURS`, `SMARTHR_TIMEZONE`), or HR picks another time.
    - It then creates a Teams meeting organised by the mailbox and invites the candidate and the interviewer. A `transactionId` prevents a second meeting on retry.
  - Each step goes out once per candidate (409 on a repeat).
- **Test mode (use it for all testing):** set `SMARTHR_EMAIL_REDIRECT=<your own address>` and restart the API.
  - Every shortlisted email and invite then goes to you instead of the candidate, with `[TEST for <candidate>]` in the subject. The interviewer is still invited.
  - The portal shows a "Test mode is on" note.
  - Test sends don't block the real send later.
  - Never test with real candidate addresses from resumes.

## Not built yet (from the design)

ClamAV virus scan of emailed attachments, auto-routing emails without a job code, 10-minute undo before sending, OCR, years of experience calculated from dates, field-level encryption of personal data, auth and roles, HR decisions, outbox and audit log.
