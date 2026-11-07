# kb: literature knowledge base and MCP server

Literature corpus and agent-queryable knowledge base for **sorghum protein bioavailability**:
kafirins, cooked and fermented digestibility, grain-quality traits, and genetic targets for
food sorghum. Setup steps are in [../docs/GUIDE.md](../docs/GUIDE.md).

## What is here

| Path | Contents |
|------|----------|
| `scripts/ingest_paper.py` | Add one paper by DOI, PDF URL or local PDF and update every index |
| `scripts/download_papers.py` | Search and download open-access literature into `papers/` |
| `scripts/extract_paper_structure.py`, `page_text.py` | Per-page text with OCR, sections, tables, figures, references |
| `scripts/build_knowledge_base.py`, `query_kb.py` | Semantic index (Chroma) and command-line search |
| `scripts/text_index.py` | Exact-text index for identifiers and numbers (SQLite FTS5 trigram) |
| `scripts/knowledge_graph.py`, `build_knowledge_graph.py` | SQLite property graph with evidence on every edge |
| `scripts/supplements.py` | Supplementary files: fetch, extract every sheet and row, index |
| `scripts/sorghum_protein_mcp.py` | MCP server (STDIO), 40 tools |
| `scripts/run_mcp_http.py` | The same server over streamable HTTP |
| `scripts/lab_data.py`, `field_trials.py` | Parsers for lab NIR workbooks and field-trial sheets (data not included) |
| `metadata/paper_catalog.json` | Bibliographic catalog of 986 works (no abstracts) |
| `skills/sorghum-protein-kb/` | Claude skill for using the tools and citing sources |
| `MCP_SETUP.md` | Tool list and client setup |
| `tests/` | Unit tests (`python -m unittest discover -s tests`) |

Built data (`papers/`, `knowledge_base/`) is created locally by the scripts and is git-ignored.

## Structured papers (figures, tables, sections)

Every PDF is extracted into `knowledge_base/structured/<paper_id>/` with:

- bibliographic metadata (title, authors, year, DOI, keywords, abstract)
- section text (introduction, methods, results, discussion, references)
- **figures** as PNG with captions
- **tables** as Markdown, CSV and JSON (plus captions when the grid parse fails)
- key claims and the reference list
- **every page's text** in `pages/page_NNN.txt`: the PDF text layer, OCR of scanned pages, and
  OCR of tables and figures saved as images (OCR is cached in `knowledge_base/ocr_cache/`)

Rebuild everything:

```bash
cd scripts
../.venv/bin/python extract_paper_structure.py --force   # pages + OCR, sections, tables, figures
../.venv/bin/python build_knowledge_base.py              # semantic (Chroma) index
../.venv/bin/python text_index.py build                  # exact-text index
../.venv/bin/python build_knowledge_graph.py             # graph (keeps LLM-reviewed facts)
```

`add_paper` (MCP) or `ingest_paper.py` updates all of these for a single paper.

## Exact search for accessions, IDs and numbers

Semantic search matches meaning and cannot reliably find identifiers. The exact-text index
(`knowledge_base/text_index.sqlite3`) holds the complete text of every page, every table row,
figure text, reference lists, and the titles of catalogued papers without a PDF.

```bash
../.venv/bin/python text_index.py search "PI 533919 IS 12615 SC 124"
```

MCP tool: `exact_search_sorghum_protein`. It tolerates `PI533919`, `PI 533919` and `PI-533919`,
reports number-only matches (tables that put "PI" only in the header), and states what was
searched. `search_sorghum_protein` and `hybrid_search_sorghum_protein` add exact matches
automatically when a question contains an identifier.

## Supplementary files

Per-line data (for example the protein of every panel accession) usually sits in supplements,
not in the article. `scripts/supplements.py` stores them in `papers/supplements/<paper_id>/`,
extracts every sheet and row (xlsx, csv, tsv, docx, pdf, txt), and indexes them for exact and
semantic search.

```bash
../.venv/bin/python supplements.py fetch-all                 # open-access supplements via Europe PMC
../.venv/bin/python supplements.py fetch 10.1186/s12864-016-3403-x
../.venv/bin/python supplements.py add <paper> <file-or-URL>  # add by hand
```

MCP tool: `add_supplement` (an empty `source` fetches from PubMed Central). Supplements survive
re-extraction of the paper.

## Graph enrichment by an assistant

The rule-based graph links entities that co-occur with relation cues. An assistant can go
further: `prepare_graph_enrichment` returns the evidence for a paper, the assistant reviews it,
and `add_graph_facts` stores only relationships backed by a quote (`extraction_method:
llm-reviewed`). The agent-built graph used by the web app is described in
[../docs/AGENT_WORKFLOW.md](../docs/AGENT_WORKFLOW.md).

## Scientific focus

- **Kafirins** (alpha, beta, gamma, delta prolamins), protein bodies, disulfide cross-linking
- **Digestibility drop after wet cooking** compared with raw grain
- **Tannins and polyphenols**, vitreous endosperm, the protein-starch matrix
- **Processing**: fermentation, germination and malting, extrusion, reducing agents
- **Highly digestible (HD) mutants** and altered protein-body morphology
- **Amino acid quality** (lysine limitation) and total protein
- **Breeding and gene-editing targets** for food sorghum
