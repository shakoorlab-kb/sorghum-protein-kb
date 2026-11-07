# Guide: build, connect and run

This guide takes you from a fresh clone to a working knowledge base, connected to your AI
assistant, with the optional web app and an agent-built knowledge graph.

| Step | You get | Needs |
|---|---|---|
| [1. Build the knowledge base](#1-build-the-knowledge-base) | Page text, OCR, tables, figures, exact and semantic search, a rule-based graph | Python 3.12, internet for downloads |
| [2. Connect your assistant](#2-connect-your-assistant-mcp) | 40 tools in Claude, Claude Code, Cursor or ChatGPT | Step 1 |
| [3. Run the web app](#3-run-the-web-app) | Cited chat, graph explorer, charts, library | Docker, an OpenRouter or compatible API key for chat |
| [4. Build the graph with agents](#4-build-the-knowledge-graph-with-agents) | Evidence-linked claims checked against each page | Claude Code |

## 1. Build the knowledge base

```bash
cd sorghum-protein-kb/kb
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
export KB_CONTACT_EMAIL=you@example.org   # sent to OpenAlex, Crossref and Unpaywall as a polite-use contact
export ANONYMIZED_TELEMETRY=False         # turn off Chroma telemetry
```

The first semantic-index build downloads a small embedding model (MiniLM, about 80 MB).

### Add papers

```bash
# one paper by DOI, PDF URL or local PDF: downloads, extracts, and updates every index
.venv/bin/python scripts/ingest_paper.py 10.1371/journal.pone.0203005
.venv/bin/python scripts/ingest_paper.py /path/to/paper.pdf

# the open-access works in metadata/paper_catalog.json (986 catalogued works)
.venv/bin/python scripts/download_papers.py

# open-access supplementary files from Europe PMC, or one by hand
.venv/bin/python scripts/supplements.py fetch-all
.venv/bin/python scripts/supplements.py add <paper_id> <file-or-URL>
```

Paywalled papers can be added from a PDF you hold under your own institutional access. Keep
them out of any public copy (see [DATA.md](DATA.md)).

### Rebuild all indexes after a batch of PDFs

```bash
cd scripts
../.venv/bin/python extract_paper_structure.py   # pages + OCR, sections, tables, figures (--force redoes all)
../.venv/bin/python build_knowledge_base.py      # semantic index (Chroma)
../.venv/bin/python text_index.py build          # exact-text index (SQLite FTS5 trigram)
../.venv/bin/python build_knowledge_graph.py     # rule-based graph, keeps LLM-reviewed facts
```

### Search from the command line

```bash
.venv/bin/python scripts/query_kb.py "Why does wet cooking reduce sorghum protein digestibility?"
.venv/bin/python scripts/query_kb.py -k 10 --json "tannin testa protein digestibility"
.venv/bin/python scripts/text_index.py search "PI 533919 IS 12615"   # identifiers and numbers
```

Semantic search finds meaning but misses identifiers. Use exact search for accession numbers,
gene IDs, DOIs and numeric values. It tolerates `PI533919`, `PI 533919` and `PI-533919`.

### Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

## 2. Connect your assistant (MCP)

The MCP server exposes 40 tools: exact, semantic and hybrid graph search, paper sections,
tables, figures and supplements, graph exploration, adding papers from chat, and
evidence-reviewed graph enrichment. The full list is in [kb/MCP_SETUP.md](../kb/MCP_SETUP.md).

Replace `/path/to/sorghum-protein-kb` with the absolute path of your clone.

**Claude Code**

```bash
claude mcp add sorghum-protein -- /path/to/sorghum-protein-kb/kb/.venv/bin/python \
  /path/to/sorghum-protein-kb/kb/scripts/sorghum_protein_mcp.py
```

**Claude Desktop**: merge [kb/mcp_server/claude_desktop.snippet.json](../kb/mcp_server/claude_desktop.snippet.json)
into `claude_desktop_config.json` under `mcpServers`, then quit and reopen Claude.

**Cursor**: add the same `command` and `args` to `.cursor/mcp.json` in your project.

**ChatGPT Desktop and Codex**: add [kb/mcp_server/chatgpt_desktop.snippet.toml](../kb/mcp_server/chatgpt_desktop.snippet.toml)
to `~/.codex/config.toml`, or add a STDIO server under Settings, MCP Servers.

**Shared server over HTTP**: run `scripts/run_mcp_http.py` (streamable HTTP on port 8765,
path `/mcp`) behind a reverse proxy with TLS and authentication.
[kb/deploy/sorghum-protein-mcp.service](../kb/deploy/sorghum-protein-mcp.service) is a systemd
unit for it. Narrow `allowed_hosts` in `run_mcp_http.py` to your host names.

**Skill**: [kb/skills/sorghum-protein-kb](../kb/skills/sorghum-protein-kb/) tells the assistant
which tool to use for which question and how to cite. Install it as a Claude skill.

Try:

- *How do wet cooking, disulfide bonds and kafirin protein bodies jointly reduce protein digestibility?*
- *Find every mention of PI 533919 across pages, tables and supplements.*
- *Add DOI 10.3390/foods10020407, then prepare a graph-enrichment packet for it.*

## 3. Run the web app

The app reads the knowledge base from step 1 (mounted read-only) and keeps its own data:
uploads, chats and the Neo4j graph.

```bash
cd sorghum-protein-kb/app
mkdir -p secrets && chmod 700 secrets
NEO4J_PW=$(openssl rand -hex 18)
cat > secrets/secrets.env <<EOF
OPENROUTER_API_KEY=<your key>
NEO4J_PASSWORD=$NEO4J_PW
SI_ADMIN_USER=admin
SI_ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)
SI_SESSION_SECRET=$(openssl rand -hex 32)
EOF
echo "NEO4J_AUTH=neo4j/$NEO4J_PW" > secrets/neo4j.env
chmod 600 secrets/*.env

docker compose up -d --build                                     # Neo4j + app on http://127.0.0.1:8770/
docker exec sorghum-intel python -m sorghum_intel sync --no-extract   # index the KB library for search
```

Sign in with `admin` and the password in `secrets/secrets.env`. Chat and automatic extraction
call the model through `SI_LLM_BASE_URL` (default OpenRouter). Set `SI_CHAT_MODEL` and
`SI_EXTRACT_MODEL` to choose models. Embeddings run locally (`BAAI/bge-small-en-v1.5`).

For remote use, put the app behind a reverse proxy with TLS. Never expose Neo4j (ports 7474 and
7687 are bound to 127.0.0.1).

`deploy/deploy.sh` syncs code to a Linux server over SSH and creates the server secrets on the
first run (`LAB_HOST=user@server OPENROUTER_API_KEY=... deploy/deploy.sh`).

**Local development** without Docker for the app:

```bash
docker run -d --name sintel-neo4j-dev -p 127.0.0.1:47474:7474 -p 127.0.0.1:47687:7687 \
  -e NEO4J_AUTH=neo4j/devpassword123 neo4j:5.26-community
python3.12 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cp dev.env.example dev.env            # edit SI_KB_DIR and passwords
export OPENROUTER_API_KEY=...
./run_dev.sh                          # API on :8770
cd frontend && npm install && npm run dev   # console on :5173, proxies /api to :8770
```

## 4. Build the knowledge graph with agents

Graph extraction runs as Claude Code agents, not as API calls from the app
(`SI_EXTRACT_MODE=claude`, the default). The short version:

```bash
cd sorghum-protein-kb/app && source dev.env
.venv/bin/python -m sorghum_intel claude-plan claude_graph            # batches.json
# start one Claude Code agent per batch with claude_graph/AGENT_BRIEF.md
.venv/bin/python -m sorghum_intel claude-check claude_graph/extractions/*.json
.venv/bin/python -m sorghum_intel claude-entities claude_graph        # inventory for entity resolution
# entity-resolution agents (CANON_BRIEF.md), then:
.venv/bin/python claude_graph/tools/merge_canon.py
# audit agents (AUDIT_BRIEF.md), then:
.venv/bin/python claude_graph/tools/apply_audit.py
.venv/bin/python -m sorghum_intel claude-import claude_graph --reset
.venv/bin/python -m sorghum_intel graph-audit
```

[AGENT_WORKFLOW.md](AGENT_WORKFLOW.md) explains each stage, the quality gates and the measured
cost. To extract automatically through the API instead, set `SI_EXTRACT_MODE=openrouter`.

## 5. Lab-data tools (optional)

Eight MCP tools read the lab's own NIR grain-quality workbooks and field-trial sheets
(`scripts/lab_data.py`, `scripts/field_trials.py`). These data are not public. To use the tools
with your own data, put workbooks in `kb/lab_data/` and run `python scripts/lab_data.py build`.
Without that folder the tools say that no lab datasets are built.

## Troubleshooting

| Problem | Fix |
|---|---|
| Unpaywall or Crossref lookups fail | Set `KB_CONTACT_EMAIL` to a real address |
| `query_kb.py` finds no collection | Run `build_knowledge_base.py` or ingest a paper first |
| An identifier search misses | Use `text_index.py search` or the `exact_search_sorghum_protein` tool |
| MCP tools do not appear | Use absolute paths in the config, then fully quit and reopen the client |
| The web app shows no graph | Run the agent build in step 4, or `sync` with `SI_EXTRACT_MODE=openrouter` |
