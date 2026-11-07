#!/usr/bin/env python3
"""Build or refresh the embedded property graph from structured papers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_graph import DEFAULT_DB, KnowledgeGraph

ROOT = Path(__file__).resolve().parents[1]
STRUCTURED = ROOT / "knowledge_base" / "structured"


def build(limit: int = 0) -> dict:
    graph = KnowledgeGraph()
    paths = sorted(STRUCTURED.glob("*/paper.json"))
    if limit:
        paths = paths[:limit]
    ok, failed = 0, []
    for number, path in enumerate(paths, 1):
        try:
            result = graph.index_paper(json.loads(path.read_text()))
            if result.get("ok"):
                ok += 1
            else:
                failed.append({"path": str(path), "error": result.get("error")})
        except Exception as exc:
            failed.append({"path": str(path), "error": str(exc)})
        if number % 100 == 0:
            print(f"Indexed {number}/{len(paths)} papers", flush=True)
    pruned = graph.prune_orphans()
    return {
        "ok": not failed,
        "indexed": ok,
        "failed": failed,
        "pruned_orphan_nodes": pruned,
        "database": str(DEFAULT_DB),
        "stats": graph.stats(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Index only the first N papers (testing)")
    args = parser.parse_args()
    print(json.dumps(build(limit=max(0, args.limit)), indent=2))
