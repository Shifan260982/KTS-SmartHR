from app.matching import bm25, compute_score, rrf, tokens
from app.reader import chunk
from app.schemas import Requirement

SETTINGS = {"met_threshold": 0.5, "partial_similarity": 0.6, "evidence_weight": 0.8, "strong_fit": 75, "floor": 40}


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


def test_tokens_keep_tech_names():
    assert tokens("ASP.NET Core, C#, C++ and Node.js.") == ["asp.net", "core", "c#", "c++", "and", "node.js"]


def test_bm25_ranks_keyword_match_first():
    docs = [tokens("designed logos"), tokens("tuned SQL Server queries"), tokens("sql")]
    scores = bm25("SQL Server query tuning", docs)
    assert scores[0] == 0 and scores[1] > scores[2] > 0


def test_rrf_prefers_items_ranked_well_in_both_lists():
    assert rrf([0, 1, 2], [1, 2, 0])[0] == 1


def test_protected_characteristics_rejected():
    import pytest
    with pytest.raises(ValueError):
        Requirement(id="R9", text="Candidate age under 30", type="must", weight=1)
    Requirement(id="R9", text="Experience with single sign-on and managers", type="nice", weight=1)
