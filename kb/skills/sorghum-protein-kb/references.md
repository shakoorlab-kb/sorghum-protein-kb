# sorghum-protein MCP tools

Load this only if you need a fuller tool map. The MCP server is `sorghum-protein`.

Endpoint: your MCP URL, for example `http://localhost:8765/mcp` (`scripts/run_mcp_http.py`), or the local STDIO server.

## Search and catalog

- `search_sorghum_protein(query_text, k=6)` — semantic chunks from PDFs/abstracts. Use k=8 for mechanisms. Returns titles/DOIs.
- `hybrid_search_sorghum_protein(query_text, k=6, graph_depth=1)` — preferred for mechanisms and relationships; returns semantic passages plus evidence-backed graph edges.
- `explore_knowledge_graph(entity, depth=1, relation="", limit=60)` — traverse typed nodes/edges and their evidence.
- `get_paper_graph(paper_query, limit=100)` — complete graph evidence for one structured paper.
- `get_project_knowledge()` — curated Shakoor Lab science (no timeline).
- `find_papers(keyword, downloaded_only=false, limit=15)` — catalog title/abstract keyword search.
- `search_structured_papers(keyword, limit=15)` — structured records → `paper_id`.
- `kb_stats()` — papers, figures, tables, PDFs.
- `list_undownloaded_papers(priority_only=true, limit=25)` — paywalled classics with links.

## Inventory

- `list_all_papers_tree(year_from=0, year_to=0, theme_filter="")` — **complete** inventory. Mandatory for “all papers / tree / how many”.
- `list_structured_papers(limit=30, year_from=0)` — sample only.

`theme_filter` values used by the server include coarse buckets such as protein, food, genetics, nutrition.

## One paper

Pass `paper_query` as `paper_id`, DOI, or unique title fragment.

- `get_paper(paper_query, include_full_sections=false)` — metadata, abstract, inventory, claims. Full sections only if needed.
- `get_paper_section(paper_query, section_name, max_chars=12000)` — `abstract`, `introduction`, `methods`, `results`, `discussion`, `conclusion`, `references`, `front_matter`.
- `get_paper_claims(paper_query)` — digestibility/kafirin/tannin/processing sentences.
- `get_paper_references(paper_query, limit=40)` — bibliography extracted from that PDF.

## Figures and tables

- `list_paper_figures` / `get_paper_figure(paper_query, figure_id, include_base64=false)` — `figure_id` like `fig_001` or `1`. Set `include_base64=true` when the user should see the image.
- `list_paper_tables` / `get_paper_table(paper_query, table_id, fmt=markdown)` — `table_id` like `table_001` or `1`. `fmt`: `markdown` | `csv` | `json`.

Figure counts are embedded PDF images, not always numbered journal figures.

## Ingest (maintainers)

- `add_paper(source, title_hint="", update_vector_index=true, update_graph_index=true)` — DOI, PDF URL, or absolute local `.pdf` path. Updates Chroma and the graph. Claude Desktop chat upload often does **not** give a filesystem path; prefer DOI or URL. Against a remote server, a "local path" is resolved **on the server**, not on the user's machine, so a laptop path like `/Users/.../Downloads/x.pdf` will fail; the file has to be copied to the server first.
- `reextract_paper(paper_query, update_vector_index=true, update_graph_index=true)` — redo structure extraction and refresh both indexes.
- `rebuild_structured_index()` — refresh index without re-parsing PDFs.
- `rebuild_knowledge_graph()` — refresh automatic graph facts from all structured papers while preserving LLM-reviewed facts.
- `prepare_graph_enrichment(paper_query)` — evidence packet and current graph for one paper.
- `add_graph_facts(paper_query, facts_json)` — persist reviewed typed relationships. Every fact needs source/target names and types, relation, evidence, and confidence.

## Typical multi-tool sequences

**Mechanism question**
1. `get_project_knowledge`
2. `hybrid_search_sorghum_protein` with a precise query (e.g. “wet cooking disulfide kafirin protein digestibility”)
3. `get_paper` or `get_paper_claims` on 2–3 top DOIs

**Add and enrich a paper**
1. `add_paper` with DOI, URL, or local path (vector + graph update automatically)
2. `get_paper_graph` to inspect automatic entities and relationships
3. `prepare_graph_enrichment` to read the evidence packet
4. `add_graph_facts` only for useful, supported, non-duplicate relationships

**Named paper + figure**
1. `search_structured_papers` or `get_paper` with the title/DOI
2. `list_paper_figures`
3. `get_paper_figure` with `include_base64=true`

**Full corpus list**
1. `list_all_papers_tree` (do not sample)
2. Report `n_papers` / year histogram from the JSON
