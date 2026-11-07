# Connect the sorghum protein MCP server

This local MCP server lets **Cursor**, **Claude Desktop**, **ChatGPT Desktop**, and other MCP clients query a
hybrid sorghum protein knowledge base: semantic vectors plus an evidence-backed property
graph, structured papers, figures, and tables.

## Tools

### Search & project
| Tool | What it does |
|------|----------------|
| `search_sorghum_protein` | Semantic search over ~15k literature chunks |
| `hybrid_search_sorghum_protein` | Preferred search: semantic passages + graph relationships |
| `get_project_knowledge` | Curated project science (no timeline) |
| `find_papers` | Catalog search with DOI + PDF/link status |
| `list_undownloaded_papers` | Paywalled papers with links |
| `kb_stats` | Corpus + structured extraction stats |

### Knowledge graph
| Tool | What it does |
|------|----------------|
| `explore_knowledge_graph` | Traverse entities and evidence-backed relationships |
| `get_paper_graph` | Show the complete graph extracted from one paper |
| `prepare_graph_enrichment` | Give Claude an evidence packet for deeper relationship extraction |
| `add_graph_facts` | Save Claude-reviewed facts, each with evidence and confidence |
| `rebuild_knowledge_graph` | Refresh the graph from all structured papers |

### Structured paper components
| Tool | What it does |
|------|----------------|
| `list_structured_papers` | Sample list (default 30) — not the full inventory |
| `list_all_papers_tree` | **Complete** JSON inventory of all papers (use for “all papers / tree”) |
| `search_structured_papers` | Keyword search → `paper_id` |
| `get_paper` | Full metadata card (title, authors, DOI, abstract, inventory) |
| `get_paper_section` | One section: methods / results / discussion / … |
| `list_paper_figures` / `get_paper_figure` | Figure list + image path (optional base64) |
| `list_paper_tables` / `get_paper_table` | Tables as markdown / csv / json |
| `get_paper_references` | Bibliography entries |
| `get_paper_claims` | Key digestibility/protein claim sentences |

### Resources
- `sorghum://papers/index`
- `sorghum://paper/{paper_id}`
- `sorghum://paper/{paper_id}/figure/{figure_id}`
- `sorghum://paper/{paper_id}/table/{table_id}`

### Add / maintain papers from chat
| Tool | What it does |
|------|----------------|
| `add_paper` | Ingest by DOI, PDF URL, or local `.pdf` path → extract + vector and graph upsert |
| `reextract_paper` | Re-run structure extraction and refresh vector/graph records |
| `rebuild_structured_index` | Refresh `index.json` without re-parsing PDFs |

### Example: add a paper in chat
- Add this paper by DOI: `10.3390/foods15101681`
- Add this PDF: `/path/to/some_sorghum_paper.pdf`
- Re-extract figures/tables for the HD mutant PNAS paper

## Example chat prompts

- List structured papers about kafirin
- Open the highly digestible sorghum mutant PNAS paper and list its figures
- Show table 1 from the African fermented sorghum foods review
- Get the methods section for DOI 10.3390/nu12041111
- Add paper DOI 10.1073/pnas.81.1.1 to the knowledge base
- Why does cooking reduce protein digestibility? (uses semantic search)
- How are fermentation, tannins, and kafirin digestibility connected? (uses hybrid search)
- Enrich the knowledge graph for the paper I just added (Claude reviews evidence, then adds facts)

## 1. Cursor

Add `sorghum-protein` to `.cursor/mcp.json` in your project (or `~/.cursor/mcp.json`) with the same
`command` and `args` as the Claude Desktop entry below.

1. Quit and reopen Cursor
2. Settings → MCP → confirm **sorghum-protein** is green
3. Ask questions; approve tool calls as needed

## 2. Claude Desktop

Use the local stdio server so Claude reads the local Chroma and graph files
directly. Merge this entry under `mcpServers` in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "sorghum-protein": {
      "command": "/path/to/sorghum-protein-kb/kb/.venv/bin/python",
      "args": [
        "/path/to/sorghum-protein-kb/kb/scripts/sorghum_protein_mcp.py"
      ]
    }
  }
}
```

1. Fully quit Claude Desktop (Cmd+Q) and reopen
2. Enable **sorghum-protein** tools
3. Chat normally

For a shared server, run `scripts/run_mcp_http.py` behind a reverse proxy and point clients at
its `/mcp` URL. Cursor accepts a direct `"url"` entry.

## 3. ChatGPT Desktop (local MCP)

ChatGPT Desktop can run the same local stdio server, so it uses the Chroma vector database
and evidence-backed graph on your machine. This setup is for the **desktop app**; ChatGPT
on the web does not read local MCP configuration files.

1. Open **ChatGPT Desktop → Settings → MCP Servers**.
2. Select **Add Server**.
3. Name it `sorghum-protein`, select **STDIO**, then enter:

   - **Command:** `/path/to/sorghum-protein-kb/kb/.venv/bin/python`
   - **Arguments:** `/path/to/sorghum-protein-kb/kb/scripts/sorghum_protein_mcp.py`

4. Save the server and select **Restart**.
5. In a new ChatGPT Desktop conversation, type `/mcp` to confirm that `sorghum-protein` is connected.

Alternatively, add the reusable config from
`mcp_server/chatgpt_desktop.snippet.toml` to `~/.codex/config.toml`. ChatGPT Desktop, Codex
CLI, and the Codex IDE extension share that local Codex MCP configuration.

Example prompts:

- How do wet cooking, disulfide bonds, and kafirin protein bodies jointly reduce protein digestibility?
- Show the evidence-backed relationship path from fermentation to reduced tannins and improved protein digestibility.
- Add this paper DOI to the knowledge base, then prepare a graph-enrichment packet for it.

See the official OpenAI [MCP documentation](https://learn.chatgpt.com/docs/extend/mcp) for
current ChatGPT Desktop availability and MCP settings.

## Rebuild structured extraction (after adding PDFs)

```bash
cd /path/to/sorghum-protein-kb/kb
source .venv/bin/activate
python scripts/extract_paper_structure.py          # skip already-done
python scripts/extract_paper_structure.py --force  # redo all
python scripts/build_knowledge_base.py             # refresh vector index
python scripts/build_knowledge_graph.py             # refresh relationship graph
```

Structured output lives in `knowledge_base/structured/<paper_id>/`:

```
paper.json      # full structured record
card.json       # compact metadata
fulltext.txt
figures/fig_001.png (+ .json)
tables/table_001.md|.csv|.json
pages/page_001.txt
```

The graph is stored at `knowledge_base/graph/knowledge_graph.sqlite3`. It uses SQLite so
local Claude needs no Neo4j daemon, Docker container, credentials, or additional package.
Each scientific edge stores its source paper, evidence, confidence, and extraction method.

Then restart Cursor, Claude Desktop, or ChatGPT Desktop so the MCP process reloads.
