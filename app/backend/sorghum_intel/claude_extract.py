"""Knowledge-graph extraction done by Claude Code agents instead of an API model.

Agents read whole documents and write one JSON file per document (or per page range of a
long document). The same checks as the API path apply on import: canonical entity names,
controlled types and relations, and every quote searched for on its page.

    claude-plan    DIR             batches of documents still to extract (by page budget)
    claude-task    DOC [--pages]   print a document's pages for an agent to read
    claude-check   FILE...         validate extraction files (exit 1 if anything needs fixing)
    claude-entities DIR            entity inventory across files, for cross-document resolution
    claude-import  DIR             load extraction files into Neo4j (run where Neo4j is)
    graph-audit                    structural checks of the loaded graph

File format: see deploy/CLAUDE_EXTRACTION.md.
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from pathlib import Path

from . import documents, graphdb, kg_extract
from .config import settings
from .documents import DocMeta

PAGE_CAP = 12000


# ---- documents -------------------------------------------------------------------------

def find_doc(doc_id: str) -> tuple[DocMeta, list[str]]:
    for p in documents.list_kb_papers():
        if p.id == doc_id:
            return p, documents.kb_page_texts(p)
    up = documents.upload_dir(doc_id)
    originals = sorted(up.glob("original.*")) if up.exists() else []
    if originals:
        pages = documents.upload_page_texts(originals[0])
        meta = DocMeta(id=doc_id, title=doc_id, origin="upload", file_path=str(originals[0]),
                       file_type=documents.file_type_of(originals[0]), n_pages=len(pages))
        return meta, pages
    raise SystemExit(f"unknown document {doc_id}")


def _range(spec: str | None, n: int) -> tuple[int, int]:
    if not spec:
        return 1, n
    a, _, b = spec.partition("-")
    return max(1, int(a)), min(n, int(b or a))


def task(doc_id: str, pages_spec: str | None = None) -> str:
    meta, pages = find_doc(doc_id)
    a, b = _range(pages_spec, len(pages))
    out = [
        f"DOCUMENT {meta.id}",
        f"title: {meta.title}",
        f"year: {meta.year or 'n.d.'}   type: {meta.doc_type}   doi: {meta.doi or '-'}   pages: {len(pages)}",
        f"authors: {', '.join(meta.authors[:8])}",
        f"this task covers pages {a}-{b}" + (" (a later part of a long document)" if a > 1 else ""),
        "",
    ]
    for p in range(a, b + 1):
        text = pages[p - 1].strip()
        if kg_extract.is_reference_page(text):
            out.append(f"=== PAGE {p} (reference list or near-empty page: skip) ===\n")
            continue
        out.append(f"=== PAGE {p} ===\n{text[:PAGE_CAP]}\n")
    return "\n".join(out)


def plan(out_dir: Path, budget: int = 110, ids: list[str] | None = None) -> dict:
    """Batches of documents with no extraction file yet, packed to about `budget` pages each.
    Documents longer than the budget are split into page ranges."""
    done = {f.name.split("__")[0].removesuffix(".json") for f in (out_dir / "extractions").glob("*.json")}
    docs = [d for d in documents.list_kb_papers() if d.id not in done and (not ids or d.id in ids)]
    units: list[dict] = []
    for d in sorted(docs, key=lambda d: -d.n_pages):
        if d.n_pages > budget:
            for i, start in enumerate(range(1, d.n_pages + 1, budget)):
                end = min(d.n_pages, start + budget - 1)
                units.append({"doc_id": d.id, "pages": f"{start}-{end}", "part": i + 1, "n": end - start + 1,
                              "title": d.title})
        else:
            units.append({"doc_id": d.id, "pages": None, "part": 0, "n": d.n_pages, "title": d.title})
    batches: list[list[dict]] = []
    for u in sorted(units, key=lambda u: -u["n"]):  # first-fit decreasing
        for b in batches:
            if sum(x["n"] for x in b) + u["n"] <= budget:
                b.append(u)
                break
        else:
            batches.append([u])
    plan_doc = {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "budget": budget,
                "documents": len(docs), "pages": sum(u["n"] for u in units),
                "batches": [{"id": i + 1, "pages": sum(u["n"] for u in b), "units": b}
                            for i, b in enumerate(batches)]}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "extractions").mkdir(exist_ok=True)
    (out_dir / "batches.json").write_text(json.dumps(plan_doc, indent=1))
    return plan_doc


# ---- files ----------------------------------------------------------------------------

def load_files(paths: list[Path]) -> dict[str, dict]:
    """Merge extraction files by doc_id (parts of a long document become one)."""
    merged: dict[str, dict] = {}
    for f in paths:
        data = json.loads(f.read_text())
        did = data.get("doc_id") or f.name.split("__")[0].removesuffix(".json")
        m = merged.setdefault(did, {"doc_id": did, "document": {}, "entities": [], "claims": [],
                                    "extractor": data.get("extractor", "claude-code"), "files": []})
        m["files"].append(f.name)
        if data.get("document"):
            doc = data["document"]
            cur = m["document"]
            if not cur:
                m["document"] = doc
            else:  # later parts add findings and studied entities
                cur["key_findings"] = (cur.get("key_findings") or []) + (doc.get("key_findings") or [])
                cur["studies"] = (cur.get("studies") or []) + (doc.get("studies") or [])
        m["entities"] += data.get("entities") or []
        m["claims"] += data.get("claims") or []
    return merged


def _check_doc(data: dict, pages: list[str]) -> dict:
    rec = kg_extract.build_records(data["doc_id"], pages, {}, data, data.get("extractor", "claude-code"))
    problems = list(rec["issues"])
    raw_rel = {str(c.get("relation", "")).upper() for c in data.get("claims") or []}
    bad_rel = sorted(r for r in raw_rel if r and r not in graphdb.RELATION_TYPES)
    if bad_rel:
        problems.append(f"relations not in the vocabulary (mapped or coerced): {bad_rel}")
    raw_types = {str(e.get("type", "")).upper() for e in data.get("entities") or []}
    bad_types = sorted(t for t in raw_types if t and t not in graphdb.ENTITY_TYPES)
    if bad_types:
        problems.append(f"entity types not in the vocabulary (coerced): {bad_types}")
    doc = data.get("document") or {}
    first_part = any("__part" not in f or "__part1." in f for f in data.get("files", []))
    if first_part and not doc.get("summary"):
        problems.append("document.summary is missing")
    n = len(rec["claims"])
    v = sum(1 for c in rec["claims"] if c["verified"])
    return {"doc_id": data["doc_id"], "claims": n, "verified": v, "entities": len(rec["entities"]),
            "findings": sum(1 for c in rec["claims"] if c["kind"] == "finding"),
            "unverified": [c for c in rec["claims"] if not c["verified"]], "problems": problems}


def check(paths: list[Path]) -> int:
    worst = 0
    for did, data in load_files(paths).items():
        try:
            _, pages = find_doc(did)
        except SystemExit as exc:
            print(f"{did}: {exc}")
            worst = 1
            continue
        r = _check_doc(data, pages)
        blocking = [p for p in r["problems"] if "moved" not in p and "coerced" not in p]
        status = "OK" if not r["unverified"] and not blocking else "FIX"
        print(f"[{status}] {did}: {r['claims']} claims ({r['verified']} verified, {r['findings']} findings), "
              f"{r['entities']} entities")
        for p in r["problems"]:
            print(f"   - {p}")
        if status == "FIX":
            worst = 1
    return worst


def entity_inventory(dir_: Path) -> dict:
    """All entities across extraction files after canonicalisation: type -> name -> info."""
    inv: dict[str, dict[str, dict]] = defaultdict(dict)
    for did, data in load_files(sorted((dir_ / "extractions").glob("*.json"))).items():
        names = list(data.get("entities") or [])
        for c in data.get("claims") or []:
            names.append({"name": c.get("subject"), "type": c.get("subject_type")})
            names.append({"name": c.get("object"), "type": c.get("object_type")})
        for e in names:
            if not isinstance(e, dict) or not e.get("name"):
                continue
            cname, ctype = kg_extract.canonical(str(e["name"]), kg_extract._etype(str(e.get("type") or "")))
            item = inv[ctype].setdefault(graphdb.entity_id(ctype, cname),
                                         {"name": cname, "variants": set(), "docs": set()})
            item["variants"].add(str(e["name"]))
            item["docs"].add(did)
    return {t: {k: {"name": v["name"], "variants": sorted(v["variants"])[:12], "n_docs": len(v["docs"])}
                for k, v in sorted(items.items(), key=lambda kv: -len(kv[1]["docs"]))}
            for t, items in sorted(inv.items())}


# ---- import (needs Neo4j) ----------------------------------------------------------------

def reset_graph() -> None:
    """Remove every entity, claim and document profile; documents and passages stay."""
    for q in ("MATCH (c:Claim) WITH c LIMIT 5000 DETACH DELETE c RETURN count(*) AS n",
              "MATCH (e:Entity) WITH e LIMIT 5000 DETACH DELETE e RETURN count(*) AS n"):
        while graphdb.run(q)[0]["n"]:
            pass
    graphdb.run_write("MATCH (d:Document) SET d.extracted_at = null, d.status = 'indexed', "
                      "d.summary = null, d.key_findings = null, d.study_type = null")


def import_dir(dir_: Path, only: list[str] | None = None) -> dict:
    from .indexer import pages_for
    files = sorted((dir_ / "extractions").glob("*.json")) if (dir_ / "extractions").exists() \
        else sorted(dir_.glob("*.json"))
    merged = load_files(files)
    report = {"documents": 0, "claims": 0, "verified": 0, "skipped": [], "issues": {}}
    all_touched: set[str] = set()
    for did, data in merged.items():
        if only and did not in only:
            continue
        doc = graphdb.get_document(did)
        if not doc:
            report["skipped"].append(f"{did}: not indexed on this server")
            continue
        pages = pages_for(doc)
        by_page: dict[int, list[dict]] = defaultdict(list)
        for c in graphdb.chunks_for_document(did):
            by_page[c["page"]].append(c)
        rec = kg_extract.build_records(did, pages, by_page, data, data.get("extractor", "claude-code"))
        # Entities the document studies (from the document profile) must exist as nodes.
        studies = []
        for s in (data.get("document") or {}).get("studies") or []:
            if not isinstance(s, dict) or not s.get("name"):
                continue
            cname, ctype = kg_extract.canonical(str(s["name"]), kg_extract._etype(str(s.get("type") or "")))
            eid = graphdb.entity_id(ctype, cname)
            if eid not in {e["id"] for e in rec["entities"]}:
                rec["entities"].append({"id": eid, "type": ctype, "name": cname, "aliases": [], "description": ""})
            studies.append({"entity_id": eid, "role": str(s.get("role") or ctype.lower())[:40]})
        touched = set(graphdb.clear_document_claims(did))
        graphdb.write_extraction(did, rec["entities"], rec["mentions"], rec["claims"])
        prof = data.get("document") or {}
        graphdb.write_document_profile(did, {
            "summary": str(prof.get("summary") or "")[:1500],
            "study_type": str(prof.get("study_type") or "")[:40],
            "key_findings": [str(x)[:400] for x in (prof.get("key_findings") or [])][:15],
        }, studies)
        graphdb.refresh_document_links(did)
        touched.update(e["id"] for e in rec["entities"])
        all_touched |= touched
        n, v = len(rec["claims"]), sum(1 for c in rec["claims"] if c["verified"])
        graphdb.set_document_state(did, extracted_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                                   extract_model=data.get("extractor", "claude-code"), status="ready",
                                   extract_counts=json.dumps({"claims": n, "verified": v}))
        report["documents"] += 1
        report["claims"] += n
        report["verified"] += v
        if rec["issues"]:
            report["issues"][did] = rec["issues"][:20]
        print(f"imported {did}: {n} claims, {v} verified", flush=True)
    graphdb.refresh_relations(all_touched)
    graphdb.prune_orphan_entities()
    return report


def audit() -> dict:
    """Structural checks of the loaded graph."""
    q = graphdb.run
    out = {
        "stats": {k: v for k, v in graphdb.stats().items() if not isinstance(v, list)},
        "unverified_claims": q("MATCH (c:Claim) WHERE NOT c.verified RETURN count(c) AS n")[0]["n"],
        "claims_by_kind": q("MATCH (c:Claim) RETURN coalesce(c.kind,'finding') AS kind, count(*) AS n"),
        "documents_without_graph": q(
            "MATCH (d:Document) WHERE d.extracted_at IS NULL AND d.doc_type <> 'supplement' "
            "RETURN d.id AS id, d.title AS title ORDER BY id"),
        "entities_without_claims": q(
            "MATCH (e:Entity) WHERE NOT (e)-[:SUBJECT_OF|OBJECT]-() RETURN count(e) AS n")[0]["n"],
        # The same name under several types usually means one concept typed inconsistently.
        "type_conflicts": q("""
            MATCH (e:Entity) WITH split(e.id, ':')[1] AS key, collect(e.type) AS types, collect(e.name)[0] AS name
            WHERE size(types) > 1 RETURN name, types ORDER BY size(types) DESC LIMIT 60"""),
        # Relation/type combinations that are unusual enough to read again.
        "suspicious": q("""
            MATCH (s:Entity)-[:SUBJECT_OF]->(c:Claim)-[:OBJECT]->(o:Entity)
            WHERE (c.relation = 'ENCODES' AND NOT (s.type IN ['GENE','MARKER'] AND o.type = 'PROTEIN'))
               OR (c.relation = 'IS_A' AND s.type <> o.type)
               OR (c.relation = 'MEASURED_BY' AND o.type <> 'METHOD')
            RETURN c.id AS id, s.name AS subject, c.relation AS relation, o.name AS object,
                   c.doc_id AS doc, c.page AS page LIMIT 80"""),
        "hubs": q("""
            MATCH (e:Entity) RETURN e.name AS name, e.type AS type, e.degree AS degree, e.n_docs AS docs
            ORDER BY e.degree DESC LIMIT 15"""),
    }
    return out
