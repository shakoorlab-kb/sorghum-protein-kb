#!/usr/bin/env python3
"""Query the sorghum protein knowledge base (ChromaDB or TF-IDF fallback)."""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "knowledge_base"


def query_chroma(question: str, k: int = 6) -> list[dict]:
    import chromadb
    from chromadb.utils import embedding_functions

    try:
        ef = embedding_functions.DefaultEmbeddingFunction()
    except Exception:
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
    client = chromadb.PersistentClient(path=str(KB / "chroma"))
    col = client.get_collection("sorghum_protein", embedding_function=ef)
    res = col.query(query_texts=[question], n_results=k)
    out = []
    for i in range(len(res["ids"][0])):
        out.append(
            {
                "score": 1.0 - float(res["distances"][0][i]) if res.get("distances") else None,
                "text": res["documents"][0][i],
                "metadata": res["metadatas"][0][i],
            }
        )
    return out


def query_tfidf(question: str, k: int = 6) -> list[dict]:
    data = json.loads((KB / "tfidf_index.json").read_text())
    idf = data["idf"]
    vectors = [dict(v) for v in data["vectors"]]
    chunks = data["chunks"]

    def tokenize(t: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", t.lower())

    q_toks = tokenize(question)
    tf = Counter(q_toks)
    length = len(q_toks) or 1
    qvec = {t: (tf[t] / length) * idf.get(t, 0.0) for t in tf}

    def cos(a: dict, b: dict) -> float:
        keys = set(a) | set(b)
        dot = sum(a.get(x, 0.0) * b.get(x, 0.0) for x in keys)
        na = math.sqrt(sum(v * v for v in a.values())) or 1e-9
        nb = math.sqrt(sum(v * v for v in b.values())) or 1e-9
        return dot / (na * nb)

    scored = [(cos(qvec, vectors[i]), chunks[i]) for i in range(len(chunks))]
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, c in scored[:k]:
        out.append(
            {
                "score": score,
                "text": c["text"],
                "metadata": {
                    "title": c.get("title"),
                    "source_type": c.get("source_type"),
                    "path": c.get("path"),
                    "year": c.get("year"),
                    "doi": c.get("doi"),
                },
            }
        )
    return out


def query(question: str, k: int = 6) -> list[dict]:
    info_path = KB / "kb_info.json"
    backend = "chroma"
    if info_path.exists():
        backend = json.loads(info_path.read_text()).get("backend", "chroma")
    if backend.startswith("chroma"):
        try:
            return query_chroma(question, k=k)
        except Exception as e:
            print(f"[warn] chroma query failed ({e}); trying tfidf", file=sys.stderr)
    return query_tfidf(question, k=k)


def format_context(hits: list[dict]) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        md = h.get("metadata") or {}
        title = md.get("title") or "Untitled"
        doi = md.get("doi") or ""
        src = md.get("source_type") or ""
        score = h.get("score")
        header = f"[{i}] {title} ({src})"
        if doi:
            header += f" | DOI: {doi}"
        if score is not None:
            header += f" | score={score:.3f}"
        blocks.append(header + "\n" + (h.get("text") or ""))
    return "\n\n---\n\n".join(blocks)


def main() -> None:
    ap = argparse.ArgumentParser(description="Query sorghum protein knowledge base")
    ap.add_argument("question", nargs="?", help="Natural language question")
    ap.add_argument("-k", type=int, default=6, help="Number of chunks to retrieve")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args()
    if not args.question:
        ap.print_help()
        print("\nExample:")
        print('  python3 scripts/query_kb.py "Why does cooking reduce sorghum protein digestibility?"')
        sys.exit(0)
    hits = query(args.question, k=args.k)
    if args.json:
        print(json.dumps(hits, indent=2))
    else:
        print(f"Question: {args.question}\n")
        print(format_context(hits))


if __name__ == "__main__":
    main()
