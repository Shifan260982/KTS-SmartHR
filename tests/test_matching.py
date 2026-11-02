from app.matching import compute_score, embed, match_requirements, rrf
from app import reader
from app.reader import chunk
from app.schemas import Requirement

SETTINGS = {"met_threshold": 0.5, "met_similarity": 0.74, "partial_similarity": 0.6, "evidence_weight": 0.8, "strong_fit": 75, "floor": 40}


def _evidence(rid, kind, weight, verdict, score=0.78):
    return {"requirement_id": rid, "type": kind, "weight": weight, "verdict": verdict, "match_score": score}


def test_design_worked_example_scores_82():
    evidence = [_evidence("R1", "must", 5, "met"), _evidence("R2", "must", 4, "met"),
                _evidence("R3", "nice", 2, "partial"), _evidence("R4", "nice", 1, "not_found")]
    result = compute_score(evidence, SETTINGS)
    assert result["coverage"] == round(10 / 12, 4)
    assert round(result["fit_score"]) == 82
    assert result["band"] == "strong_fit"


def test_missing_must_have_overrides_score():
    evidence = [_evidence("R1", "must", 5, "partial", 0.99), _evidence("R2", "nice", 5, "met", 0.99)]
    assert compute_score(evidence, SETTINGS)["band"] == "must_have_missing"


def test_chunks_keep_exact_offsets_and_cover_text():
    text = "Header\nshort\n" + "A long sentence about ASP.NET Core APIs. " * 20 + "\n\nSkills: C#, SQL Server"
    chunks = chunk(text)
    assert chunks
    for c in chunks:
        assert text[c["start"]:c["end"]] == c["text"]
        assert len(c["text"]) <= 400
    assert "Skills: C#, SQL Server" in chunks[-1]["text"]


RESUME = """PRIYA SHARMA
AI INTERN | APPLIED AI
PROFESSIONAL PROFILE
Master's candidate in Artificial Intelligence building Python-based LLM and agentic
workflows with document processing and human-
in-the-loop controls.
CORE TECHNICAL SKILLS
AI & Machine Learning: Python, scikit-learn, LLM workflows, SBERT, BM25,
TF-IDF, Cosine Similarity
Software & Integration: Flask, REST/JSON APIs, SQL/SQLite
SELECTED AI PROJECTS
• Evaluated the pipeline with ranking metrics including nDCG@5 of
0.9279.
EDUCATION
BTech, Electrical & Electronics Engineering - CUSAT, India | 2018 - 2022
"""


def test_chunks_follow_sentences_labels_and_sections_not_visual_lines():
    chunks = reader.chunk(RESUME)
    texts = [" ".join(c["text"].split()) for c in chunks]
    for c in chunks:
        assert RESUME[c["start"]:c["end"]] == c["text"]
    # a wrapped sentence is one unit; a skills label never swallows the next label
    assert any(t.startswith("Master's candidate") and t.endswith("in-the-loop controls.") for t in texts)
    assert "AI & Machine Learning: Python, scikit-learn, LLM workflows, SBERT, BM25, TF-IDF, Cosine Similarity" in texts
    assert "Software & Integration: Flask, REST/JSON APIs, SQL/SQLite" in texts
    assert any(t.endswith("nDCG@5 of 0.9279.") for t in texts)  # "0.9279." is not a numbered bullet
    education = next(c for c in chunks if c["text"].startswith("BTech"))
    assert education["section"] == "Education"
    assert reader.embedding_text(education).startswith("Education: BTech")
    # the candidate's name above the first section never becomes embedding context
    assert all("Priya" not in c["section"] and "PRIYA" not in c["section"] for c in chunks)


def test_match_requirements_fuses_search_hits_and_quotes_the_best_chunk():
    lines = ["Built REST APIs in ASP.NET Core for payments.", "Designed logos and packaging for brands."]
    chunks = [{"_id": i, "text": t, "start": i * 100, "end": i * 100 + len(t), "embedding": v.tolist()}
              for i, (t, v) in enumerate(zip(lines, embed(lines)))]
    calls = []

    def search(text, vector):  # stands in for Atlas: meaning hits, then keyword hits with a BM25 score
        calls.append(text)
        return chunks, [{**chunks[0], "keyword": 3.2}]

    rubric = [{"id": "R1", "text": "Builds REST APIs", "type": "must", "weight": 5}]
    [evidence] = match_requirements(rubric, SETTINGS, search)
    assert calls == ["Builds REST APIs"]
    assert evidence["verdict"] == "met" and evidence["quote"] == lines[0] and evidence["start"] == 0
    assert evidence["candidates"][0]["keyword_score"] == 3.2


def test_no_search_hits_means_not_found_not_an_error():
    rubric = [{"id": "R1", "text": "Kubernetes", "type": "nice", "weight": 2}]
    [evidence] = match_requirements(rubric, SETTINGS, lambda text, vector: ([], []))
    assert evidence["verdict"] == "not_found" and evidence["quote"] is None


def test_rrf_prefers_items_ranked_well_in_both_lists():
    assert rrf([0, 1, 2], [1, 2, 0])[0] == 1


def test_protected_characteristics_rejected():
    import pytest
    with pytest.raises(ValueError):
        Requirement(id="R9", text="Candidate age under 30", type="must", weight=1)
    Requirement(id="R9", text="Experience with single sign-on and managers", type="nice", weight=1)
