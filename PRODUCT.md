# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React + TypeScript + Vite, TanStack Query, API types generated from the backend's `/openapi.json` (as specified in `SmartHR_System_Design.pdf`, section 10). Backend: FastAPI under `/api/v1` (this repo, `app/`).

## Users

HR recruiters at Kiaratech who shortlist candidates for open roles. Today they download and read each resume by hand (around 1,000 a day arrive by email). HR managers also own each job's checklist (rubric), weights and thresholds. Used at a desk, on a desktop browser, for long working sessions.

## Product Purpose

SmartHR ranks each resume against an HR-approved checklist for the job, by meaning rather than keywords, and proves every match with an exact line quoted from the resume. HR starts from a ranked, explained shortlist instead of a pile of files. Success: HR trusts the ranking enough to review the top of the list first, and can see why every candidate sits where they do.

## Positioning

Every verdict is backed by a quote that exists in the resume, and the score is plain arithmetic on those verdicts (`Fit = 100 × (0.8 × Coverage + 0.2 × Semantic)`). The system never decides on its own: HR approves the checklist before scoring and makes every final decision.

## Operating Context

- Flow: create a job from a JD → draft and edit the checklist (must-have / nice-to-have, weight 1–10) → approve (locks a version and scores) → upload resumes → review the ranked list → open a candidate and check the evidence.
- Bands: Strong fit, Possible fit, Low match, Must-have missing, Needs attention (unreadable; no score, never a low score).
- Verdicts per requirement: met, partial, not found, each with the matched quote and its character position in the resume text.
- Current roles in use: Intern AI Engineer (`KTS-AI-INTERN`) and Senior Backend Engineer, C#/.NET Core (`KTS-SE1`).

## Capabilities and Constraints

- Matching is semantic only for now (hybrid BM25 + `bge-small-en-v1.5` vectors, MiniLM reranker, all local on CPU). No LLM stage yet.
- Accepted files: PDF, DOCX, TXT up to 10 MB. Scanned PDFs need OCR, which is not built, so they land in Needs attention.
- No sign-in, roles, HR decisions (approve/reject/hold), email or audit log yet. These are in the design for later and must not be faked in the UI.
- Thresholds were tuned on 3 sample resumes only; scores are not yet validated against HR's own shortlists.
- The checklist rejects protected characteristics (age, gender, religion, marital status and similar).

## Brand Commitments

Name: SmartHR, by Kiaratech. Voice from the design document: plain, careful, honest about limits ("an unreadable resume gets no score, never a low score"). No other brand assets are confirmed.

## Evidence on Hand

Real JDs in `data/JD/`, real company resumes in `data/Resume-dataset-and-company-recieved/` (personal data: never shown in demos, screenshots or commits). No testimonials, metrics or customer claims exist.

## Product Principles

1. Evidence before score: a number is never shown without the lines that justify it.
2. HR decides: the system ranks and explains, a person approves.
3. Honest about uncertainty: unreadable, partial and unverified states are visible, never hidden or rounded away.
4. Same rules for every candidate: one locked checklist version per score, traceable.
5. Fast to scan: a recruiter clears a long list in one sitting.

## Accessibility & Inclusion

Internal tool for long sessions: WCAG 2.2 AA, full keyboard use on the review flow, and verdicts never conveyed by colour alone.
