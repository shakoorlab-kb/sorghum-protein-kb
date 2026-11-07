# app: Sorghum Protein Intelligence (web app)

A web app over the knowledge base in [`../kb`](../kb): chat with the literature, explore a
knowledge graph, and check every answer against the original page. It runs alongside the MCP
server and reads the KB read-only. Setup is in [../docs/GUIDE.md](../docs/GUIDE.md#3-run-the-web-app).

## What it does

| View | |
|---|---|
| **Chat** | Questions answered from the corpus with numbered citations. Each citation opens the source page (the PDF page image with the supporting sentences highlighted) and the graph around the answer. |
| **Charts** | Ask for a plot ("plot protein against digestibility for the tannin accessions", "bar chart of raw vs cooked digestibility") and the answer carries a chart. Values come from a supplementary table (read straight from its rows, the model only picks columns) or from numbers in the cited evidence (any value not found in its source is dropped). Bar, grouped bar, scatter and line charts, with hover, table view and CSV. |
| **Graph** | Neo4j knowledge graph of genes, kafirins, traits, processes, compounds, germplasm and methods. Every edge is backed by claims, and every claim by a verbatim quote, page and passage. Neighbourhoods, paths between two entities, evidence for any link. |
| **Library** | All documents (KB papers and uploads), each with its claims, graph and page viewer. |
| **Ingest** | Upload PDF, DOCX or text files, add by DOI or URL, or sync the KB library. Live job progress. |
| **Overview** | Corpus and graph counts, entity and relation mix, model cost. |

## How it works

```
KB (read-only)  ─┐                                   ┌─ chat agent (LangGraph)
uploads/DOIs    ─┴─> pages ─> passages + embeddings ─┤    plan > link entities > retrieve
                              │       (Neo4j vector     │    (vector + full text + graph claims
                              │        + full text)     │     + paths) > answer > verify citations
                              └─> claims per page ──────┘
                                  (quote verified against the page)
```

* **Neo4j** holds everything searchable: `Document`, `Chunk` (page, text, embedding),
  `Entity`, `Claim`. Claims are the provenance layer (subject, relation, object, statement,
  context, verbatim evidence, page, chunk). `RELATES` edges aggregate claims for traversal.
  `CITES`, `AUTHORED_BY` and `PUBLISHED_IN` come from paper metadata and the KB graph.
* **Graph extraction** is done by Claude Code agents by default (`SI_EXTRACT_MODE=claude`),
  following [deploy/CLAUDE_EXTRACTION.md](deploy/CLAUDE_EXTRACTION.md) and the briefs in
  [claude_graph/](claude_graph/). See [../docs/AGENT_WORKFLOW.md](../docs/AGENT_WORKFLOW.md).
  With `SI_EXTRACT_MODE=openrouter`, `kg_extract.py` makes one model call per page with the
  same vocabulary, and each quote is searched for in the page text.
* **Chat** (`agent.py`) is a LangGraph state machine. Citations the model did not receive are
  removed, and for each cited passage the supporting sentences are picked and highlighted on
  the PDF.
* **Models**: any OpenAI-compatible endpoint (`SI_LLM_BASE_URL`, default OpenRouter). The
  default chat and extraction model is a fast tier, `anthropic/claude-haiku-5.5`
  (`SI_CHAT_MODEL`, `SI_EXTRACT_MODEL`), well under one cent per answer. Embeddings are local
  (`BAAI/bge-small-en-v1.5`, ONNX), so search sends nothing out.

## Commands

```bash
python -m sorghum_intel serve                 # API and console
python -m sorghum_intel sync [--no-extract]   # index the KB library (and extract when mode is openrouter)
python -m sorghum_intel stats
python -m sorghum_intel claude-plan claude_graph       # agent extraction batches
python -m sorghum_intel claude-task <doc_id>           # page dump for an agent
python -m sorghum_intel claude-check <files>           # quote and schema check
python -m sorghum_intel claude-entities claude_graph   # inventory for entity resolution
python -m sorghum_intel claude-import claude_graph [--reset]
python -m sorghum_intel graph-audit
```

## Deploy

`docker compose up -d --build` runs Neo4j and the app (port 8770 on localhost). Secrets live in
`secrets/` (git-ignored), as shown in the guide. `deploy/deploy.sh` syncs code to a Linux
server over SSH, creates the server secrets on the first run, rebuilds and checks health:

```bash
LAB_HOST=user@server OPENROUTER_API_KEY=... deploy/deploy.sh   # sync, secrets, build, start
LAB_HOST=user@server deploy/deploy.sh sync-kb                   # index the KB library on the server
LAB_HOST=user@server deploy/deploy.sh graph                     # ship agent extractions and import
LAB_HOST=user@server deploy/deploy.sh logs
```

The server owns its data (uploads, chats, the Neo4j volume). Deploys only replace code. Uploads
made in the app live in the app, not in the KB, so the MCP server does not see them.
