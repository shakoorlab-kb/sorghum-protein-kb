"""Command line: python -m sorghum_intel <command>

  serve                         run the web app (uvicorn) on $SI_PORT (default 8000)
  sync [--no-extract] [--limit N] [--force]   index the KB library (and build the graph)
  extract DOC_ID                rebuild one document's graph
  stats                         print graph counts
  claude-plan DIR [--budget N]  batches of documents for Claude Code extraction
  claude-task DOC [--pages a-b] print a document's pages for an extraction agent
  claude-check FILE...          validate extraction files against the page texts
  claude-entities DIR           entity inventory across extraction files (JSON)
  claude-import DIR [--only ..] load extraction files into Neo4j
  graph-audit                   structural checks of the loaded graph
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from pathlib import Path

from . import claude_extract, graphdb, indexer, store


def _wait(jid: str) -> int:
    last = ""
    while True:
        j = store.get_job(jid)
        line = f"[{j['status']}] {j['progress'] * 100:5.1f}% {j['message']}"
        if line != last:
            print(line, flush=True)
            last = line
        if j["status"] in ("done", "failed"):
            print(json.dumps(j["detail"], indent=1)[:4000])
            return 0 if j["status"] == "done" else 1
        time.sleep(2)


def main() -> int:
    ap = argparse.ArgumentParser(prog="sorghum_intel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    s = sub.add_parser("sync")
    s.add_argument("--no-extract", action="store_true")
    s.add_argument("--limit", type=int)
    s.add_argument("--force", action="store_true")
    s.add_argument("--only", nargs="*")
    e = sub.add_parser("extract")
    e.add_argument("doc_id")
    sub.add_parser("stats")
    c = sub.add_parser("claude-plan")
    c.add_argument("dir", type=Path)
    c.add_argument("--budget", type=int, default=110)
    c.add_argument("--only", nargs="*")
    c = sub.add_parser("claude-task")
    c.add_argument("doc_id")
    c.add_argument("--pages")
    c = sub.add_parser("claude-check")
    c.add_argument("files", nargs="+", type=Path)
    c = sub.add_parser("claude-entities")
    c.add_argument("dir", type=Path)
    c = sub.add_parser("claude-import")
    c.add_argument("dir", type=Path)
    c.add_argument("--only", nargs="*")
    c.add_argument("--reset", action="store_true", help="drop all entities and claims first")
    sub.add_parser("graph-audit")
    args = ap.parse_args()

    # File-only commands: no Neo4j needed.
    if args.cmd == "claude-plan":
        p = claude_extract.plan(args.dir, args.budget, args.only)
        print(f"{p['documents']} documents, {p['pages']} pages, {len(p['batches'])} batches -> {args.dir}/batches.json")
        return 0
    if args.cmd == "claude-task":
        print(claude_extract.task(args.doc_id, args.pages))
        return 0
    if args.cmd == "claude-check":
        return claude_extract.check(args.files)
    if args.cmd == "claude-entities":
        print(json.dumps(claude_extract.entity_inventory(args.dir), indent=1, ensure_ascii=False))
        return 0

    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("sorghum_intel.api:app", host="0.0.0.0", port=int(os.environ.get("SI_PORT", "8000")),
                    proxy_headers=True, forwarded_allow_ips="*", timeout_keep_alive=75)
        return 0
    graphdb.ensure_schema()
    if args.cmd == "sync":
        return _wait(indexer.submit_kb_sync(not args.no_extract, args.limit, args.only, args.force))
    if args.cmd == "extract":
        return _wait(indexer.submit_reextract(args.doc_id))
    if args.cmd == "claude-import":
        if args.reset:
            claude_extract.reset_graph()
        rep = claude_extract.import_dir(args.dir, args.only)
        print(json.dumps({k: v for k, v in rep.items() if k != "issues"}, indent=1))
        print(f"documents with notes: {len(rep['issues'])}")
        return 0
    if args.cmd == "graph-audit":
        print(json.dumps(claude_extract.audit(), indent=1, default=str))
        return 0
    if args.cmd == "stats":
        print(json.dumps(graphdb.stats(), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
