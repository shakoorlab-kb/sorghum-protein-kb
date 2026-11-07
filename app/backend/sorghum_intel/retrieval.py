"""Hybrid passage retrieval over the Neo4j indexes: vector + full text, fused by RRF."""
from __future__ import annotations

import re

from . import embed, graphdb

RRF_K = 60


def rrf(*ranked_lists: list[str], weights: list[float] | None = None) -> dict[str, float]:
    scores: dict[str, float] = {}
    for li, ids in enumerate(ranked_lists):
        w = weights[li] if weights else 1.0
        for rank, cid in enumerate(ids):
            scores[cid] = scores.get(cid, 0.0) + w / (RRF_K + rank + 1)
    return scores


# Accession, line, gene and marker IDs: semantic search cannot match these, and in a mixed
# question generic words outrank them in full text, so they get a search of their own.
_ID = re.compile(r"\b(?:PI\s?\d{5,7}|IS\s?\d{3,6}|[A-Za-z]{1,6}[.\-]?\d[\w.\-]{1,20}|\d+[A-Za-z][\w\-]*)\b")


def identifiers(query: str) -> list[str]:
    out = []
    for m in _ID.findall(query):
        if sum(ch.isdigit() for ch in m) and sum(ch.isalpha() for ch in m) and not re.fullmatch(r"\d{4}s?", m):
            out.append(m)
            if re.match(r"(?i)(PI|IS)\s\d", m):
                out.append(m.replace(" ", ""))
    return list(dict.fromkeys(out))[:8]


def hybrid_ids(query: str, k: int = 12, doc_ids: list[str] | None = None) -> list[tuple[str, float]]:
    vec = graphdb.vector_search(embed.embed_query(query), k=max(k * 3, 30), doc_ids=doc_ids)
    ft = graphdb.fulltext_search(query, k=max(k * 3, 30), doc_ids=doc_ids)
    ids = identifiers(query)
    exact = graphdb.fulltext_search(" ".join(ids), k=max(k * 2, 20), doc_ids=doc_ids) if ids else []
    fused = rrf([r["id"] for r in vec], [r["id"] for r in ft], [r["id"] for r in exact], weights=[1.0, 0.8, 1.6])
    return sorted(fused.items(), key=lambda kv: -kv[1])[:k]


def search(query: str, k: int = 12, doc_ids: list[str] | None = None, max_per_doc: int = 3) -> list[dict]:
    ranked = hybrid_ids(query, k=k * 2, doc_ids=doc_ids)
    rows = graphdb.chunks_by_ids([cid for cid, _ in ranked])
    out, per_doc = [], {}
    for cid, score in ranked:
        r = rows.get(cid)
        if not r:
            continue
        did = r["document"]["id"]
        if per_doc.get(did, 0) >= max_per_doc:
            continue
        per_doc[did] = per_doc.get(did, 0) + 1
        out.append({**r, "score": round(score, 5)})
        if len(out) >= k:
            break
    return out
