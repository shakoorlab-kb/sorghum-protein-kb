#!/usr/bin/env python3
"""Ingest a new paper into the sorghum protein knowledge system.

Supports:
  - DOI (e.g. 10.3390/nu12041111 or https://doi.org/...)
  - Direct PDF URL
  - Local PDF path

Pipeline: download/copy PDF → structure extract → update catalogs → Chroma + graph upsert
"""
from __future__ import annotations

import hashlib
import os
import json
import re
import shutil
import time
import urllib.parse
from pathlib import Path

import requests

import structured_store as store

ROOT = Path(__file__).resolve().parents[1]
PAPERS = ROOT / "papers"
META = ROOT / "metadata"
KB = ROOT / "knowledge_base"
STRUCTURED = KB / "structured"
PAPERS.mkdir(exist_ok=True)
META.mkdir(exist_ok=True)

EMAIL = os.environ.get("KB_CONTACT_EMAIL", "")  # used by OpenAlex, Crossref and Unpaywall
HEADERS = {
    "User-Agent": f"SorghumProteinKB/1.0 (mailto:{EMAIL})",
    "Accept": "application/pdf,*/*",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\]\)\>\"\'<>]+", re.I)


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    v = re.sub(r"^https?://(dx\.)?doi\.org/", "", v, flags=re.I)
    m = DOI_RE.search(v)
    if not m:
        return None
    return m.group(0).rstrip(".,;").lower()


def safe_filename(title: str, doi: str | None) -> str:
    base = re.sub(r"[^\w\s\-]+", "", (title or doi or "paper")[:80])
    base = re.sub(r"\s+", "_", base).strip("_") or "paper"
    if doi:
        base = f"{base}__{re.sub(r'[^\w.\-]+', '_', doi)[-40:]}"
    return base[:180] + ".pdf"


def looks_like_pdf(content: bytes, content_type: str | None) -> bool:
    return content[:5] == b"%PDF-" or (
        bool(content_type) and "pdf" in content_type.lower() and content[:5] == b"%PDF-"
    )


def fetch_crossref(doi: str) -> dict:
    try:
        r = SESSION.get(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}", timeout=40)
        if r.status_code != 200:
            return {}
        msg = (r.json() or {}).get("message") or {}
        authors = []
        for a in msg.get("author") or []:
            name = " ".join(x for x in [a.get("given"), a.get("family")] if x)
            if name:
                authors.append(name)
        title = " ".join(msg.get("title") or []) or None
        year = None
        for key in ("published-print", "published-online", "created"):
            parts = ((msg.get(key) or {}).get("date-parts") or [[None]])[0]
            if parts and parts[0]:
                year = int(parts[0])
                break
        abstract = msg.get("abstract") or ""
        abstract = re.sub(r"<[^>]+>", " ", abstract)
        abstract = re.sub(r"\s+", " ", abstract).strip()
        container = " ".join(msg.get("container-title") or []) or None
        return {
            "title": title,
            "authors": authors,
            "year": year,
            "doi": doi,
            "abstract": abstract,
            "journal": container,
            "landing_url": f"https://doi.org/{doi}",
            "source": "crossref",
            "is_oa": None,
            "cited_by_count": msg.get("is-referenced-by-count") or 0,
        }
    except Exception:
        return {}


def unpaywall_urls(doi: str) -> tuple[list[str], dict]:
    meta = {}
    urls: list[str] = []
    try:
        r = SESSION.get(
            f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}",
            params={"email": EMAIL},
            timeout=30,
        )
        if r.status_code != 200:
            return urls, meta
        data = r.json()
        meta = {
            "title": data.get("title"),
            "year": data.get("year"),
            "doi": doi,
            "is_oa": bool(data.get("is_oa")),
            "journal": (data.get("journal_name") or None),
            "landing_url": data.get("doi_url") or f"https://doi.org/{doi}",
            "authors": [
                " ".join(
                    x
                    for x in [
                        (a.get("given") if isinstance(a, dict) else None),
                        (a.get("family") if isinstance(a, dict) else None),
                    ]
                    if x
                )
                for a in (data.get("z_authors") or [])
            ],
            "source": "unpaywall",
        }
        meta["authors"] = [a for a in meta["authors"] if a]
        for loc in [data.get("best_oa_location"), *(data.get("oa_locations") or [])]:
            if not loc:
                continue
            if loc.get("url_for_pdf"):
                urls.append(loc["url_for_pdf"])
            if loc.get("url"):
                urls.append(loc["url"])
    except Exception:
        pass
    # unique
    out, seen = [], set()
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out, meta


