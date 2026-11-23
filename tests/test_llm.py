from app import llm

RAG = {"text": "Built a RAG chatbot with LangChain and FAISS.", "section": "Projects", "start": 10, "end": 55}
DEGREE = {"text": "B.Tech, EEE - CUSAT", "section": "Education", "start": 80, "end": 99}
PYTHON = {"text": "Languages: Python, SQL", "section": "Skills", "start": 120, "end": 142}
PANDAS = {"text": "Analytics: Pandas, NumPy", "section": "Skills", "start": 150, "end": 174}
RESUME = [RAG, DEGREE, PYTHON, PANDAS]


def _evidence(rid, *parts):
    return {"requirement_id": rid, "requirement": " with ".join(parts), "type": "must", "weight": 4,
            "verdict": "partial", "match_score": 0.4, "quote": "search quote", "start": 0, "end": 12,
            "parts": [{"text": p} for p in parts], "candidates": []}


def test_every_part_reads_the_whole_resume_and_quotes_only_real_lines(monkeypatch):
    answers = {"AI skills": {"verdict": "met", "line": 1, "reason": "RAG is AI."},
               "Degree in CS": {"verdict": "met", "line": 7, "reason": "Points at nothing."},
               "Docker": {"verdict": "not_found", "line": 0, "reason": "No container work."}}
    asked = []

    def ask(requirement, resume):
        asked.append(resume)
        return answers[requirement]
    monkeypatch.setattr(llm, "_ask", ask)
    reviewed, judge = llm.review([_evidence("A1", "AI skills"), _evidence("A2", "Degree in CS"),
                                  _evidence("A3", "Docker")], RESUME)

    assert judge == llm.LLM_MODEL
    assert asked == [llm.number_lines(RESUME)] * 3  # same whole resume every call (so Ollama reuses the prompt)
    assert asked[0].splitlines()[1] == "[2] Education: B.Tech, EEE - CUSAT"
    ai, degree, docker = reviewed
    assert (ai["verdict"], ai["quote"], ai["start"], ai["reason"], ai["parts"]) == ("met", RAG["text"], 10, "RAG is AI.", [])
    assert degree["verdict"] == "partial" and degree["quote"] is None  # cited a line that does not exist
    assert docker["verdict"] == "not_found" and docker["quote"] is None


def test_compound_requirements_are_judged_part_by_part(monkeypatch):
    answers = {"Python programming": {"verdict": "met", "line": 3, "reason": "Python listed."},
               "NumPy, Pandas or TensorFlow": {"verdict": "met", "line": 4, "reason": "Pandas listed."},
               "Docker": {"verdict": "not_found", "line": 0, "reason": "None."}}
    monkeypatch.setattr(llm, "_ask", lambda requirement, resume: answers[requirement])
    (python, half_done), _ = llm.review([_evidence("A2", "Python programming", "NumPy, Pandas or TensorFlow"),
                                         _evidence("A9", "Python programming", "Docker")], RESUME)

    assert python["verdict"] == "met" and python["quote"] == PYTHON["text"]
    assert [(p["text"], p["quote"]) for p in python["parts"]] == [("Python programming", PYTHON["text"]),
                                                                  ("NumPy, Pandas or TensorFlow", PANDAS["text"])]
    assert half_done["verdict"] == "partial"  # one part found, one not: never met


def test_review_keeps_search_verdicts_when_ollama_is_down_or_resume_is_empty(monkeypatch):
    def down(requirement, resume):
        raise llm.urllib.error.URLError("connection refused")
    monkeypatch.setattr(llm, "_ask", down)
    evidence = [_evidence("A1", "AI skills")]
    assert llm.review(evidence, RESUME) == (evidence, None)
    assert llm.review(evidence, []) == (evidence, None)
