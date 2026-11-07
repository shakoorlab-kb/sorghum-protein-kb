#!/usr/bin/env python3
"""Run sorghum-protein MCP over Streamable HTTP (+ status page) for remote clients."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY_DISABLED", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import HTMLResponse, JSONResponse  # noqa: E402

from sorghum_protein_mcp import mcp  # noqa: E402

STRUCTURED = ROOT / "knowledge_base" / "structured"


def _index() -> dict:
    p = STRUCTURED / "index.json"
    if not p.exists():
        return {"n_papers": 0, "total_figures": 0, "total_tables": 0, "papers": []}
    return json.loads(p.read_text())


@mcp.custom_route("/", methods=["GET"])
async def home(_request: Request):
    idx = _index()
    n = idx.get("n_papers", 0)
    figs = idx.get("total_figures", 0)
    tables = idx.get("total_tables", 0)
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Sorghum Protein KB</title>
<style>
 body{{font-family:ui-sans-serif,system-ui;max-width:860px;margin:40px auto;padding:0 16px;line-height:1.5;color:#14231a;background:#f7faf7}}
 code,pre{{background:#eef3ee;padding:2px 6px;border-radius:4px}}
 .card{{background:#fff;border:1px solid #d5e0d6;border-radius:12px;padding:18px;margin:16px 0}}
 a{{color:#1b5e20}}
</style></head><body>
<h1>Sorghum Protein Knowledge Base</h1>
<p>Shakoor Lab · MCP over streamable HTTP</p>
<div class="card"><b>{n}</b> papers · <b>{figs}</b> figures · <b>{tables}</b> tables</div>
<div class="card">
 <h3>MCP endpoint</h3>
 <p><code>http://localhost:8765/mcp</code></p>
  <p>Health: <a href="health">health</a> · All lab services: <a href="/hub/">lab hub</a></p>
</div>
<div class="card">
 <h3>Claude Desktop / Cursor config</h3>
<pre>{{
  "mcpServers": {{
    "sorghum-protein": {{
      "url": "http://localhost:8765/mcp"
    }}
  }}
}}</pre>
</div>
</body></html>"""
    return HTMLResponse(html)


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request):
    idx = _index()
    return JSONResponse(
        {
            "ok": True,
            "service": "sorghum-protein-kb",
            "papers": idx.get("n_papers"),
            "figures": idx.get("total_figures"),
            "tables": idx.get("total_tables"),
            "mcp_path": "/mcp",
        }
    )


def main() -> None:
    host = os.environ.get("SORGHUM_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("SORGHUM_MCP_PORT", "8765"))
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "localhost",
            "localhost:8765",
            "127.0.0.1",
            "127.0.0.1:8765",
            "127.0.0.1:80",
            "host.docker.internal",
            "*",  # allow reverse-proxy Host variants; narrow this list for a public deployment
        ],
        allowed_origins=[
            "http://localhost",
            "http://127.0.0.1",
            "*",
        ],
    )
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        transport_security=transport_security,
    )


if __name__ == "__main__":
    main()
