#!/usr/bin/env python3
"""Sorghum protein knowledge MCP server (stdio).

Serves:
  - semantic literature search (Chroma)
  - curated project knowledge
  - structured per-paper metadata, sections, figures, tables, references
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY_DISABLED", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from mcp.server import MCPServer  # noqa: E402
from query_kb import format_context, query  # noqa: E402
import structured_store as store  # noqa: E402
import ingest_paper as ingest  # noqa: E402
from knowledge_graph import KnowledgeGraph, hybrid_graph_context  # noqa: E402
import text_index  # noqa: E402
import lab_data  # noqa: E402
import field_trials  # noqa: E402

KB = ROOT / "knowledge_base"
META = ROOT / "metadata"
PAPERS = ROOT / "papers"
STRUCTURED = KB / "structured"

mcp = MCPServer(
    name="sorghum-protein",
    title="Sorghum Protein Knowledge",
    description=(
        "Local research knowledge base for sorghum protein bioavailability: "
        "kafirins, cooked/fermented digestibility, tannins, HD mutants, "
        "gene-editing targets, hybrid vector search, evidence-backed knowledge graph, "
        "plus structured paper metadata, figures, and tables. "
        "Can ingest new papers by DOI, URL, or local PDF path."
    ),
    instructions=(
        "For accession/line IDs (PI, IS numbers), cultivar names, gene IDs, SNP IDs, DOIs or exact "
        "numbers: call exact_search_sorghum_protein first. It searches every page (text layer + OCR of "
        "scanned pages and image tables), every table row and figure text; semantic search cannot "
        "match identifiers reliably. Report its coverage line when nothing is found. "
        "For scientific questions involving mechanisms, relationships, genes, traits, "
        "or interventions: call hybrid_search_sorghum_protein. Use search_sorghum_protein "
        "when only ranked passages are needed. "
        "When the user asks for ALL papers, a tree, inventory, or full list: "
        "ALWAYS call list_all_papers_tree (never invent a partial list). "
        "For a specific paper's metadata/sections/figures/tables: "
        "use list_structured_papers, get_paper, get_paper_section, "
        "list_paper_figures, get_paper_figure, list_paper_tables, get_paper_table. "
        "To find text inside figures (OCR/labels), tables, captions, or numeric facts: "
        "call search_paper_components. Use get_paper_coverage to see what is still missing. "
        "Supplementary tables are searchable when attached; add them with add_supplement "
        "(empty source fetches open-access supplements from PubMed Central). "
        "To add a new paper into the KB from chat: call add_paper with a DOI, "
        "PDF URL, or local .pdf path; it updates both vector and graph indexes. "
        "For deeper LLM-assisted graph enrichment, call prepare_graph_enrichment, inspect "
        "the evidence, then call add_graph_facts with evidence-backed relationships. "
        "Use get_project_knowledge for Shakoor Lab goals. "
        "The lab's OWN measurements (NIR grain protein, lysine, starch, amylose and fat of SAP and Danforth "
        "diversity samples) are lab datasets, not papers: use list_lab_datasets, get_lab_dataset (notes, "
        "calibration, QC rule, summaries, data issues), query_lab_data, lab_genotype_profile (lab values plus "
        "literature mentions of the same accession), compare_lab_traits, compare_lab_datasets and compare_lab_with_literature (lab vs published values for shared accessions). Field trials (plot agronomy: flowering, height, lodging, tillers, stem diameter, biomass) use query_field_data, compare_field_traits and compare_nir_with_field. Report the "
        "QC band (Mahalanobis distance) with lab values and leave out 'unreliable' and 'invalid' samples unless "
        "asked; say which dataset and location each value comes from. "
        "Cite titles and DOIs. figure counts are embedded images, not always captioned figures."
    ),
    version="3.0.0",
)


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).replace("&amp;", "&").strip()


def _load_catalog() -> list[dict]:
    path = META / "paper_catalog.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _downloaded_names() -> set[str]:
    return {p.name for p in PAPERS.glob("*.pdf")}


def _resolve_or_message(paper_query: str) -> tuple[str | None, str | None]:
    pid = store.resolve_paper_id(paper_query)
    if not pid:
        return None, (
            f"Could not resolve paper {paper_query!r}. "
            "Try list_structured_papers or search_structured_papers first "
            "(use paper_id, DOI, or a unique title fragment)."
        )
    return pid, None


# ---------------------------------------------------------------------------
# Semantic + project tools
# ---------------------------------------------------------------------------


def _exact_section(query_text: str) -> str:
    """Exact-text matches for identifier queries, appended to semantic results."""
    if not text_index.has_identifier(query_text):
        return ""
    try:
        return "\n\n## Exact text matches (identifiers)\n" + text_index.format_results(
            text_index.search(query_text), max_per_term=6
        )
    except Exception as exc:
        return f"\n\n## Exact text matches\nunavailable: {exc}"


@mcp.tool(
    name="exact_search_sorghum_protein",
    description=(
        "Exact/substring search over the COMPLETE text of the KB: every page (text layer plus OCR "
        "of scanned pages and of tables/figures saved as images), every table row, figure text, "
        "reference lists, and titles/abstracts of catalogued papers without a PDF. Use for "
        "accession or line IDs (e.g. 'PI 533919', 'IS 11167', several at once), cultivar names, "
        "gene/SNP IDs, DOIs and exact numbers. Tolerates 'PI533919' / 'PI 533919' / 'PI-533919' "
        "and also reports number-only matches (tables that put 'PI' only in the header). "
        "Returns page-level snippets and a coverage line stating exactly what was searched."
    ),
)
def exact_search_sorghum_protein(query_text: str, limit: int = 20) -> str:
    limit = max(1, min(int(limit), 50))
    return text_index.format_results(text_index.search(query_text, limit=limit), max_per_term=limit)


@mcp.tool(
    name="search_sorghum_protein",
    description=(
        "Semantic search over the sorghum protein literature knowledge base "
        "(PDFs, abstracts, project notes). Best for open scientific questions "
        "about kafirins, digestibility, cooking/fermentation, tannins, mutants, "
        "amino acids, and gene editing. Returns ranked passages with titles/DOIs."
    ),
)
def search_sorghum_protein(query_text: str, k: int = 6) -> str:
    k = max(1, min(int(k), 12))
    hits = query(query_text, k=k)
    exact = _exact_section(query_text)
    if not hits:
        return ("No matching passages found." + exact) if exact else "No matching passages found."
    return f"Query: {query_text}\n\n" + format_context(hits) + exact


@mcp.tool(
    name="hybrid_search_sorghum_protein",
    description=(
        "Preferred search for scientific questions involving relationships or mechanisms. "
        "Combines semantic vector passages with evidence-backed graph connections among "
        "papers, genes, proteins, traits, compounds, processing methods, and phenotypes."
    ),
)
def hybrid_search_sorghum_protein(
    query_text: str,
    k: int = 6,
    graph_depth: int = 1,
) -> str:
    k = max(1, min(int(k), 12))
    graph_depth = max(1, min(int(graph_depth), 2))
    hits = query(query_text, k=k)
    graph = KnowledgeGraph()
    graph_result = hybrid_graph_context(query_text, hits, depth=graph_depth)
    vector_text = format_context(hits) if hits else "No matching vector passages found."
    graph_text = graph.format_connections(graph_result)
    matched = [f"{n['name']} ({n['type']})" for n in graph_result.get("matched_nodes") or []]
    matched_text = ", ".join(matched[:10]) or "none"
    return (
        f"Hybrid query: {query_text}\n\n"
        f"## Semantic evidence\n{vector_text}\n\n"
        f"## Knowledge graph\nMatched entities: {matched_text}\n{graph_text}"
        f"{_exact_section(query_text)}\n\n"
        "Synthesize only claims supported by the passages or relationship evidence; "
        "cite paper titles/DOIs when available."
    )


@mcp.tool(
    name="get_project_knowledge",
    description=(
        "Return curated Shakoor Lab sorghum-protein project knowledge: "
        "goals, kafirin biology, digestibility mechanisms, processing interventions, "
        "and phenotyping/gene-target framework. Does not include project timeline."
    ),
)
def get_project_knowledge() -> str:
    path = KB / "PROJECT_KNOWLEDGE.md"
    if path.exists():
        return path.read_text()
    return "Project knowledge file is missing. Rebuild with scripts/build_knowledge_base.py."


@mcp.tool(
    name="kb_stats",
    description="Return knowledge-base size, structured paper counts, figures/tables totals, and PDF count.",
)
def kb_stats() -> str:
    info = {}
    info_path = KB / "kb_info.json"
    if info_path.exists():
        info = json.loads(info_path.read_text())
    idx = store.load_index()
    graph_info = KnowledgeGraph().stats()
    return json.dumps(
        {
            "backend": info.get("backend"),
            "embedding_model": info.get("embedding_model"),
            "n_documents": info.get("n_documents"),
            "n_chunks": info.get("n_chunks"),
            "pdf_count": len(list(PAPERS.glob("*.pdf"))),
            "catalog_records": len(_load_catalog()),
            "structured_papers": idx.get("n_papers", 0),
            "structured_figures": idx.get("total_figures", 0),
            "structured_tables": idx.get("total_tables", 0),
            "graph": graph_info,
            "lab_datasets": [{"id": ds["id"], "samples": ds["n_samples"], "qc": ds["qc_counts"]} for ds, _ in lab_data.load_all()],
            "project_root": str(ROOT),
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Lab datasets: the lab's own measurements (scripts/lab_data.py)
# ---------------------------------------------------------------------------
def _no_lab() -> str:
    return "No lab datasets are built. Put workbooks in lab_data/ and run: python scripts/lab_data.py build"


@mcp.tool(
    name="list_lab_datasets",
    description=(
        "List the lab's own measurement datasets (NIR-predicted grain protein, lysine, starch, amylose and "
        "fat of SAP and Danforth diversity samples): samples, genotypes, locations, traits, QC counts "
        "(from the Mahalanobis distance of each NIR prediction) and the number of data issues found."
    ),
)
def list_lab_datasets() -> str:
    rows = lab_data.load_all()
    trials = field_trials.load_all()
    if not rows and not trials:
        return _no_lab()
    lines = ["Lab datasets (the lab's own samples; not literature). NIR grain composition:"]
    for ds, _ in rows:
        locs = ", ".join(f"{k} {v}" for k, v in ds["locations"].items())
        qc = ", ".join(f"{k} {v}" for k, v in ds["qc_counts"].items())
        lines.append(
            f"- {ds['title']} [{ds['id']}]: {ds['n_samples']} samples, {ds['n_scans']} scans, "
            f"{ds['n_genotypes']} genotypes; locations: {locs}; traits: {', '.join(ds['traits'])}; "
            f"QC: {qc}; {len(ds['issues'])} data issue(s); file {ds['source_file']}"
        )
    if trials:
        lines.append("Field trials (per-plot agronomic traits):")
        for ds, _ in trials:
            lines.append(f"- {ds['title']} [{ds['id']}]: {ds['site']}, {ds['year']}; {ds['n_plots']} plots "
                         f"({ds['n_plots_with_data']} with data), {ds['n_genotypes']} genotypes; traits: {', '.join(ds['traits'])}"
                         + (f"; text traits: {', '.join(ds['text_traits'])}" if ds["text_traits"] else "")
                         + f"; {len(ds['issues'])} data issue(s); file {ds['source_file']}")
        lines.append("Plot IDs link the trials to the NIR samples: Danforth 2025 NIR = HPI 2025 plots; HPI-SAP NIR = HPI "
                     "(Missouri) and South Carolina plots, season not recorded.")
    lines.append("Use get_lab_dataset for notes, calibration statistics, summaries and issues.")
    return "\n".join(lines)


@mcp.tool(
    name="get_lab_dataset",
    description=(
        "Full card of one lab dataset: description, notes from the NIR lab, calibration-model statistics "
        "(N, R2, RMSECV, range), the QC rule, trait summaries (all usable samples, by location, good-only), "
        "trait correlations, agreement of the same genotypes between locations, and every data issue found "
        "when the workbook was parsed (swapped scans, negative predictions, text in number cells, layout)."
    ),
)
def get_lab_dataset(dataset: str = "") -> str:
    rows = lab_data.resolve_dataset(dataset)
    trials = field_trials.resolve(dataset)
    if dataset and not [r for r in rows if dataset.lower() in r[0]["id"].lower() or dataset.lower() in r[0]["title"].lower()]:
        rows = []
    if not rows and not trials:
        return _no_lab() if not lab_data.load_all() else f"No lab dataset matches {dataset!r}; see list_lab_datasets."
    return "\n\n".join([lab_data.card_text(ds) for ds, _ in rows] + [field_trials.card_text(ds) for ds, _ in trials])


@mcp.tool(
    name="query_lab_data",
    description=(
        "Filter and sort samples of the lab datasets. Filters: genotype ('PI 533919' or a name fragment), "
        "sample or plot ID, dataset, location ('Missouri'), qc ('good'; 'usable' = good+usable, the default; "
        "'unreliable'; 'invalid'; 'all'), and a trait with min_value/max_value. Traits: protein, lysine_grain, "
        "lysine_protein (lysine share of protein), starch, amylose_grain, amylose_starch, fat. sort_by takes a "
        "trait. Returns a markdown table with average Mahalanobis distance, QC band and flags; include_reps "
        "adds each replicate scan. fmt='csv' returns compact CSV of all matching samples (up to 2000 rows), "
        "for plotting or statistics."
    ),
)
def query_lab_data(
    genotype: str = "",
    sample: str = "",
    dataset: str = "",
    location: str = "",
    qc: str = "usable",
    trait: str = "",
    min_value: float | None = None,
    max_value: float | None = None,
    sort_by: str = "",
    descending: bool = True,
    limit: int = 25,
    include_reps: bool = False,
    fmt: str = "table",
) -> str:
    if not lab_data.load_all():
        return _no_lab()
    if (trait and not lab_data.trait_key(trait)) or (sort_by and not lab_data.trait_key(sort_by)):
        return f"Unknown trait. Use one of: {', '.join(lab_data.TRAITS)}"
    rows = lab_data.find_samples(genotype, sample, dataset, location, qc, trait, min_value, max_value, sort_by, descending)
    csv_out = (fmt or "").lower() == "csv"
    limit = max(1, min(int(limit), 2000 if csv_out else 200))
    if csv_out and limit == 25:
        limit = 2000
    head = (f"{len(rows)} sample(s) match (qc={qc or 'usable'}"
            + "".join(f", {k}={v}" for k, v in [("genotype", genotype), ("sample", sample), ("dataset", dataset),
                                                 ("location", location), ("trait", trait), ("min", min_value),
                                                 ("max", max_value), ("sort_by", sort_by)] if v not in ("", None)) + ").")
    if not rows:
        return head + " Try qc='all' to include unreliable and invalid NIR predictions."
    if csv_out:
        units = "; ".join(f"{k} = {v[1]}" for k, v in lab_data.TRAITS.items())
        return head + f"\nNIR predictions, dry weight basis. Units: {units}.\n\n" + lab_data.csv_rows(rows, limit)
    return head + "\nValues are NIR predictions, dry weight basis.\n\n" + lab_data.table(rows, include_reps, limit)


@mcp.tool(
    name="lab_genotype_profile",
    description=(
        "Everything the KB holds for one genotype/accession: its samples in every lab dataset (all QC bands, "
        "with each band labelled), its mean per dataset over usable samples, and where the same accession "
        "appears in the literature (paper pages, tables and supplements, via exact search)."
    ),
)
def lab_genotype_profile(genotype: str, include_literature: bool = True) -> str:
    rows = lab_data.find_samples(genotype=genotype, qc="all")
    parts = [f"# Genotype {lab_data.canon_genotype(genotype)}"]
    if rows:
        parts.append(f"## Lab samples ({len(rows)})\n" + lab_data.table(rows, include_reps=False, limit=60))
        means = []
        for ds, _ in lab_data.load_all():
            ok = [s for d, s in rows if d["id"] == ds["id"] and s["qc"] in ("good", "usable")]
            if ok:
                vals = {k: sum(s["values"][k] for s in ok if k in s["values"]) / max(1, sum(k in s["values"] for s in ok))
                        for k in lab_data.TRAITS if any(k in s["values"] for s in ok)}
                means.append(f"- {ds['id']} (n = {len(ok)} usable sample(s)): {lab_data.values_text(vals)}")
            elif any(d["id"] == ds["id"] for d, _ in rows):
                means.append(f"- {ds['id']}: no sample with QC good or usable")
        parts.append("## Mean of usable samples per dataset\n" + "\n".join(means))
    else:
        parts.append("Not found in the lab datasets.")
    trial_lines = []
    for ds, plots in field_trials.load_all():
        mine = [p for p in plots if lab_data.genotype_match(genotype, p["genotype"])]
        if mine:
            vals = {k: [p["traits"][k] for p in mine if k in p["traits"] and field_trials._usable(p)] for k in field_trials.TRAITS}
            txt = "; ".join(f"{field_trials.TRAITS[k][0].lower()} {sum(v) / len(v):.3g} {field_trials.TRAITS[k][1]}" for k, v in vals.items() if v)
            flags = sorted({f for p in mine for f in p["flags"]})
            trial_lines.append(f"- {ds['title']} ({ds['site']}, {ds['year']}): plots {', '.join(p['plot'] for p in mine)}; mean of reps: {txt}"
                               + (f"; flags: {', '.join(flags)}" if flags else ""))
    if trial_lines:
        parts.append("## Field trials\n" + "\n".join(trial_lines))
    if include_literature:
        res = text_index.search(genotype, limit=12)
        hits = [h for t in res.get("terms", []) for h in t["exact"] if h["kind"] != "lab_data"]
        if hits:
            parts.append(f"## Literature mentions ({len(hits)})\n" + "\n".join(
                f"- {h['title']} ({h['year'] or 'n.d.'}) DOI: {h['doi'] or 'n/a'} [{h['kind']} {h['ref']}".rstrip() +
                (f", page {h['page']}" if h['page'] else "") + f"]\n  {h['snippet']}" for h in hits[:12]))
        else:
            parts.append("## Literature mentions\nNone found by exact search.")
    return "\n\n".join(parts)


@mcp.tool(
    name="compare_lab_traits",
    description=(
        "Correlation between two traits in the lab datasets (Pearson r with 95% CI, n, p, slope), per dataset "
        "and optionally per location. Example: protein vs lysine_protein tests lysine dilution; protein vs "
        "starch the protein-starch trade-off. qc defaults to good+usable samples."
    ),
)
def compare_lab_traits(trait_x: str, trait_y: str, dataset: str = "", qc: str = "usable", by_location: bool = False) -> str:
    res = lab_data.correlate(trait_x, trait_y, dataset, qc, by_location)
    if not res.get("ok"):
        return res.get("error", "failed")
    x, y = lab_data.TRAITS[res["x"]], lab_data.TRAITS[res["y"]]
    lines = [f"{x[0]} ({x[1]}) vs {y[0]} ({y[1]}), QC {', '.join(res['qc'])}:"]
    for ds_id, groups in res["results"].items():
        for g, c in groups.items():
            if c.get("r") is None:
                lines.append(f"- {ds_id} / {g}: too few samples (n = {c['n']})")
            else:
                lines.append(f"- {ds_id} / {g}: r = {c['r']} (95% CI {c['ci95'][0]} to {c['ci95'][1]}), n = {c['n']}, "
                             f"p = {c['p']}, slope = {c['slope']}")
    return "\n".join(lines)


@mcp.tool(
    name="compare_lab_datasets",
    description=(
        "Compare one trait for the genotypes measured in two lab datasets (e.g. HPI-SAP vs Danforth 2025): "
        "number of shared genotypes, Pearson and rank correlation of genotype means, mean values, and the "
        "genotypes whose values changed most. Defaults to the first two datasets and good+usable samples."
    ),
)
def compare_lab_datasets(trait: str = "protein", dataset_a: str = "", dataset_b: str = "", qc: str = "usable", top: int = 10) -> str:
    res = lab_data.compare_datasets(trait, dataset_a, dataset_b, qc, max(1, min(int(top), 30)))
    if not res.get("ok"):
        return res.get("error", "failed")
    c, rc = res["correlation"], res["rank_correlation"]
    lines = [
        f"{lab_data.TRAITS[res['trait']][0]} ({res['unit']}): {res['dataset_a']} (A) vs {res['dataset_b']} (B), QC {', '.join(res['qc'])}",
        f"Genotypes with usable data: A {res['n_genotypes_a']}, B {res['n_genotypes_b']}, shared {res['n_shared']}",
    ]
    if c.get("r") is not None:
        lines.append(f"Pearson r = {c['r']} (95% CI {c['ci95'][0]} to {c['ci95'][1]}, n = {c['n']}); rank correlation = {rc.get('r')}")
        lines.append(f"Mean of shared genotypes: A {res['mean_a']}, B {res['mean_b']}")
        lines.append("Largest increase (B minus A): " + ", ".join(f"{g} {d:+}" for g, d in res["largest_increase_b_minus_a"]))
        lines.append("Largest decrease (B minus A): " + ", ".join(f"{g} {d:+}" for g, d in res["largest_decrease_b_minus_a"]))
    else:
        lines.append("Too few shared genotypes with usable data for a correlation (try qc='all').")
    return "\n".join(lines)


@mcp.tool(
    name="compare_lab_with_literature",
    description=(
        "Check lab values against published per-accession values held in KB supplements (e.g. NIRS protein, "
        "starch and fat of the sorghum association panel in Rhodes et al. 2017; lysine and starch in Kimani "
        "et al. 2020): shared accessions, Pearson and rank correlation, lab and published means, per lab dataset. "
        "Traits: protein, starch, fat, lysine_grain."
    ),
)
def compare_lab_with_literature(trait: str = "protein", dataset: str = "", qc: str = "usable") -> str:
    res = lab_data.compare_with_literature(trait, dataset, qc)
    if not res.get("ok"):
        return res.get("error", "failed")
    lines = [f"{lab_data.TRAITS[res['trait']][0]}: lab NIR values (QC {', '.join(res['qc'])}) vs published values for the same accessions"]
    for r in res["results"]:
        c, rc = r["correlation"], r["rank_correlation"]
        stat = (f"r = {c['r']} (95% CI {c['ci95'][0]} to {c['ci95'][1]}), rank r = {rc.get('r')}" if c.get("r") is not None
                else "too few shared accessions for a correlation")
        lines.append(f"- {r['dataset']} vs {r['paper']} (DOI {r['doi']}), {r['source']}, column {r['published_column']!r}: "
                     f"{r['n_shared']} shared accessions; {stat}; lab mean {r['lab_mean']}, published mean {r['published_mean']} "
                     f"({r['linked_by']})")
    lines.append("Different years, sites and NIR calibrations lower agreement; units of published columns may differ "
                 "(check the column name), so compare correlations rather than absolute values.")
    return "\n".join(lines)


@mcp.tool(
    name="query_field_data",
    description=(
        "Filter and sort plots of the lab's field trials (HPI field in Missouri 2023 to 2025, South Carolina 2023 "
        "and 2024). Filters: genotype ('PI 533919' or a name fragment), plot ID, dataset, site, year, trait with "
        "min_value/max_value, sort_by a trait, exclude_flagged (drop mixed, empty, weedy or armyworm plots). Traits: "
        "days_to_anthesis, plant_height, lodging_pct, tillering_pct, tillers_per_plant, panicle_tillers, tiller_height, "
        "stem_diameter_3rd, stem_diameter_6th, biomass, leaf_biomass, weed_cover_pct, stand_count. fmt='csv' returns "
        "all matching plots (up to 3000) for plotting."
    ),
)
def query_field_data(
    genotype: str = "",
    plot: str = "",
    dataset: str = "",
    site: str = "",
    year: int | None = None,
    trait: str = "",
    min_value: float | None = None,
    max_value: float | None = None,
    sort_by: str = "",
    descending: bool = True,
    exclude_flagged: bool = False,
    limit: int = 25,
    fmt: str = "table",
) -> str:
    if not field_trials.load_all():
        return "No field trials are built."
    if (trait and not field_trials.trait_key(trait)) or (sort_by and not field_trials.trait_key(sort_by)):
        return f"Unknown trait. Use one of: {', '.join(field_trials.TRAITS)}"
    rows = field_trials.find_plots(genotype, plot, dataset, site, year, trait, min_value, max_value, sort_by, descending, exclude_flagged)
    csv_out = (fmt or "").lower() == "csv"
    limit = max(1, min(int(limit), 3000 if csv_out else 200))
    if csv_out and limit == 25:
        limit = 3000
    head = f"{len(rows)} plot(s) match."
    if not rows:
        return head
    units = "; ".join(f"{k} = {v[1]}" for k, v in field_trials.TRAITS.items())
    return head + f"\nUnits: {units}.\n\n" + field_trials.table(rows, limit, csv_out)


@mcp.tool(
    name="compare_field_traits",
    description=(
        "Field-trait relationships and year-to-year agreement. With trait_y: correlation of two field traits over "
        "plots in each trial (e.g. plant_height vs biomass). With dataset_a and dataset_b instead: agreement of one "
        "trait for the same genotypes in two trials (e.g. days_to_anthesis in HPI 2023 vs HPI 2024), with the "
        "genotypes that changed most."
    ),
)
def compare_field_traits(trait_x: str, trait_y: str = "", dataset: str = "", dataset_a: str = "", dataset_b: str = "", top: int = 10) -> str:
    if dataset_a and dataset_b:
        r = field_trials.compare_trials(trait_x, dataset_a, dataset_b, max(1, min(int(top), 30)))
        if not r.get("ok"):
            return r.get("error", "failed")
        c = r["correlation"]
        return (f"{field_trials.TRAITS[r['trait']][0]} ({r['unit']}): {r['dataset_a']} (A) vs {r['dataset_b']} (B), genotype means, "
                f"mixed and empty plots excluded\nShared genotypes: {r['n_shared']}; r = {c.get('r')} "
                f"(95% CI {c.get('ci95')}); mean A {r['mean_a']}, mean B {r['mean_b']}\n"
                "Largest increase (B minus A): " + ", ".join(f"{g} {d:+}" for g, d in r["largest_increase_b_minus_a"]) + "\n"
                "Largest decrease (B minus A): " + ", ".join(f"{g} {d:+}" for g, d in r["largest_decrease_b_minus_a"]))
    if not trait_y:
        return "Give trait_y (relationship within trials) or dataset_a and dataset_b (same trait in two trials)."
    r = field_trials.correlate(trait_x, trait_y, dataset)
    if not r.get("ok"):
        return r.get("error", "failed")
    lines = [f"{trait_x} vs {trait_y}, {r['level']}:"]
    for ds_id, c in r["results"].items():
        lines.append(f"- {ds_id}: r = {c['r']} (95% CI {c['ci95'][0]} to {c['ci95'][1]}), n = {c['n']}, slope = {c['slope']}")
    return "\n".join(lines)


@mcp.tool(
    name="compare_nir_with_field",
    description=(
        "Join the NIR grain composition with field traits and correlate them, e.g. protein vs days_to_anthesis, "
        "lysine_protein vs plant_height, starch vs biomass. Danforth 2025 NIR is joined plot by plot to the HPI 2025 "
        "trial (same plots, same season); HPI-SAP NIR (season not recorded) is joined by genotype means to each field "
        "trial at the same site. NIR qc defaults to good+usable."
    ),
)
def compare_nir_with_field(nir_trait: str, field_trait: str, nir_dataset: str = "", field_dataset: str = "", qc: str = "usable") -> str:
    r = field_trials.nir_field_join(nir_trait, field_trait, nir_dataset, field_dataset, qc)
    if not r.get("ok"):
        return r.get("error", "failed")
    if not r["results"]:
        return "No NIR samples could be joined to that field trait (try qc='all')."
    lines = [f"{lab_data.TRAITS[r['nir_trait']][0]} (NIR) vs {field_trials.TRAITS[r['field_trait']][0]} (field), NIR QC {', '.join(r['qc'])}:"]
    for x in r["results"]:
        lines.append(f"- {x['nir_dataset']} x {x['field_dataset']} ({x['site']}, {x['year']}; {x['level']}): r = {x['r']} "
                     f"(95% CI {x['ci95'][0]} to {x['ci95'][1]}), n = {x['n']}, slope = {x['slope']}")
    lines.append("Genotype-mean joins compare grain from one season with field traits from another; treat them as "
                 "genotype-level associations.")
    return "\n".join(lines)


@mcp.tool(
    name="rebuild_lab_data",
    description=(
        "Re-parse every workbook in lab_data/ and refresh the exact-text, semantic and graph indexes. Use "
        "after a lab workbook was added or replaced on the server."
    ),
)
def rebuild_lab_data() -> str:
    return json.dumps(lab_data.build(update_indexes=True), indent=2)


@mcp.tool(
    name="explore_knowledge_graph",
    description=(
        "Explore relationships around an entity such as kafirin, fermentation, tannin, "
        "protein digestibility, a gene, or a paper title. Returns typed nodes, edges, "
        "source paper IDs, evidence text, confidence, and extraction method."
    ),
)
def explore_knowledge_graph(
    entity: str,
    depth: int = 1,
    relation: str = "",
    limit: int = 60,
) -> str:
    result = KnowledgeGraph().explore(
        query=entity,
        depth=max(1, min(int(depth), 3)),
        relation=relation,
        limit=max(1, min(int(limit), 150)),
    )
    return json.dumps(result, indent=2)


@mcp.tool(
    name="get_paper_graph",
    description=(
        "Return graph entities and evidence-backed relationships extracted from one paper. "
        "Pass paper_id, DOI, or a unique title fragment."
    ),
)
def get_paper_graph(paper_query: str, limit: int = 100) -> str:
    pid, err = _resolve_or_message(paper_query)
    if err:
        return err
    return json.dumps(
        KnowledgeGraph().paper_graph(pid, limit=max(1, min(int(limit), 200))),
        indent=2,
    )


@mcp.tool(
    name="find_papers",
    description=(
        "Search the literature catalog by keyword in title/abstract. "
        "Returns title, year, DOI/link, citation count, and whether a local PDF exists."
    ),
)
def find_papers(keyword: str, downloaded_only: bool = False, limit: int = 15) -> str:
    keyword_l = (keyword or "").strip().lower()
    if not keyword_l:
        return "Provide a keyword such as kafirin, tannin, fermentation, or CRISPR."
    limit = max(1, min(int(limit), 30))
    pdfs = _downloaded_names()
    rows = []
    for p in _load_catalog():
        title = _clean(p.get("title") or "")
        abstract = p.get("abstract") or ""
        blob = f"{title} {abstract}".lower()
        if keyword_l not in blob:
            continue
        local = p.get("local_path") or ""
        has_pdf = bool(local) or any(
            (p.get("doi") or "").replace("/", "_") in name for name in pdfs
        )
        if downloaded_only and not has_pdf:
            continue
        doi = p.get("doi")
        rows.append(
            {
                "title": title,
                "year": p.get("year"),
                "doi": doi,
                "cited_by_count": p.get("cited_by_count") or 0,
                "downloaded": has_pdf,
                "link": f"https://doi.org/{doi}" if doi else (p.get("landing_url") or p.get("link")),
            }
        )
    rows.sort(key=lambda r: (-int(bool(r["downloaded"])), -(r["cited_by_count"] or 0)))
    rows = rows[:limit]
    if not rows:
        return f"No catalog hits for {keyword!r}."
    lines = [f"Found {len(rows)} papers for {keyword!r}:\n"]
    for r in rows:
        flag = "PDF" if r["downloaded"] else "LINK"
        lines.append(
            f"- [{flag}] {r['title']} ({r.get('year') or '?'}, cited {r['cited_by_count']})\n"
            f"  {r.get('link') or 'n/a'}"
        )
    return "\n".join(lines)


@mcp.tool(
    name="list_undownloaded_papers",
    description=(
        "List sorghum-protein papers that could not be downloaded as PDFs, with DOI links. "
        "Set priority_only=true for the most important kafirin/digestibility papers."
    ),
)
def list_undownloaded_papers(priority_only: bool = True, limit: int = 25) -> str:
    path = META / "UNAVAILABLE_PAPERS.md"
    if not path.exists():
        return "No unavailable-papers list found."
    text = path.read_text()
    if not priority_only:
        return text[:12000]
    if "## Highest priority" in text:
        part = text.split("## Other related")[0]
        lines = part.strip().splitlines()
        out, n = [], 0
        for line in lines:
            out.append(line)
            if line.startswith("- **"):
                n += 1
                if n >= max(1, min(int(limit), 40)):
                    break
        return "\n".join(out)
    return text[:8000]


# ---------------------------------------------------------------------------
# Structured paper component tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="list_structured_papers",
    description=(
        "List papers that have been structurally extracted (metadata, sections, "
        "figures, tables). Returns paper_id, title, year, DOI, figure/table counts. "
        "Use paper_id with get_paper / get_paper_figure / get_paper_table. "
        "Default limit is 30 — for the COMPLETE inventory use list_all_papers_tree."
    ),
)
def list_structured_papers(limit: int = 30, year_from: int = 0) -> str:
    limit = max(1, min(int(limit), 100))
    papers = store.load_index().get("papers") or []
    if year_from:
        papers = [p for p in papers if (p.get("year") or 0) >= int(year_from)]
    total = len(papers)
    papers = papers[:limit]
    if not papers:
        return (
            "No structured papers yet. Run: "
            "python scripts/extract_paper_structure.py"
        )
    lines = [
        f"Showing {len(papers)} of {total} structured papers "
        f"(use list_all_papers_tree for the full inventory):\n"
    ]
    for p in papers:
        lines.append(
            f"- `{p['paper_id']}` | {p.get('title')} ({p.get('year') or '?'})\n"
            f"  DOI: {p.get('doi') or 'n/a'} | "
            f"figures={p.get('n_figures', 0)} tables={p.get('n_tables', 0)} "
            f"pages={p.get('n_pages', 0)}"
        )
    return "\n".join(lines)


@mcp.tool(
    name="list_all_papers_tree",
    description=(
        "Return the COMPLETE sorghum KB paper inventory as structured JSON "
        "(all papers, not a sample). Includes totals, year histogram, and every "
        "paper's title/year/DOI/pages/figures/tables/paper_id. "
        "Use this when the user asks for a tree, full list, inventory, or "
        "'all papers' in the knowledge base. Do NOT invent or truncate counts."
    ),
)
def list_all_papers_tree(
    year_from: int = 0,
    year_to: int = 0,
    theme_filter: str = "",
) -> str:
    idx = store.load_index()
    papers = list(idx.get("papers") or [])
    if year_from:
        papers = [p for p in papers if (p.get("year") or 0) >= int(year_from)]
    if year_to:
        papers = [p for p in papers if (p.get("year") or 0) <= int(year_to)]

    def theme_of(title: str) -> str:
        t = (title or "").lower()
        if any(k in t for k in ("kafirin", "protein digest", "digestibility", "prolamin", "amino acid")):
            return "prot"
        if any(k in t for k in ("fermented", "cooking", "germination", "malting", "extrud", "biscuit", "bread", "food", "processing", "injera", "silage")):
            return "food"
        if any(k in t for k in ("genome", "crispr", "mutant", "qtl", "gwas", "breeding", "editing", "transcript")):
            return "genet"
        if any(k in t for k in ("nutrit", "bioactive", "polyphenol", "tannin", "mineral", "health")):
            return "nutr"
        return "other"

    if theme_filter:
        tf = theme_filter.lower().strip()
        papers = [p for p in papers if theme_of(p.get("title") or "") == tf or tf in (p.get("title") or "").lower()]

    years = [p.get("year") for p in papers if p.get("year")]
    year_hist: dict[str, int] = {}
    for y in years:
        year_hist[str(y)] = year_hist.get(str(y), 0) + 1

    rows = []
    for i, p in enumerate(sorted(papers, key=lambda x: (-(x.get("year") or 0), x.get("title") or "")), 1):
        rows.append(
            {
                "id": i,
                "paper_id": p.get("paper_id"),
                "year": p.get("year"),
                "title": p.get("title"),
                "doi": p.get("doi"),
                "url": f"https://doi.org/{p['doi']}" if p.get("doi") else None,
                "theme": theme_of(p.get("title") or ""),
                "pages": p.get("n_pages") or 0,
                "figures": p.get("n_figures") or 0,
                "tables": p.get("n_tables") or 0,
                "note": "figure_count=embedded_images_not_captioned_figures",
            }
        )

    payload = {
        "metadata": {
            "name": "Shakoor Lab Sorghum-Protein Knowledge Base",
            "complete": True,
            "warning": (
                "This is the FULL inventory from knowledge_base/structured/index.json. "
                "figure counts are extracted embedded images (may exceed named 'Figure N' captions)."
            ),
            "total_papers": len(rows),
            "kb_total_papers_unfiltered": idx.get("n_papers"),
            "year_range": [min(years), max(years)] if years else None,
            "year_histogram": dict(sorted(year_hist.items())),
            "totals": {
                "pages": sum(r["pages"] for r in rows),
                "figures": sum(r["figures"] for r in rows),
                "tables": sum(r["tables"] for r in rows),
            },
            "filters": {
                "year_from": year_from or None,
                "year_to": year_to or None,
                "theme_filter": theme_filter or None,
            },
            "themes": {
                "prot": "Protein & digestibility",
                "nutr": "Nutrition & bioactives",
                "food": "Food & processing",
                "genet": "Genetics / genomics / breeding",
                "other": "Other / mixed",
            },
        },
        "papers": rows,
    }
    return json.dumps(payload, indent=2)


@mcp.tool(
    name="search_structured_papers",
    description=(
        "Keyword search over structured paper titles/abstracts/keywords. "
        "Returns paper_id values you can pass to get_paper and related tools."
    ),
)
def search_structured_papers(keyword: str, limit: int = 15) -> str:
    rows = store.search_structured(keyword, limit=max(1, min(int(limit), 30)))
    if not rows:
        return f"No structured hits for {keyword!r}."
    lines = [f"Structured hits for {keyword!r}:\n"]
    for p in rows:
        lines.append(
            f"- `{p['paper_id']}` | {p.get('title')} ({p.get('year') or '?'})\n"
            f"  DOI: {p.get('doi') or 'n/a'} | figs={p.get('n_figures', 0)} "
            f"tables={p.get('n_tables', 0)}"
        )
    return "\n".join(lines)


@mcp.tool(
    name="search_paper_components",
    description=(
        "Search ALL extracted paper parts: figure OCR/labels, captions, tables, "
        "claims, quantitative facts, and sections. Use when the user asks what a "
        "figure/graph/gel shows, a number from a plot, or anything that may live "
        "in an image rather than body text. "
        "component filter: figure, table, caption, claim, fact, section, abstract."
    ),
)
def search_paper_components(
    query_text: str,
    component: str = "",
    limit: int = 12,
) -> str:
    q = (query_text or "").strip()
    if not q:
        return "Provide a search phrase (e.g. protein body invagination, 80% digestibility)."
    rows = store.search_components(q, component=component or "", limit=max(1, min(int(limit), 40)))
    if not rows:
        return f"No component hits for {q!r}."
    lines = [f"Component hits for {q!r} ({len(rows)}):\n"]
    for r in rows:
        lines.append(
            f"- [{r.get('component')}] `{r.get('id')}` | {r.get('title')} ({r.get('doi') or r.get('paper_id')})\n"
            f"  page={r.get('page')} type={r.get('figure_type') or '-'}\n"
            f"  {(r.get('text') or '')[:420]}"
        )
    return "\n".join(lines)


@mcp.tool(
    name="get_paper_coverage",
    description=(
        "Show how completely a paper was ingested: sections, figure OCR/labels, "
        "table grids, references, quantitative facts, and what is still missing."
    ),
)
def get_paper_coverage(paper_query: str) -> str:
    pid, err = _resolve_or_message(paper_query)
    if err:
        return err
    data = store.load_paper(pid, light=True)
    if not data:
        return f"No structured record for {pid}."
    cov = data.get("coverage") or {}
    payload = {
        "paper_id": pid,
        "title": data.get("title"),
        "doi": data.get("doi"),
        "stats": data.get("stats"),
        "coverage": cov,
        "section_names": data.get("section_names"),
        "n_components": data.get("n_components"),
        "quantitative_facts": data.get("quantitative_facts") or [],
    }
    return json.dumps(payload, indent=2)


@mcp.tool(
    name="get_paper",
    description=(
        "Get structured metadata for one paper: title, authors, year, DOI, abstract, "
        "keywords, section list, figure/table inventory, key claims. "
        "Pass paper_id, DOI, or unique title fragment. "
        "Set include_full_sections=true only when you need full section text."
    ),
)
def get_paper(paper_query: str, include_full_sections: bool = False) -> str:
    pid, err = _resolve_or_message(paper_query)
    if err:
        return err
    data = store.load_paper(pid, light=not include_full_sections)
    if not data:
        return f"No structured record for {pid}."
    if include_full_sections:
        # may be large — truncate huge sections for tool safety
        sections = data.get("sections") or {}
        data = dict(data)
        data["sections"] = {
            k: (v[:8000] + "…[truncated]" if isinstance(v, str) and len(v) > 8000 else v)
            for k, v in sections.items()
        }
        if data.get("references"):
            data["references"] = data["references"][:80]
    return json.dumps(data, indent=2)


@mcp.tool(
    name="get_paper_section",
    description=(
        "Return one section of a structured paper. "
        "section_name examples: abstract, introduction, methods, results, "
        "discussion, conclusion, references, front_matter."
    ),
)
def get_paper_section(paper_query: str, section_name: str, max_chars: int = 12000) -> str:
    pid, err = _resolve_or_message(paper_query)
    if err:
        return err
    text = store.get_section(pid, section_name, max_chars=max(1000, min(int(max_chars), 30000)))
    if not text:
        card = store.load_card(pid) or {}
        names = card.get("section_names") or []
        return f"Section {section_name!r} not found. Available: {', '.join(names) or 'none'}"
    paper = store.load_card(pid) or {}
    header = f"# {paper.get('title')} — section: {section_name}\nDOI: {paper.get('doi')}\n\n"
    return header + text


@mcp.tool(
    name="list_paper_figures",
    description="List extracted figures/images for a paper (id, page, caption, file path).",
)
def list_paper_figures(paper_query: str) -> str:
    pid, err = _resolve_or_message(paper_query)
    if err:
        return err
    figs = store.list_figures(pid)
    if not figs:
        return f"No figures extracted for {pid}."
    lines = [f"Figures for `{pid}` ({len(figs)}):\n"]
    for f in figs:
        ocr = (f.get("ocr_text") or f.get("nearby_text") or "")[:180]
        lines.append(
            f"- `{f.get('id')}` page {f.get('page')} | {f.get('figure_type') or 'image'} | "
            f"{f.get('width')}x{f.get('height')}\n"
            f"  caption: {(f.get('caption_guess') or '')[:240]}\n"
            f"  labels/ocr: {ocr or 'n/a'}\n"
            f"  file: knowledge_base/structured/{pid}/{f.get('file')}"
        )
    return "\n".join(lines)


@mcp.tool(
    name="get_paper_figure",
    description=(
        "Get one figure from a paper. Returns metadata + local file path. "
        "Set include_base64=true to inline PNG data for the client (may be large). "
        "figure_id examples: fig_001 or 1."
    ),
)
def get_paper_figure(paper_query: str, figure_id: str, include_base64: bool = False) -> str:
    pid, err = _resolve_or_message(paper_query)
    if err:
        return err
    if include_base64:
        payload = store.get_figure_base64(pid, figure_id)
    else:
        payload = store.get_figure_meta(pid, figure_id)
    if not payload:
        figs = store.list_figures(pid)
        ids = ", ".join(f.get("id", "") for f in figs) or "none"
        return f"Figure {figure_id!r} not found. Available: {ids}"
    # Also register-friendly absolute path
    return json.dumps(payload, indent=2)


@mcp.tool(
    name="list_paper_tables",
    description="List extracted tables for a paper (id, page, caption, shape, files).",
)
def list_paper_tables(paper_query: str) -> str:
    pid, err = _resolve_or_message(paper_query)
    if err:
        return err
    tables = store.list_tables(pid)
    if not tables:
        return f"No tables extracted for {pid}."
    lines = [f"Tables for `{pid}` ({len(tables)}):\n"]
    for t in tables:
        lines.append(
            f"- `{t.get('id')}` page {t.get('page')} | "
            f"{t.get('n_rows')} rows x {t.get('n_cols')} cols\n"
            f"  caption: {(t.get('caption_guess') or '')[:240]}\n"
            f"  headers: {t.get('headers')}"
        )
    return "\n".join(lines)


@mcp.tool(
    name="get_paper_table",
    description=(
        "Get one table from a paper as markdown (default), csv, or json. "
        "table_id examples: table_001 or 1."
    ),
)
def get_paper_table(paper_query: str, table_id: str, fmt: str = "markdown") -> str:
    pid, err = _resolve_or_message(paper_query)
    if err:
        return err
    fmt = (fmt or "markdown").lower()
    if fmt not in {"markdown", "md", "csv", "json"}:
        fmt = "markdown"
    text = store.get_table(pid, table_id, fmt=fmt)
    if not text:
        tables = store.list_tables(pid)
        ids = ", ".join(t.get("id", "") for t in tables) or "none"
        return f"Table {table_id!r} not found. Available: {ids}"
    paper = store.load_card(pid) or {}
    return f"# {paper.get('title')} — {table_id}\nDOI: {paper.get('doi')}\n\n{text}"


@mcp.tool(
    name="get_paper_references",
    description="Return extracted bibliography/reference entries for a paper.",
)
def get_paper_references(paper_query: str, limit: int = 40) -> str:
    pid, err = _resolve_or_message(paper_query)
    if err:
        return err
    data = store.load_paper(pid, light=False)
    if not data:
        return f"No structured record for {pid}."
    refs = data.get("references") or []
    limit = max(1, min(int(limit), 200))
    if not refs:
        return "No references extracted for this paper."
    lines = [f"References for `{pid}` (showing {min(limit, len(refs))} of {len(refs)}):\n"]
    for i, r in enumerate(refs[:limit], 1):
        lines.append(f"{i}. {r}")
    return "\n".join(lines)


@mcp.tool(
    name="get_paper_claims",
    description=(
        "Return key claim sentences mined from abstract/results/discussion "
        "related to protein digestibility, kafirins, tannins, processing, etc."
    ),
)
def get_paper_claims(paper_query: str) -> str:
    pid, err = _resolve_or_message(paper_query)
    if err:
        return err
    data = store.load_paper(pid, light=True)
    if not data:
        return f"No structured record for {pid}."
    claims = data.get("key_claims") or []
    if not claims:
        return "No key claims extracted."
    title = data.get("title")
    lines = [f"Key claims — {title}\nDOI: {data.get('doi')}\n"]
    for i, c in enumerate(claims, 1):
        lines.append(f"{i}. {c}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Ingest / maintenance tools (add papers from chat)
# ---------------------------------------------------------------------------


@mcp.tool(
    name="add_supplement",
    description=(
        "Attach a paper's supplementary files (supplementary tables/datasets: xlsx, csv, tsv, docx, pdf, "
        "txt, or a .zip of them) and index every row for exact and semantic search. Per-accession or "
        "per-line data usually lives in supplements, not in the article. Leave source empty to fetch the "
        "paper's open-access supplements from PubMed Central automatically (via its DOI); otherwise pass "
        "a local file path or a direct download URL."
    ),
)
def add_supplement(paper_query: str, source: str = "", refresh: bool = False) -> str:
    import supplements

    if source.strip():
        res = supplements.add_supplement(paper_query, source.strip())
    else:
        res = supplements.fetch_supplements(paper_query, refresh=bool(refresh))
    if not res.get("ok"):
        return res.get("error") or "Could not add supplement."
    lines = [f"Paper: {res['paper_id']}"]
    if res.get("checked"):
        lines.append(f"PubMed Central: {res['checked'].get('result')} (PMCID {res['checked'].get('pmcid') or 'none'})")
    if res.get("skipped"):
        lines.append(f"Already checked; files: {', '.join(res.get('files') or []) or 'none'} (use refresh=true to re-fetch)")
    for a in res.get("added") or []:
        if a.get("error"):
            lines.append(f"- {a['file']}: failed ({a['error']})")
        else:
            lines.append(f"- {a['file']}: {', '.join(a.get('parts') or [])} ({a.get('n_rows')} rows, {a.get('chars')} chars)")
    if res.get("added"):
        lines.append("Indexed for exact_search_sorghum_protein and semantic search.")
    return "\n".join(lines)


@mcp.tool(
    name="add_paper",
    description=(
        "Add a new research paper to the knowledge base from chat. "
        "source can be: (1) DOI like 10.3390/nu12041111 or https://doi.org/..., "
        "(2) a direct PDF/article URL, or (3) an absolute local .pdf path on this machine. "
        "Downloads when possible, extracts metadata/sections/figures/tables, "
        "updates the structured index, and upserts into both vector and graph databases. "
        "Returns paper_id for follow-up get_paper / get_paper_graph calls."
    ),
)
def add_paper(
    source: str,
    title_hint: str = "",
    update_vector_index: bool = True,
    update_graph_index: bool = True,
) -> str:
    out = ingest.ingest(
        source=source,
        title_hint=title_hint or None,
        update_vector_index=bool(update_vector_index),
        update_graph_index=bool(update_graph_index),
        force_extract=True,
    )
    return json.dumps(out, indent=2)


@mcp.tool(
    name="reextract_paper",
    description=(
        "Re-run structured extraction (sections/figures/tables) for a paper already "
        "in papers/. Pass paper_id, DOI, title fragment, or PDF filename. "
        "Use after a failed/partial extract or when you want fresh figures/tables; "
        "vector and graph records are refreshed by default."
    ),
)
def reextract_paper(
    paper_query: str,
    update_vector_index: bool = True,
    update_graph_index: bool = True,
) -> str:
    # Resolve to a PDF on disk
    pid = store.resolve_paper_id(paper_query)
    pdf: Path | None = None
    catalog_rec = None
    if pid:
        card = store.load_card(pid) or {}
        sp = card.get("source_pdf")
        if sp and (ROOT / sp).exists():
            pdf = ROOT / sp
        catalog_rec = {
            "title": card.get("title"),
            "doi": card.get("doi"),
            "year": card.get("year"),
            "authors": card.get("authors"),
            "abstract": card.get("abstract"),
        }
    if pdf is None:
        # try as path / filename
        cand = Path(paper_query).expanduser()
        if cand.exists() and cand.suffix.lower() == ".pdf":
            pdf = cand
        else:
            hits = list(PAPERS.glob(f"*{paper_query}*"))
            hits = [h for h in hits if h.suffix.lower() == ".pdf"]
            if len(hits) == 1:
                pdf = hits[0]
            elif hits:
                return json.dumps(
                    {
                        "ok": False,
                        "error": "Multiple PDF matches; pass a more specific name.",
                        "matches": [h.name for h in hits[:10]],
                    },
                    indent=2,
                )
    if pdf is None or not pdf.exists():
        return json.dumps(
            {
                "ok": False,
                "error": f"Could not find PDF for {paper_query!r}. Use add_paper first.",
            },
            indent=2,
        )
    try:
        paper_rec = ingest.extract_one_pdf(pdf, catalog_rec=catalog_rec, force=True)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, indent=2)
    chroma_info = None
    if update_vector_index:
        chroma_info = ingest.index_paper_into_chroma(paper_rec)
    graph_info = None
    if update_graph_index:
        graph_info = ingest.index_paper_into_graph(paper_rec)
    stats = paper_rec.get("stats") or {}
    return json.dumps(
        {
            "ok": True,
            "paper_id": paper_rec.get("paper_id"),
            "title": paper_rec.get("title"),
            "doi": paper_rec.get("doi"),
            "figures": stats.get("n_figures"),
            "tables": stats.get("n_tables"),
            "pages": stats.get("n_pages"),
            "vector_index": chroma_info,
            "graph_index": graph_info,
        },
        indent=2,
    )


@mcp.tool(
    name="rebuild_structured_index",
    description=(
        "Rebuild knowledge_base/structured/index.json from already-extracted paper.json "
        "files (fast). Does not re-parse PDFs. Use after manual folder changes."
    ),
)
def rebuild_structured_index() -> str:
    info = ingest.refresh_structured_index()
    return json.dumps({"ok": True, **info}, indent=2)


@mcp.tool(
    name="prepare_graph_enrichment",
    description=(
        "Prepare an evidence packet for LLM-assisted graph enrichment of one paper. "
        "Read this output, identify only relationships supported by its evidence, then call "
        "add_graph_facts. Useful after add_paper when richer relationships are desired."
    ),
)
def prepare_graph_enrichment(paper_query: str) -> str:
    pid, err = _resolve_or_message(paper_query)
    if err:
        return err
    paper = store.load_paper(pid, light=False)
    if not paper:
        return f"No structured record for {pid}."
    packet = {
        "paper_id": pid,
        "title": paper.get("title"),
        "doi": paper.get("doi"),
        "abstract": paper.get("abstract"),
        "key_claims": paper.get("key_claims") or [],
        "quantitative_facts": paper.get("quantitative_facts") or [],
        "current_graph": KnowledgeGraph().paper_graph(pid, limit=25),
        "allowed_node_types": [
            "GENE", "PROTEIN", "TRAIT", "PROCESS", "COMPOUND", "STRUCTURE",
            "PHENOTYPE", "ORGANISM", "METHOD", "TOPIC",
        ],
        "fact_format": {
            "source": "canonical entity name",
            "source_type": "PROCESS",
            "relation": "IMPROVES",
            "target": "canonical entity name",
            "target_type": "TRAIT",
            "evidence": "exact or close paper-supported evidence sentence",
            "confidence": 0.8,
        },
        "instructions": (
            "Add only useful, non-duplicate relationships supported by this packet. "
            "Do not convert speculation into fact. Preserve directionality and include evidence."
        ),
    }
    return json.dumps(packet, indent=2)


@mcp.tool(
    name="add_graph_facts",
    description=(
        "Add LLM-reviewed, evidence-backed relationships for an already ingested paper. "
        "First call prepare_graph_enrichment. facts_json must be a JSON array of objects "
        "with source, source_type, relation, target, target_type, evidence, and confidence."
    ),
)
def add_graph_facts(paper_query: str, facts_json: str) -> str:
    pid, err = _resolve_or_message(paper_query)
    if err:
        return err
    try:
        facts = json.loads(facts_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"ok": False, "error": f"invalid facts_json: {exc}"}, indent=2)
    if isinstance(facts, dict):
        facts = facts.get("facts") or [facts]
    if not isinstance(facts, list) or not all(isinstance(item, dict) for item in facts):
        return json.dumps(
            {"ok": False, "error": "facts_json must contain a JSON array of objects"},
            indent=2,
        )
    return json.dumps(KnowledgeGraph().add_reviewed_facts(pid, facts), indent=2)


@mcp.tool(
    name="rebuild_knowledge_graph",
    description=(
        "Rebuild or refresh the local knowledge graph from all existing structured paper.json "
        "records. Safe to rerun; preserves LLM-reviewed facts and refreshes automatic facts."
    ),
)
def rebuild_knowledge_graph() -> str:
    from build_knowledge_graph import build

    return json.dumps(build(), indent=2)


# ---------------------------------------------------------------------------
# MCP resources: paper cards + figures as browsable URIs
# ---------------------------------------------------------------------------


@mcp.resource("sorghum://papers/index")
def resource_papers_index() -> str:
    return json.dumps(store.load_index(), indent=2)


@mcp.resource("sorghum://paper/{paper_id}")
def resource_paper(paper_id: str) -> str:
    data = store.load_paper(paper_id, light=True)
    if not data:
        return json.dumps({"error": f"unknown paper_id {paper_id}"})
    return json.dumps(data, indent=2)


@mcp.resource("sorghum://paper/{paper_id}/figure/{figure_id}")
def resource_figure(paper_id: str, figure_id: str) -> str:
    """Return figure metadata; binary PNG is available via get_paper_figure(include_base64=true)."""
    meta = store.get_figure_meta(paper_id, figure_id)
    if not meta:
        return json.dumps({"error": "figure not found"})
    return json.dumps(meta, indent=2)


@mcp.resource("sorghum://paper/{paper_id}/table/{table_id}")
def resource_table(paper_id: str, table_id: str) -> str:
    text = store.get_table(paper_id, table_id, fmt="markdown")
    return text or json.dumps({"error": "table not found"})


@mcp.prompt(
    name="sorghum_research_assistant",
    description="System-style prompt for answering sorghum protein research questions from this knowledge base.",
)
def sorghum_research_assistant() -> str:
    return (
        "You are a research assistant for sorghum protein bioavailability "
        "(Shakoor Lab). For open scientific questions call hybrid_search_sorghum_protein "
        "so both passage evidence and knowledge relationships are retrieved. "
        "When the user asks about a specific paper, figure, table, methods, or "
        "results, use the structured paper tools "
        "(list_structured_papers / get_paper / get_paper_section / "
        "list_paper_figures / get_paper_figure / list_paper_tables / get_paper_table). "
        "When the user wants to add a new paper, call add_paper with a DOI, "
        "PDF URL, or local path; vector and graph indexes update together. For deeper "
        "graph enrichment, call prepare_graph_enrichment and then add_graph_facts. "
        "Cite titles and DOIs. Distinguish raw vs wet-cooked vs fermented "
        "digestibility. If evidence is only an abstract, say so."
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
