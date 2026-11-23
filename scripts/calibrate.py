"""Offline calibration on local resumes: compare embedding/reranker models and verdict thresholds. Nothing is uploaded.

Atlas is emulated in memory (cosine top-k for Vector Search, BM25 for Atlas Search), so results carry over.
Raw scores are cached in data/calibration/ (git-ignored, contains personal data).

    .venv/Scripts/python scripts/calibrate.py collect BAAI/bge-small-en-v1.5 Xenova/ms-marco-MiniLM-L-6-v2
    .venv/Scripts/python scripts/calibrate.py report
    .venv/Scripts/python scripts/calibrate.py collect-nli   # current models + NLI judge, whole requirement and split parts
    .venv/Scripts/python scripts/calibrate.py report-nli
"""
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import reader  # noqa: E402

DATA = ROOT / "data" / "Resume-dataset-and-company-recieved"
OUT = ROOT / "data" / "calibration"
SPECS = json.loads((ROOT / "data" / "JD" / "jobs.json").read_text(encoding="utf-8"))["jobs"]
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
TOP_K = 5


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9+#]+(?:\.[a-z0-9+#]+)*", text.lower())


def _bm25(query: str, docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> list[float]:
    n, avg = len(docs), (sum(map(len, docs)) / len(docs)) or 1
    terms = set(_tokens(query))
    idf = {t: math.log(1 + (n - (df := sum(t in d for d in docs)) + 0.5) / (df + 0.5)) for t in terms}
    return [sum(idf[t] * (tf := d.count(t)) * (k1 + 1) / (tf + k1 * (1 - b + b * len(d) / avg)) for t in terms if t in d)
            for d in docs]


def resumes() -> list[tuple[str, str, str]]:
    """(folder, file name, text) for every readable company resume."""
    out = []
    for spec in SPECS:
        for f in sorted((DATA / spec["resume_folder"]).iterdir()):
            try:
                text = reader.extract_text(f.name, f.read_bytes())
            except Exception:
                continue
            if reader.quality(text, reader.chunk(text)) == "ok":
                out.append((spec["resume_folder"], f.name, text))
    return out


def collect(embed_model: str, rerank_model: str) -> None:
    from fastembed import TextEmbedding
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    embedder, reranker = TextEmbedding(embed_model), TextCrossEncoder(rerank_model)

    def embed(texts):
        v = np.array(list(embedder.embed(texts)), dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    rows = []
    items = resumes()
    for n, (folder, name, text) in enumerate(items, 1):
        chunks = reader.chunk(text)
        vectors = embed([reader.embedding_text(c) for c in chunks])
        tokens = [_tokens(reader.embedding_text(c)) for c in chunks]
        for spec in SPECS:
            rubric = spec["requirements"]
            queries = embed([QUERY_PREFIX + r["text"] for r in rubric])
            for req, q in zip(rubric, queries):
                cosine = vectors @ q
                by_meaning = list(np.argsort(-cosine)[:TOP_K])
                keyword = _bm25(req["text"], tokens)
                by_keyword = [i for i in np.argsort(-np.array(keyword))[:TOP_K] if keyword[i] > 0]
                fused: dict = {}
                for ranking in (by_meaning, by_keyword):
                    for rank, i in enumerate(ranking):
                        fused[int(i)] = fused.get(int(i), 0) + 1 / (61 + rank)
                top = sorted(fused, key=fused.get, reverse=True)[:TOP_K]
                logits = list(reranker.rerank(req["text"], [chunks[i]["text"] for i in top]))
                rows.append({"folder": folder, "file": name, "job": spec["code"], "req": req["id"], "type": req["type"],
                             "weight": req["weight"],
                             "cands": [{"p": 1 / (1 + math.exp(-x)), "sim": float(cosine[i]), "text": chunks[i]["text"][:160]}
                                       for i, x in zip(top, logits)]})
        print(f"\r{n}/{len(items)}", end="", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    tag = f"{embed_model.split('/')[-1]}__{rerank_model.split('/')[-1]}"
    (OUT / f"{tag}.json").write_text(json.dumps(rows), encoding="utf-8")
    print(f"\nsaved {tag}")


def _top(query: str, q: np.ndarray, vectors: np.ndarray, tokens: list[list[str]]) -> tuple[list[int], np.ndarray]:
    """Emulated Atlas hybrid search for one query: RRF of cosine top-k and BM25 top-k."""
    cosine = vectors @ q
    keyword = _bm25(query, tokens)
    by_keyword = [i for i in np.argsort(-np.array(keyword))[:TOP_K] if keyword[i] > 0]
    fused: dict = {}
    for ranking in (list(np.argsort(-cosine)[:TOP_K]), by_keyword):
        for rank, i in enumerate(ranking):
            fused[int(i)] = fused.get(int(i), 0) + 1 / (61 + rank)
    return sorted(fused, key=fused.get, reverse=True)[:TOP_K], cosine


def collect_nli() -> None:
    from app import matching
    rows = []
    items = resumes()
    for n, (folder, name, text) in enumerate(items, 1):
        chunks = reader.chunk(text)
        premises = [reader.embedding_text(c) for c in chunks]
        vectors = matching.embed(premises)
        tokens = [_tokens(t) for t in premises]
        for spec in SPECS:
            for req in spec["requirements"]:
                parts = []
                for part in dict.fromkeys([req["text"], *matching.split_requirement(req["text"])]):
                    q = matching.embed([QUERY_PREFIX + part])[0]
                    top, cosine = _top(part, q, vectors, tokens)
                    logits = list(matching._reranker().rerank(part, [chunks[i]["text"] for i in top]))
                    entail = matching.entailment([premises[i] for i in top], matching.hypothesis(part))
                    parts.append({"text": part, "cands": [{"p": 1 / (1 + math.exp(-x)), "sim": float(cosine[i]), "e": e}
                                                          for i, x, e in zip(top, logits, entail)]})
                rows.append({"folder": folder, "file": name, "job": spec["code"], "req": req["id"], "type": req["type"],
                             "weight": req["weight"], "whole": parts[0],
                             "parts": parts[1:] or parts[:1]})
        print(f"\r{n}/{len(items)}", end="", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "nli.json").write_text(json.dumps(rows), encoding="utf-8")
    print("\nsaved nli")


def nli_verdict(row: dict, use_parts: bool, rule: str, t: float) -> tuple[str, float]:
    """rule 'nli': entailment alone; 'nli+sim': entailment, or the current reranker/similarity rule."""
    verdicts, scores = [], []
    for part in (row["parts"] if use_parts else [row["whole"]]):
        c = part["cands"]
        best_e, best_p, best_sim = max(x["e"] for x in c), max(x["p"] for x in c), max(x["sim"] for x in c)
        met = best_e >= t or (rule == "nli+sim" and (best_p >= 0.5 or best_sim >= 0.72))
        verdicts.append("met" if met else "partial" if best_sim >= 0.66 else "not_found")
        scores.append(max(best_e, best_p))
    if all(v == "met" for v in verdicts):
        return "met", min(scores)
    return ("partial" if any(v != "not_found" for v in verdicts) else "not_found"), min(scores)


def report_nli() -> None:
    rows = json.loads((OUT / "nli.json").read_text(encoding="utf-8"))
    value = {"met": 1, "partial": 0.5, "not_found": 0}
    for spec in SPECS:
        own = spec["resume_folder"]
        job_rows = [r for r in rows if r["job"] == spec["code"]]
        by_file: dict = {}
        for r in job_rows:
            by_file.setdefault((r["folder"], r["file"]), []).append(r)
        print(f"== {spec['code']} (own folder: {own})")
        for req in spec["requirements"]:
            mine = [r for r in job_rows if r["req"] == req["id"]]
            parts = " | ".join(p["text"] for p in mine[0]["parts"])
            for use_parts in (False, True):
                rate = lambda folder_is_own: np.mean([nli_verdict(r, use_parts, "nli", 0.5)[0] == "met" for r in mine
                                                      if (r["folder"] == own) == folder_is_own])
                print(f"   {req['id']:<4}{req['type']:<5}{'parts' if use_parts else 'whole'}  nli>=.5 met: own {rate(True):4.0%} other {rate(False):4.0%}"
                      f"  {(parts if use_parts else req['text'])[:70]}")
        for use_parts in (False, True):
            for rule in ("nli", "nli+sim"):
                for t in (0.5, 0.7, 0.9):
                    scored = {}
                    for k, rs in by_file.items():
                        vs = [nli_verdict(r, use_parts, rule, t) for r in rs]
                        total = sum(r["weight"] for r in rs)
                        cov = sum(r["weight"] * value[v] for r, (v, _) in zip(rs, vs)) / total
                        sem = sum(r["weight"] * s for r, (_, s) in zip(rs, vs)) / total
                        scored[k] = (100 * (0.8 * cov + 0.2 * sem),
                                     all(v == "met" for r, (v, _) in zip(rs, vs) if r["type"] == "must"))
                    pos = [s for (f, _), (s, _) in scored.items() if f == own]
                    neg = [s for (f, _), (s, _) in scored.items() if f != own]
                    mp = np.mean([ok for (f, _), (_, ok) in scored.items() if f == own])
                    mn = np.mean([ok for (f, _), (_, ok) in scored.items() if f != own])
                    print(f"   {'parts' if use_parts else 'whole'} {rule:<8} t={t}  AUC {auc(pos, neg):.3f}  all must-haves met: own {mp:5.0%}  other {mn:5.0%}")


def verdict(cands: list[dict], met_p: float, met_sim: float, partial_sim: float) -> tuple[str, float]:
    best_p = max(cands, key=lambda c: c["p"])
    best_sim = max(c["sim"] for c in cands)
    if best_p["p"] >= met_p or best_sim >= met_sim:
        return "met", best_p["p"]
    return ("partial" if best_sim >= partial_sim else "not_found"), best_p["p"]


def fit(rows: list[dict], met_p: float, met_sim: float, partial_sim: float) -> tuple[float, bool]:
    value = {"met": 1, "partial": 0.5, "not_found": 0}
    total = sum(r["weight"] for r in rows)
    verdicts = [verdict(r["cands"], met_p, met_sim, partial_sim) for r in rows]
    coverage = sum(r["weight"] * value[v] for r, (v, _) in zip(rows, verdicts)) / total
    semantic = sum(r["weight"] * p for r, (_, p) in zip(rows, verdicts)) / total
    must_ok = all(v == "met" for r, (v, _) in zip(rows, verdicts) if r["type"] == "must")
    return 100 * (0.8 * coverage + 0.2 * semantic), must_ok


def auc(pos: list[float], neg: list[float]) -> float:
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


def report() -> None:
    for path in sorted(OUT.glob("*.json")):
        rows = json.loads(path.read_text(encoding="utf-8"))
        print(f"\n######## {path.stem}")
        for spec in SPECS:
            job_rows = [r for r in rows if r["job"] == spec["code"]]
            by_file: dict = {}
            for r in job_rows:
                by_file.setdefault((r["folder"], r["file"]), []).append(r)
            own = spec["resume_folder"]
            print(f"== {spec['code']} (own folder: {own})")
            # Separation per requirement: how strongly own-role resumes match vs the other role's resumes.
            for req in spec["requirements"]:
                pos = [max(c["sim"] for c in r["cands"]) for r in job_rows if r["req"] == req["id"] and r["folder"] == own]
                neg = [max(c["sim"] for c in r["cands"]) for r in job_rows if r["req"] == req["id"] and r["folder"] != own]
                pp = [max(c["p"] for c in r["cands"]) for r in job_rows if r["req"] == req["id"] and r["folder"] == own]
                print(f"   {req['id']:<4}{req['type']:<5} sim own p50 {np.median(pos):.3f} p25 {np.percentile(pos, 25):.3f} | "
                      f"other p50 {np.median(neg):.3f} p90 {np.percentile(neg, 90):.3f} | rerank own p50 {np.median(pp):.3f}"
                      f"  {req['text'][:44]}")
            for label, met_p, met_sim, partial_sim in [("current rule", 0.5, 9, 0.6)] + [
                    (f"met_p .5 met_sim {s:.2f} partial {ps:.2f}", 0.5, s, ps)
                    for s in (0.70, 0.72, 0.74, 0.76) for ps in (0.62, 0.64, 0.66)]:
                scored = {k: fit(v, met_p, met_sim, partial_sim) for k, v in by_file.items()}
                pos = [s for (folder, _), (s, _) in scored.items() if folder == own]
                neg = [s for (folder, _), (s, _) in scored.items() if folder != own]
                must_pos = sum(ok for (folder, _), (_, ok) in scored.items() if folder == own) / len(pos)
                must_neg = sum(ok for (folder, _), (_, ok) in scored.items() if folder != own) / len(neg)
                print(f"   {label:<34} AUC {auc(pos, neg):.3f}  all must-haves met: own {must_pos:5.0%}  other {must_neg:5.0%}")


if __name__ == "__main__":
    if sys.argv[1] == "collect":
        collect(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "collect-nli":
        collect_nli()
    elif sys.argv[1] == "report-nli":
        report_nli()
    else:
        report()
