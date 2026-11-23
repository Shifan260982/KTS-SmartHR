"""Steps 5 and 7 of the design: hybrid search (Atlas Vector Search + Atlas Search) -> rerank + entailment -> score.
No LLM.

Each requirement is split into the parts it joins ("Python and libraries such as NumPy"), and each part must be proven
by some resume line, judged by an NLI entailment model or the reranker. Quotes are the matched chunks themselves, so
every quote exists in the resume by construction.
"""
import math
import os
import re
from collections.abc import Callable
from functools import cache
from pathlib import Path

import numpy as np

from app import reader

# shortcut: small models (~150 MB) instead of the design's bge-base + bge-reranker-base (~1.3 GB) because of a slow network;
# set these two env vars to switch back once a faster link is available.
EMBED_MODEL = os.getenv("SMARTHR_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
RERANK_MODEL = os.getenv("SMARTHR_RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
# Judges whether a resume line proves a requirement; rerankers only judge relevance and miss "B.Tech" => "Bachelor's".
NLI_MODEL = os.getenv("SMARTHR_NLI_MODEL", "Xenova/nli-deberta-v3-small")
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


@cache
def _nli():
    """Entailment model (premise -> hypothesis), run directly on ONNX Runtime; fastembed has no NLI models."""
    import json

    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer
    labels = json.loads(Path(hf_hub_download(NLI_MODEL, "config.json")).read_text(encoding="utf-8"))["id2label"]
    tokenizer = Tokenizer.from_file(hf_hub_download(NLI_MODEL, "tokenizer.json"))
    tokenizer.enable_truncation(512)
    tokenizer.enable_padding()
    session = ort.InferenceSession(hf_hub_download(NLI_MODEL, "onnx/model_quantized.onnx"))
    entail = next(int(k) for k, v in labels.items() if v.lower() == "entailment")
    return tokenizer, session, entail


def entailment(premises: list[str], hypothesis: str) -> list[float]:
    """Probability that each premise (a resume line) proves the hypothesis (one requirement part)."""
    if not premises:
        return []
    tokenizer, session, entail = _nli()
    encoded = tokenizer.encode_batch([(p, hypothesis) for p in premises])
    feed = {"input_ids": np.array([e.ids for e in encoded]), "attention_mask": np.array([e.attention_mask for e in encoded])}
    if any(i.name == "token_type_ids" for i in session.get_inputs()):
        feed["token_type_ids"] = np.array([e.type_ids for e in encoded])
    logits = session.run(None, feed)[0]
    probs = np.exp(logits - logits.max(axis=1, keepdims=True))
    return (probs[:, entail] / probs.sum(axis=1)).tolist()


def hypothesis(part: str) -> str:
    return f"The candidate has {part[0].lower()}{part[1:]}."


MIN_LEFT_WORDS, MIN_RIGHT_WORDS = 2, 3  # a split-off right part (".NET Core") matches too loosely alone; calibrated on 177 resumes
_JOINER = re.compile(r"\s+(?:and|with)\s+", re.IGNORECASE)
_LEAD_IN = re.compile(r"^.*?\b(?:with|in|of|on|using|for)\s+", re.IGNORECASE)


def split_requirement(text: str) -> list[str]:
    """Split a compound requirement into parts that must each be proven ("Python and libraries such as NumPy").

    Splits on "and"/"with" outside brackets, only when the left keeps two words and the right three, so lists of one noun
    ("pre-processing, cleaning and analysis") and verb pairs ("Developing and deploying models") stay whole.
    """
    depth, cuts = 0, []
    for i, ch in enumerate(text):
        depth += (ch in "([") - (ch in ")]")
        if depth == 0 and (m := _JOINER.match(text, i)) and (i == 0 or text[i - 1] != " "):
            left, right = text[:m.start()], text[m.end():]
            if len(left.split()) >= MIN_LEFT_WORDS and len(right.split()) >= MIN_RIGHT_WORDS:
                cuts.append(m)
    if not cuts:
        return [text.strip()]
    cut = cuts[0]
    left, right = text[:cut.start()].strip(), text[cut.end():]
    # "Familiarity with Python and libraries" -> the second part keeps its lead-in ("Familiarity with libraries")
    if cut.group().strip().lower() == "and" and (lead := _LEAD_IN.match(left)):
        right = lead.group() + right
    return [left, *split_requirement(right)]


def warm_up() -> None:
    """Load (and on first run download) every model so the first request is not slow."""
    embed(["warm up"])
    list(_reranker().rerank("warm up", ["warm up"]))
    entailment(["warm up"], "warm up")


def models() -> dict:
    return {"embedding": EMBED_MODEL, "reranker": RERANK_MODEL, "nli": NLI_MODEL}


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


def _judge_part(part: str, query_vector: np.ndarray, settings: dict, search: Search) -> dict:
    """Verdict for one requirement part: does any of its best resume chunks prove it?"""
    by_meaning, by_keyword = search(part, query_vector.tolist())
    chunks = {c["_id"]: c for c in [*by_meaning, *by_keyword]}
    top = rrf([c["_id"] for c in by_meaning], [c["_id"] for c in by_keyword])[:CANDIDATES_PER_REQUIREMENT]
    if not top:
        return {"text": part, "verdict": "not_found", "score": 0.0, "similarity": 0.0, "chunk": None, "candidates": []}

    keyword = {c["_id"]: c.get("keyword", 0.0) for c in by_keyword}
    logits = list(_reranker().rerank(part, [chunks[i]["text"] for i in top]))
    entails = entailment([reader.embedding_text(chunks[i]) for i in top], hypothesis(part))
    candidates = sorted(
        ({"chunk": chunks[i], "rerank": 1 / (1 + math.exp(-x)), "entail": e, "keyword": keyword.get(i, 0.0),
          # raw cosine, computed here so it means the same whichever search found the chunk
          "vector": float(np.asarray(chunks[i]["embedding"], dtype=np.float32) @ query_vector)}
         for i, x, e in zip(top, logits, entails)),
        key=lambda c: -max(c["entail"], c["rerank"]),
    )
    best = candidates[0]
    if best["entail"] >= settings["entail_threshold"] or best["rerank"] >= settings["met_threshold"]:
        verdict = "met"
    else:
        # Skill lists and paraphrases can defeat both cross-encoders, so meaning similarity can also confirm a match,
        # or mark it as related but weaker.
        best = max(candidates, key=lambda c: c["vector"])
        verdict = ("met" if best["vector"] >= settings["met_similarity"]
                   else "partial" if best["vector"] >= settings["partial_similarity"] else "not_found")
    return {"text": part, "verdict": verdict, "score": max(candidates[0]["entail"], candidates[0]["rerank"]),
            "similarity": best["vector"], "chunk": best["chunk"] if verdict != "not_found" else None,
            "candidates": candidates}


def requirement_verdict(verdicts: list[str]) -> str:
    """A requirement is met only when every part is; anything found makes it partial."""
    if all(v == "met" for v in verdicts):
        return "met"
    return "partial" if any(v != "not_found" for v in verdicts) else "not_found"


def match_requirements(rubric: list[dict], settings: dict, search: Search) -> list[dict]:
    parts_by_req = [split_requirement(r["text"]) for r in rubric]
    query_vectors = iter(embed([QUERY_PREFIX + p for parts in parts_by_req for p in parts]))
    evidence = []
    for req, parts in zip(rubric, parts_by_req):
        judged = [_judge_part(p, next(query_vectors), settings, search) for p in parts]
        verdict = requirement_verdict([j["verdict"] for j in judged])
        quoted = next((j for j in judged if j["chunk"]), None) if verdict != "not_found" else None
        evidence.append({
            "requirement_id": req["id"],
            "requirement": req["text"],
            "type": req["type"],
            "weight": req["weight"],
            "verdict": verdict,
            "match_score": round(min(j["score"] for j in judged), 4),
            "similarity": round(min(j["similarity"] for j in judged), 4),
            "quote": quoted["chunk"]["text"] if quoted else None,
            "start": quoted["chunk"]["start"] if quoted else None,
            "end": quoted["chunk"]["end"] if quoted else None,
            "parts": [{"text": j["text"], "verdict": j["verdict"], "score": round(j["score"], 4),
                       "quote": j["chunk"]["text"] if j["chunk"] else None,
                       "start": j["chunk"]["start"] if j["chunk"] else None,
                       "end": j["chunk"]["end"] if j["chunk"] else None} for j in judged],
            "candidates": [
                {"part": j["text"], "text": c["chunk"]["text"], "section": c["chunk"].get("section", ""),
                 "start": c["chunk"]["start"], "end": c["chunk"]["end"],
                 "entail_score": round(c["entail"], 4), "rerank_score": round(c["rerank"], 4),
                 "vector_similarity": round(c["vector"], 4), "keyword_score": round(c["keyword"], 4)}
                for j in judged for c in j["candidates"]
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
