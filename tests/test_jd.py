from app.jd import draft_job
from app.schemas import RubricUpdate

BULLETED = """Company - Example Ltd
Job Title: Intern Data Engineer (Onsite)
Experience Required: Fresher
Key Responsibilities
 Build data pipelines
Requirements
 Bachelor's degree in Computer Science or related field
 Strong academic record is mandatory
 Familiarity with Python and libraries such as Pandas, or
NumPy for analysis
What You Get
 Paid stipend
Note: Excellent communication skills are must
How to Apply:
Submit your resume to jobs@example.com
"""

LABELLED = """\U0001f680 We're Hiring! | Senior Backend Engineer – C#, .NET Core | 3-8 Years Experience
*Tech Stack & Requirements:*
Languages/Frameworks*: .NET Core, C#, Entity Framework
Cloud: Azure
This role would suit a driven engineer. Day to day you will:
Software Development
• Designing and Coding: Write clean code based on
specifications and requirements
Testing
• Unit Testing: Create and execute unit tests
"""


def test_bulleted_jd_takes_requirements_section_only():
    job = draft_job(BULLETED, "fallback")
    texts = [r["text"] for r in job["requirements"]]
    assert job["title"] == "Intern Data Engineer (Onsite)"
    assert "Build data pipelines" not in texts and "Paid stipend" not in texts
    assert "Familiarity with Python and libraries such as Pandas, or NumPy for analysis" in texts  # wrapped line joined
    must = {r["text"] for r in job["requirements"] if r["type"] == "must"}
    assert must == {"Strong academic record is mandatory", "Excellent communication skills are must"}
    RubricUpdate(requirements=job["requirements"])  # drafted checklist passes the API's own validation


def test_labelled_stack_lines_and_years():
    job = draft_job(LABELLED, "fallback")
    texts = [r["text"] for r in job["requirements"]]
    assert job["title"] == "Senior Backend Engineer – C#, .NET Core"
    assert texts[0] == "3+ years of professional experience"
    assert "Languages/Frameworks: .NET Core, C#, Entity Framework" in texts and "Cloud: Azure" in texts
    # a continuation line containing "requirements" must not open a requirements section
    assert not any("Unit Testing" in t or "Designing" in t for t in texts)
    RubricUpdate(requirements=job["requirements"])


def test_no_section_falls_back_to_bullets_and_skips_protected_terms():
    job = draft_job("About us\n• Knows SQL\n• Male candidates preferred\n", "Analyst")
    assert [r["text"] for r in job["requirements"]] == ["Knows SQL"]
    assert job["title"] == "Analyst"
