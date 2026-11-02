"""Steps 5 and 7 of the design: hybrid search (Atlas Vector Search + Atlas Search) -> rerank -> score. No LLM.

Without the LLM verify step, the verdict comes from the reranker's probability and the quote is the
matched chunk itself, so every quote exists in the resume by construction.
"""
import math
import os
from collections.abc import Callable
from functools import cache

import numpy as np

# shortcut: small models (~150 MB) instead of the design's bge-base + bge-reranker-base (~1.3 GB) because of a slow network;
# set these two env vars to switch back once a faster link is available.
EMBED_MODEL = os.getenv("SMARTHR_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
RERANK_MODEL = os.getenv("SMARTHR_RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # bge retrieval convention
CANDIDATES_PER_REQUIREMENT = 5
RRF_K = 60
VERDICT_VALUE = {"met": 1.0, "partial": 0.5, "not_found": 0.0}


@cache
def _embedder():
    from fastembed import TextEmbedding
    return TextEmbedding(EMBED_MODEL)


@cache
def _reranker():
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    return TextCrossEncoder(RERANK_MODEL)


def warm_up() -> None:
    """Load (and on first run download) both models so the first request is not slow."""
    embed(["warm up"])
    list(_reranker().rerank("warm up", ["warm up"]))


def models() -> dict:
    return {"embedding": EMBED_MODEL, "reranker": RERANK_MODEL}


def embed(texts: list[str]) -> np.ndarray:
    vectors = np.array(list(_embedder().embed(texts)), dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def dimensions() -> int:
    return int(embed(["dimension probe"]).shape[1])


def rrf(*rankings: list) -> list:
    """Reciprocal Rank Fusion of best-first id lists."""
    fused: dict = {}
    for ranking in rankings:
        for rank, i in enumerate(ranking):
            fused[i] = fused.get(i, 0.0) + 1 / (RRF_K + rank + 1)
    return sorted(fused, key=fused.get, reverse=True)


# (requirement text, query vector) -> (chunks best by meaning, chunks best by keywords); each chunk carries
# _id, text, start, end, embedding, and keyword hits also a "keyword" score.
Search = Callable[[str, list[float]], tuple[list[dict], list[dict]]]


def _not_found(req: dict) -> dict:
    return {"requirement_id": req["id"], "requirement": req["text"], "type": req["type"], "weight": req["weight"],
            "verdict": "not_found", "match_score": 0.0, "similarity": 0.0, "quote": None, "start": None, "end": None,
            "candidates": []}


def match_requirements(rubric: list[dict], settings: dict, search: Search) -> list[dict]:
    query_vectors = embed([QUERY_PREFIX + r["text"] for r in rubric])
    evidence = []
    for req, query_vector in zip(rubric, query_vectors):
        by_meaning, by_keyword = search(req["text"], query_vector.tolist())
        chunks = {c["_id"]: c for c in [*by_meaning, *by_keyword]}
        top = rrf([c["_id"] for c in by_meaning], [c["_id"] for c in by_keyword])[:CANDIDATES_PER_REQUIREMENT]
        if not top:
            evidence.append(_not_found(req))
            continue

        keyword = {c["_id"]: c.get("keyword", 0.0) for c in by_keyword}
        logits = list(_reranker().rerank(req["text"], [chunks[i]["text"] for i in top]))
        candidates = sorted(
            ({"chunk": chunks[i], "rerank": 1 / (1 + math.exp(-x)), "keyword": keyword.get(i, 0.0),
              # raw cosine, computed here so it means the same whichever search found the chunk
              "vector": float(np.asarray(chunks[i]["embedding"], dtype=np.float32) @ query_vector)}
             for i, x in zip(top, logits)),
            key=lambda c: -c["rerank"],
        )
        best = candidates[0]
        p = best["rerank"]
        if p >= settings["met_threshold"]:
            verdict = "met"
        else:
            # The cross-encoder undervalues paraphrases and skill lists ("AI & ML: LLMs, RAG" vs "AI & ML knowledge"),
            # so meaning similarity can also confirm a match, or mark it as related but weaker.
            best = max(candidates, key=lambda c: c["vector"])
            verdict = ("met" if best["vector"] >= settings["met_similarity"]
                       else "partial" if best["vector"] >= settings["partial_similarity"] else "not_found")
        evidence.append({
            "requirement_id": req["id"],
            "requirement": req["text"],
            "type": req["type"],
            "weight": req["weight"],
            "verdict": verdict,
            "match_score": round(p, 4),
            "similarity": round(best["vector"], 4),
            "quote": best["chunk"]["text"] if verdict != "not_found" else None,
            "start": best["chunk"]["start"] if verdict != "not_found" else None,
            "end": best["chunk"]["end"] if verdict != "not_found" else None,
            "candidates": [
                {"text": c["chunk"]["text"], "start": c["chunk"]["start"], "end": c["chunk"]["end"],
                 "rerank_score": round(c["rerank"], 4), "vector_similarity": round(c["vector"], 4),
                 "keyword_score": round(c["keyword"], 4)}
                for c in candidates
            ],
        })
    return evidence


def compute_score(evidence: list[dict], settings: dict) -> dict:
    """Design section 08: Fit = 100 x (w x Coverage + (1 - w) x Semantic), must-have gate, bands."""
    total_weight = sum(e["weight"] for e in evidence)
    coverage = sum(e["weight"] * VERDICT_VALUE[e["verdict"]] for e in evidence) / total_weight
    semantic = sum(e["weight"] * e["match_score"] for e in evidence) / total_weight
    w = settings["evidence_weight"]
    fit = round(100 * (w * coverage + (1 - w) * semantic), 1)
    must_missing = any(e["type"] == "must" and e["verdict"] != "met" for e in evidence)
    if must_missing:
        band = "must_have_missing"
    elif fit >= settings["strong_fit"]:
        band = "strong_fit"
    elif fit >= settings["floor"]:
        band = "possible_fit"
    else:
        band = "low_match"
    return {"fit_score": fit, "coverage": round(coverage, 4), "semantic": round(semantic, 4), "band": band}
