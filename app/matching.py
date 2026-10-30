"""Steps 5 and 7 of the design: hybrid search -> rerank -> score. No LLM.

Without the LLM verify step, the verdict comes from the reranker's probability and the quote is the
matched chunk itself, so every quote exists in the resume by construction.
"""
import math
import os
import re
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


def tokens(text: str) -> list[str]:
    # Keeps tech names intact: "asp.net", "c#", "c++", "node.js".
    return re.findall(r"[a-z0-9+#]+(?:\.[a-z0-9+#]+)*", text.lower())


def bm25(query: str, docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> list[float]:
    n = len(docs)
    avg_len = sum(map(len, docs)) / n or 1
    terms = set(tokens(query))
    doc_sets = [set(d) for d in docs]
    idf = {t: math.log(1 + (n - (df := sum(t in s for s in doc_sets)) + 0.5) / (df + 0.5)) for t in terms}
    scores = []
    for doc in docs:
        score = 0.0
        for t in terms:
            if tf := doc.count(t):
                score += idf[t] * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(doc) / avg_len))
        scores.append(score)
    return scores


def rrf(*rankings: list[int]) -> list[int]:
    """Reciprocal Rank Fusion of best-first index lists."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, i in enumerate(ranking):
            fused[i] = fused.get(i, 0.0) + 1 / (RRF_K + rank + 1)
    return sorted(fused, key=fused.get, reverse=True)


def match_requirements(rubric: list[dict], chunks: list[dict], chunk_vectors: np.ndarray, settings: dict) -> list[dict]:
    query_vectors = embed([QUERY_PREFIX + r["text"] for r in rubric])
    chunk_tokens = [tokens(c["text"]) for c in chunks]
    evidence = []
    for req, query_vector in zip(rubric, query_vectors):
        similarity = chunk_vectors @ query_vector
        vector_rank = [int(i) for i in np.argsort(-similarity)]
        keyword = bm25(req["text"], chunk_tokens)
        keyword_rank = sorted((i for i, s in enumerate(keyword) if s > 0), key=lambda i: -keyword[i])
        top = rrf(vector_rank, keyword_rank)[:CANDIDATES_PER_REQUIREMENT]

        logits = list(_reranker().rerank(req["text"], [chunks[i]["text"] for i in top]))
        candidates = sorted(
            ({"chunk": chunks[i], "rerank": 1 / (1 + math.exp(-x)), "vector": float(similarity[i]), "keyword": keyword[i]}
             for i, x in zip(top, logits)),
            key=lambda c: -c["rerank"],
        )
        best = candidates[0]
        p = best["rerank"]
        if p >= settings["met_threshold"]:
            verdict = "met"
        else:
            # The cross-encoder is near 0 for paraphrases ("PostgreSQL tuning" vs "SQL Server tuning"),
            # so "related but weaker" is judged on vector similarity instead.
            best = max(candidates, key=lambda c: c["vector"])
            verdict = "partial" if best["vector"] >= settings["partial_similarity"] else "not_found"
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