def europepmc_urls(doi: str) -> tuple[list[str], dict]:
    urls, meta = [], {}
    try:
        r = SESSION.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": f"DOI:{doi}", "format": "json", "pageSize": 3, "resultType": "core"},
            timeout=30,
        )
        r.raise_for_status()
        hits = ((r.json().get("resultList") or {}).get("result")) or []
        if not hits:
            return urls, meta
        w = hits[0]
        meta = {
            "title": w.get("title"),
            "year": int(w["pubYear"]) if str(w.get("pubYear", "")).isdigit() else None,
            "doi": doi,
            "pmcid": w.get("pmcid"),
            "pmid": w.get("pmid"),
            "abstract": w.get("abstractText") or "",
            "authors": [w["authorString"]] if w.get("authorString") else [],
            "is_oa": w.get("isOpenAccess") == "Y",
            "landing_url": f"https://doi.org/{doi}",
            "source": "europepmc",
        }
        pmcid = w.get("pmcid")
        if pmcid:
            urls += [
                f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/",
                f"https://europepmc.org/articles/{pmcid}?pdf=render",
            ]
    except Exception:
        pass
    return urls, meta


def download_bytes(url: str) -> bytes | None:
    try:
        r = SESSION.get(url, timeout=90, allow_redirects=True, headers=HEADERS)
        if r.status_code != 200:
            return None
        ctype = r.headers.get("Content-Type", "")
        if r.content[:5] == b"%PDF-":
            return r.content
        text = ""
        try:
            text = r.text[:400000]
        except Exception:
            return None
        m = re.search(
            r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
            text,
            re.I,
        )
        if not m:
            m = re.search(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', text, re.I)
        if not m:
            return None
        pdf_url = urllib.parse.urljoin(url, m.group(1))
        r2 = SESSION.get(pdf_url, timeout=90, allow_redirects=True, headers=HEADERS)
        if r2.status_code == 200 and r2.content[:5] == b"%PDF-":
            return r2.content
    except Exception:
        return None
    return None


def merge_meta(*metas: dict) -> dict:
    out: dict = {}
    for m in metas:
        for k, v in (m or {}).items():
            if v in (None, "", [], {}):
                continue
            if k not in out or not out[k]:
                out[k] = v
            elif k == "authors" and isinstance(v, list) and len(v) > len(out.get("authors") or []):
                out[k] = v
    return out


def already_have_doi(doi: str) -> Path | None:
    tag = re.sub(r"[^\w.\-]+", "_", doi)
    for p in PAPERS.glob("*.pdf"):
        if tag in p.name or doi.replace("/", "_") in p.name:
            return p
    return None


def update_json_list(path: Path, record: dict, key_fields: tuple[str, ...] = ("doi", "local_path")) -> None:
    items = []
    if path.exists():
        try:
            items = json.loads(path.read_text())
            if not isinstance(items, list):
                items = []
        except Exception:
            items = []

    def same(a: dict, b: dict) -> bool:
        for k in key_fields:
            av, bv = a.get(k), b.get(k)
            if av and bv and str(av).lower() == str(bv).lower():
                return True
        return False

    replaced = False
    for i, old in enumerate(items):
        if same(old, record):
            merged = dict(old)
            merged.update({k: v for k, v in record.items() if v not in (None, "", [])})
            items[i] = merged
            replaced = True
            break
    if not replaced:
        items.append(record)
    path.write_text(json.dumps(items, indent=2))


def refresh_structured_index() -> dict:
    from extract_paper_structure import OUT, build_index

    all_recs = []
    for d in sorted(OUT.iterdir()):
        if d.is_dir() and (d / "paper.json").exists():
            all_recs.append(json.loads((d / "paper.json").read_text()))
    build_index(all_recs)
    return {"n_papers": len(all_recs)}


def extract_one_pdf(pdf_path: Path, catalog_rec: dict | None = None, force: bool = True) -> dict:
    from extract_paper_structure import load_catalog_by_pdf, process_one

    catalog = load_catalog_by_pdf()
    if catalog_rec:
        if catalog_rec.get("doi"):
            catalog[catalog_rec["doi"].lower()] = catalog_rec
        catalog[pdf_path.name] = catalog_rec
    rec = process_one(pdf_path, catalog, force=force)
    refresh_structured_index()
    # keep the component and exact-text indexes in step with the new record
    try:
        from rich_enrich import rebuild_component_index

        rebuild_component_index()
    except Exception:
        pass
    try:
        import text_index

        text_index.update_paper(rec)
    except Exception:
        pass
    return rec


def index_paper_into_chroma(paper_rec: dict) -> dict:
    """Incrementally upsert one structured paper into Chroma."""
    try:
        import chromadb
        from chromadb.utils import embedding_functions
        from build_knowledge_base import chunk_text
    except Exception as e:
        return {"ok": False, "error": f"chroma unavailable: {e}"}

    paper_id = paper_rec.get("paper_id")
    title = paper_rec.get("title") or paper_id
    doi = paper_rec.get("doi")
    parts = chunk_text(store.record_index_text(paper_rec))
    if not parts:
        return {"ok": False, "error": "no text chunks"}

    try:
        ef = embedding_functions.DefaultEmbeddingFunction()
    except Exception:
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
    client = chromadb.PersistentClient(path=str(KB / "chroma"))
    col = client.get_or_create_collection(
        name="sorghum_protein",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )
    # delete old chunks for this paper
    try:
        existing = col.get(where={"doc_id": f"structured:{paper_id}"})
        if existing and existing.get("ids"):
            col.delete(ids=existing["ids"])
    except Exception:
        pass

    ids, docs, metas = [], [], []
    for i, part in enumerate(parts):
        cid = hashlib.sha1(f"structured:{paper_id}:{i}:{part[:40]}".encode()).hexdigest()[:16]
        ids.append(cid)
        docs.append(part)
        metas.append(
            {
                "title": (title or "")[:300],
                "source_type": "structured_paper",
                "path": f"knowledge_base/structured/{paper_id}/paper.json",
                "year": str(paper_rec.get("year") or ""),
                "doi": str(doi or ""),
                "doc_id": f"structured:{paper_id}",
                "chunk_index": i,
            }
        )
    batch = 64
    for i in range(0, len(ids), batch):
        col.add(
            ids=ids[i : i + batch],
            documents=docs[i : i + batch],
            metadatas=metas[i : i + batch],
        )
    return {"ok": True, "n_chunks": len(ids), "paper_id": paper_id}


def index_paper_into_graph(paper_rec: dict) -> dict:
    """Incrementally upsert one structured paper into the local property graph."""
    try:
        from knowledge_graph import KnowledgeGraph

        return KnowledgeGraph().index_paper(paper_rec)
    except Exception as e:
        return {"ok": False, "error": f"graph indexing failed: {e}"}


def ingest(
    source: str,
    title_hint: str | None = None,
    update_vector_index: bool = True,
    update_graph_index: bool = True,
    force_extract: bool = True,
) -> dict:
    """Main entry: ingest from DOI, URL, or local path."""
    source = (source or "").strip().strip('"').strip("'")
    if not source:
        return {"ok": False, "error": "Provide a DOI, PDF URL, or local PDF path."}

    result: dict = {"ok": False, "source": source}
    meta: dict = {}
    pdf_path: Path | None = None
    downloaded_from = None

    # Local path
    local = Path(source).expanduser()
    if local.exists() and local.suffix.lower() == ".pdf":
        title = title_hint or local.stem.replace("_", " ")
        dest = PAPERS / safe_filename(title, None)
        if local.resolve() != dest.resolve():
            # if already inside papers/, use it
            if local.resolve().parent == PAPERS.resolve():
                dest = local.resolve()
            else:
                shutil.copy2(local, dest)
        pdf_path = dest
        meta = {"title": title, "source": "local_path", "local_path": str(dest.relative_to(ROOT))}
        result["mode"] = "local_path"
        if store.resolve_paper_id(title):
            result["already_present"] = True

    else:
        doi = normalize_doi(source)
        urls: list[str] = []

        if doi:
            result["mode"] = "doi"
            result["doi"] = doi
            existing = already_have_doi(doi)
            xref = fetch_crossref(doi)
            up_urls, up_meta = unpaywall_urls(doi)
            ep_urls, ep_meta = europepmc_urls(doi)
            meta = merge_meta(xref, up_meta, ep_meta, {"doi": doi, "title": title_hint})
            if title_hint and not meta.get("title"):
                meta["title"] = title_hint
            urls = list(dict.fromkeys(up_urls + ep_urls + [f"https://doi.org/{doi}"]))
            if source.startswith("http") and source not in urls:
                urls.insert(0, source)
            if existing and existing.stat().st_size > 5000:
                pdf_path = existing
            if existing or store.resolve_paper_id(doi):
                result["already_present"] = True
        elif source.startswith("http://") or source.startswith("https://"):
            result["mode"] = "url"
            urls = [source]
            meta = {"title": title_hint or "paper", "landing_url": source, "source": "url"}
            if title_hint and store.resolve_paper_id(title_hint):
                result["already_present"] = True
        else:
            return {
                "ok": False,
                "error": (
                    f"Unrecognized source {source!r}. "
                    "Pass a DOI (10.xxxx/...), https PDF/article URL, or local .pdf path."
                ),
            }

        if pdf_path is None:
            content = None
            for u in urls:
                content = download_bytes(u)
                if content and len(content) > 4000:
                    downloaded_from = u
                    break
                time.sleep(0.1)
            if not content:
                update_json_list(
                    META / "failed_downloads.json",
                    {
                        **meta,
                        "download_status": "failed",
                        "link": meta.get("landing_url")
                        or (f"https://doi.org/{doi}" if doi else source),
                    },
                )
                # keep unavailable list note
                return {
                    "ok": False,
                    "error": (
                        "Could not download PDF (paywall or no OA link). "
                        "Try a direct PDF URL or a local file path."
                    ),
                    "doi": doi,
                    "link": meta.get("landing_url") or (f"https://doi.org/{doi}" if doi else source),
                    "tried_urls": urls[:8],
                    "metadata": meta,
                }
            title = meta.get("title") or title_hint or (doi or "paper")
            dest = PAPERS / safe_filename(re.sub(r"<[^>]+>", "", title), doi)
            dest.write_bytes(content)
            pdf_path = dest

    assert pdf_path is not None
    meta["local_path"] = str(pdf_path.relative_to(ROOT))
    meta["download_status"] = "downloaded"
    if downloaded_from:
        meta["downloaded_from"] = downloaded_from
    if "doi" not in meta or not meta["doi"]:
        # try from filename
        m = DOI_RE.search(pdf_path.name.replace("_", "/"))
        if m:
            meta["doi"] = normalize_doi(m.group(0))

    update_json_list(META / "downloaded.json", meta)
    update_json_list(META / "paper_catalog.json", meta)

    # Structure extract
    try:
        paper_rec = extract_one_pdf(pdf_path, catalog_rec=meta, force=force_extract)
    except Exception as e:
        return {
            "ok": False,
            "error": f"PDF saved but structure extraction failed: {e}",
            "pdf": str(pdf_path.relative_to(ROOT)),
            "metadata": meta,
        }

    chroma_info = None
    if update_vector_index:
        chroma_info = index_paper_into_chroma(paper_rec)
    graph_info = None
    if update_graph_index:
        graph_info = index_paper_into_graph(paper_rec)

    stats = paper_rec.get("stats") or {}
    already_present = bool(result.get("already_present"))
    message = (
        f"Already in the knowledge base: {paper_rec.get('title')} "
        f"(paper_id={paper_rec.get('paper_id')}). Re-extracted and refreshed vector/graph indexes. "
        "Use get_paper / get_paper_graph next."
        if already_present
        else (
            f"Ingested {paper_rec.get('title')} "
            f"(paper_id={paper_rec.get('paper_id')}). "
            "Updated vector and graph indexes. Use get_paper / get_paper_graph next."
        )
    )
    return {
        "ok": True,
        "already_present": already_present,
        "paper_id": paper_rec.get("paper_id"),
        "title": paper_rec.get("title"),
        "doi": paper_rec.get("doi"),
        "year": paper_rec.get("year"),
        "pdf": str(pdf_path.relative_to(ROOT)),
        "figures": stats.get("n_figures"),
        "tables": stats.get("n_tables"),
        "pages": stats.get("n_pages"),
        "sections": paper_rec.get("section_names"),
        "vector_index": chroma_info,
        "graph_index": graph_info,
        "message": message,
    }


if __name__ == "__main__":
    import sys

    src = sys.argv[1] if len(sys.argv) > 1 else ""
    out = ingest(
        src,
        update_vector_index="--no-vector" not in sys.argv,
        update_graph_index="--no-graph" not in sys.argv,
    )
    print(json.dumps(out, indent=2))
