#!/usr/bin/env python3
"""Lightweight status + docs web UI for the KB (port beside MCP)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

ROOT = Path(__file__).resolve().parents[1]
STRUCTURED = ROOT / "knowledge_base" / "structured"

app = FastAPI(title="Sorghum Protein KB", version="1.0")


def _index() -> dict:
    p = STRUCTURED / "index.json"
    if not p.exists():
        return {"n_papers": 0, "papers": []}
    return json.loads(p.read_text())


@app.get("/health")
def health():
    idx = _index()
    return {
        "ok": True,
        "service": "sorghum-protein-kb",
        "papers": idx.get("n_papers"),
        "figures": idx.get("total_figures"),
        "tables": idx.get("total_tables"),
        "mcp_path": "/mcp",
    }


@app.get("/", response_class=HTMLResponse)
def home():
    idx = _index()
    n = idx.get("n_papers", 0)
    figs = idx.get("total_figures", 0)
    tables = idx.get("total_tables", 0)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Sorghum Protein KB</title>
<style>
 body{{font-family:ui-sans-serif,system-ui;max-width:820px;margin:40px auto;padding:0 16px;line-height:1.5;color:#142}}
 code,pre{{background:#f4f6f4;padding:2px 6px;border-radius:4px}}
 .card{{border:1px solid #d7e0d7;border-radius:10px;padding:16px;margin:16px 0}}
 a{{color:#1b5e20}}
</style></head>
<body>
<h1>Sorghum Protein Knowledge Base</h1>
<p>Shakoor Lab · MCP + literature corpus</p>
<div class="card">
 <b>{n}</b> papers · <b>{figs}</b> figures · <b>{tables}</b> tables
</div>
<div class="card">
 <h3>MCP endpoint</h3>
 <p>Streamable HTTP: <code>http://localhost:8765/mcp</code></p>
 <p>Health: <a href="/health">/health</a></p>
</div>
<div class="card">
 <h3>Claude / Cursor config</h3>
 <pre>{{
  "mcpServers": {{
    "sorghum-protein": {{
      "url": "http://localhost:8765/mcp"
    }}
  }}
}}</pre>
</div>
</body></html>"""


@app.get("/papers")
def papers(limit: int = 50):
    idx = _index()
    rows = (idx.get("papers") or [])[: max(1, min(limit, 200))]
    return JSONResponse(rows)


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("SORGHUM_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("SORGHUM_WEB_PORT", "8766"))
    uvicorn.run(app, host=host, port=port)
