# Data availability

## In this repository

| What | Where | Notes |
|---|---|---|
| Code, agent instructions and tests | `kb/`, `app/` | MIT License |
| Bibliographic catalog of 986 works | `kb/metadata/paper_catalog.json` | Title, DOI, year, authors, open-access status and links from OpenAlex. Abstracts are left out |
| Audit report of the October 2026 graph build | `app/claude_graph/audit/report.json` | Counts only |
| Entity-resolution decisions | `app/claude_graph/canon/` | Entity names only |
| Example extraction files | `app/claude_graph/examples/` | Three open-access papers published under CC BY 4.0: Weerasooriya et al. 2018, PLOS ONE, [10.1371/journal.pone.0203005](https://doi.org/10.1371/journal.pone.0203005), and two MDPI articles, [10.3390/foods10020407](https://doi.org/10.3390/foods10020407) and [10.3390/plants12081662](https://doi.org/10.3390/plants12081662) |
| Screenshots | `docs/images/` | Taken from the lab deployment. Visible page images are from CC BY articles |

## Not in this repository, and why

| What | Why | How to get it |
|---|---|---|
| Paper PDFs, page text, OCR and indexes | Publisher copyright, and size | Rebuild them from open-access sources with `kb/scripts/download_papers.py` and `ingest_paper.py` |
| The full set of extracted claims | Each claim quotes a passage from a publication, and not all of those publications are openly licensed | Rebuild with the agent workflow in [AGENT_WORKFLOW.md](AGENT_WORKFLOW.md) |
| The lab's NIR grain-quality and field-trial data | Unpublished research data | Not shared. The lab-data tools work with your own workbooks in `kb/lab_data/` |
| Deployment secrets and server configuration | Security | Create your own as shown in [GUIDE.md](GUIDE.md) |

## Quotations

Claims store short verbatim quotes as evidence, with the source page and DOI, so that every
statement can be checked against its source. When you rebuild the graph, the quotes come from
the documents you add, and their reuse is governed by each document's license.
