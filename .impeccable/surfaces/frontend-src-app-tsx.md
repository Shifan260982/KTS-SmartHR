---
version: 1
slug: "frontend-src-app-tsx"
primary_target: "frontend/src/App.tsx"
related_targets: ["frontend"]
---

# HR portal (frontend/)

Scope: the whole HR portal SPA. Mode: Operate. Users: HR recruiters and HR managers, desktop, long sessions.
Screens in v1: jobs + rubric editor + thresholds, resume upload, ranked shortlist, candidate review.
Evidence view: extracted resume text with quotes highlighted (new endpoint for document text).
Out of scope until the backend has them: sign-in/roles, approve/reject/hold, email, audit.

## Direction contract

THESIS: Every candidate is examined like an answer script: one mark per requirement, the reason written in the margin beside the line that earned it, the total boxed in examiner red. Refuses the category default of candidate cards with a lone score pill.

OWN-WORLD: Booklet-cover teal shell rail (#1f6f6b family) with a yellow current-item edge; cool paper ground; blue-black ballpoint ink for all text; examiner red reserved strictly for marks and totals (fit score, margin marks), never for errors; amber for must-have-missing and needs-attention states. System UI sans, tabular figures. Marks cells: square boxes, solid = met, hatched = partial, empty = not found; must-have cells carry a red outline.

STORY: The recruiter opens a job, sees who is strongest and exactly which requirements each person met at a glance, opens a candidate, and verifies each mark against the highlighted line in the margin before deciding.

FIRST VIEWPORT: Left teal rail (240px) listing jobs with applicant-count bars and a Needs attention entry. Main: job title (28px) with code, rubric version and Upload resumes as the primary action top right; band filter tabs; ranked table with rank, candidate, boxed red fit, distance to next band, and the S1–Sn marks grid; right column the selected candidate's paper: big boxed score, then resume lines with margin marks.

FORM: Candidate 5 of 7 (The Marked Answer Script). Raises: distance-to-band (Exposure Record), sticky current candidate with J/K (Terminal Wayfinding), rail bars sized by applicants (Acetate Manual), verdict by fill not colour (Labanotation). seed:e4c1675cf540bdf9

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
