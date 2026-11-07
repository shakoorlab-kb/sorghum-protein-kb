"""Ingestion: documents -> chunks + embeddings -> Neo4j -> LLM graph extraction.

Jobs run one at a time on a background thread (page extraction inside a job runs in
parallel). Every step is idempotent per document: re-indexing replaces that
document's chunks and claims and recomputes the relations they touched.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import httpx

from . import documents, embed, graphdb, kg_extract, store
from .config import settings
from .documents import DocMeta

log = logging.getLogger("sorghum_intel.indexer")

CHUNK_CHARS = 1100
CHUNK_OVERLAP = 160

_jobs = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job")
_cancel: set[str] = set()
_cancel_lock = threading.Lock()


class Cancelled(Exception):
    pass


# ---- chunking ------------------------------------------------------------------

def chunk_page(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    if len(text) <= CHUNK_CHARS:
        return [text]
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            # Break at a sentence or line end near the limit.
            window = text[start + CHUNK_CHARS // 2:end]
            cut = max(window.rfind(". "), window.rfind("\n"))
            if cut > 0:
                end = start + CHUNK_CHARS // 2 + cut + 1
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return out


def build_chunks(meta: DocMeta, pages: list[str]) -> list[dict]:
    chunks = []
    for pno, text in enumerate(pages, start=1):
        for idx, piece in enumerate(chunk_page(text)):
            chunks.append({"id": f"{meta.id}:p{pno}:c{idx}", "page": pno, "idx": idx, "text": piece})
    vectors = embed.embed_passages([f"{meta.title}\n{c['text']}" for c in chunks])
    for c, v in zip(chunks, vectors):
        c["embedding"] = v
    return chunks


# ---- steps ----------------------------------------------------------------------

def index_document(meta: DocMeta, pages: list[str]) -> int:
    graphdb.upsert_document(meta.to_dict())
    chunks = build_chunks(meta, pages)
    affected = graphdb.replace_chunks(meta.id, chunks)
    graphdb.refresh_relations(affected)
    graphdb.set_document_state(meta.id, status="indexed", indexed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                               extracted_at=None, n_chunks=len(chunks))
    return len(chunks)


def extract_document(meta_id: str, title: str, pages: list[str],
                     on_page: Callable[[int, int], None] | None = None,
                     should_stop: Callable[[], bool] | None = None) -> dict:
    """LLM extraction of every page; returns counts."""
    chunks = graphdb.chunks_for_document(meta_id)
    by_page: dict[int, list[dict]] = {}
    for c in chunks:
        by_page.setdefault(c["page"], []).append(c)
    # Re-extraction replaces earlier claims of this document.
    touched: set[str] = set(graphdb.clear_document_claims(meta_id))
    counts = {"pages": len(pages), "skipped": 0, "claims": 0, "verified": 0, "entities": 0, "errors": 0}
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, settings.extract_workers)) as pool:
        futs = {
            pool.submit(kg_extract.extract_page, meta_id, title, pno, pages, by_page): pno
            for pno in range(1, len(pages) + 1)
        }
        for fut in as_completed(futs):
            if should_stop and should_stop():
                for f in futs:
                    f.cancel()
                raise Cancelled()
            done += 1
            try:
                res = fut.result()
            except Exception as exc:  # one bad page must not sink the document
                counts["errors"] += 1
                log.warning("extract %s p%s failed: %s", meta_id, futs[fut], exc)
                if on_page:
                    on_page(done, len(pages))
                continue
            if res.get("skipped"):
                counts["skipped"] += 1
            graphdb.write_extraction(meta_id, res["entities"], res["mentions"], res["claims"])
            counts["claims"] += len(res["claims"])
            counts["verified"] += sum(1 for c in res["claims"] if c["verified"])
            counts["entities"] += len(res["entities"])
            touched.update(e["id"] for e in res["entities"])
            if on_page:
                on_page(done, len(pages))
    graphdb.refresh_document_links(meta_id)
    graphdb.refresh_relations(touched)
    if counts["errors"] < max(1, len(pages) // 3):
        graphdb.set_document_state(meta_id, extracted_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                                   extract_model=settings.extract_model, status="ready",
                                   extract_counts=str(counts))
    else:
        graphdb.set_document_state(meta_id, status="extract_failed", extract_counts=str(counts))
    return counts


def _title_key(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def import_kb_citations() -> int:
    """CITES edges between library documents.

    Two sources: the KB's own property graph, and a scan of every document's text (reference
    pages included) for the DOI or the title of another library document.
    """
    pairs: set[tuple[str, str]] = set()
    db = settings.kb_dir / "knowledge_base" / "graph" / "knowledge_graph.sqlite3"
    if db.exists():
        con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
        try:
            pm = dict(con.execute("SELECT node_id, paper_id FROM paper_map").fetchall())
            rows = con.execute("SELECT source_id, target_id FROM edges WHERE relation='CITES'").fetchall()
        finally:
            con.close()
        pairs |= {(pm[a], pm[b]) for a, b in rows if a in pm and b in pm}
    # Supplements share their paper's title and DOI, so they are neither sources nor targets.
    graphdb.run_write("MATCH (s:Document {doc_type: 'supplement'})-[r:CITES]-() DELETE r")
    docs = graphdb.run("MATCH (d:Document) WHERE coalesce(d.doc_type, '') <> 'supplement' "
                       "RETURN d.id AS id, d.title AS title, d.doi AS doi, "
                       "d.origin AS origin, d.file_path AS file_path, d.file_type AS file_type")
    titles = {d["id"]: _title_key(d["title"])[:60] for d in docs if len(_title_key(d["title"])) > 30}
    dois = {(d["doi"] or "").lower(): d["id"] for d in docs if d.get("doi")}
    for d in docs:
        try:
            text = "\n".join(pages_for(d))
        except Exception:
            continue
        flat = _title_key(text.replace("-\n", ""))
        for other, key in titles.items():
            if other != d["id"] and key in flat:
                pairs.add((d["id"], other))
        for m in re.findall(r"10\.\d{4,9}/[^\s,;]+", text.lower()):
            target = dois.get(m.rstrip(".)"))
            if target and target != d["id"]:
                pairs.add((d["id"], target))
    rows = [{"src": a, "dst": b} for a, b in sorted(pairs)]
    for i in range(0, len(rows), 500):
        graphdb.write_citations(rows[i:i + 500])
    return len(rows)


# ---- metadata lookup for uploads -------------------------------------------------

def openalex_lookup(doi: str) -> dict:
    if not doi:
        return {}
    try:
        r = httpx.get(f"https://api.openalex.org/works/doi:{doi}", timeout=20,
                      params={"mailto": os.environ.get("KB_CONTACT_EMAIL", "")})
        if r.status_code != 200:
            return {}
        w = r.json()
    except (httpx.HTTPError, ValueError):
        return {}
    loc = w.get("primary_location") or {}
    best = w.get("best_oa_location") or {}
    abstract = ""
    inv = w.get("abstract_inverted_index") or {}
    if inv:
        pos = sorted((p, word) for word, ps in inv.items() for p in ps)
        abstract = " ".join(word for _, word in pos)
    return {
        "title": w.get("title") or "",
        "year": w.get("publication_year"),
        "journal": ((loc.get("source") or {}).get("display_name")) or "",
        "authors": [a["author"]["display_name"] for a in (w.get("authorships") or []) if a.get("author")][:60],
        "abstract": abstract[:6000],
        "pdf_url": best.get("pdf_url") or "",
        "pdf_urls": [u for u in dict.fromkeys(
            [best.get("pdf_url") or ""] + [loc.get("pdf_url") or "" for loc in (w.get("locations") or [])]) if u],
        "pmcid": ((w.get("ids") or {}).get("pmcid") or "").rsplit("/", 1)[-1],
        "type": w.get("type") or "",
    }


def _apply_openalex(meta: DocMeta) -> None:
    info = openalex_lookup(meta.doi)
    if not info:
        return
    meta.title = info["title"] or meta.title
    meta.year = info["year"] or meta.year
    meta.journal = info["journal"] or meta.journal
    meta.authors = info["authors"] or meta.authors
    meta.abstract = info["abstract"] or meta.abstract
    meta.url = f"https://doi.org/{meta.doi}"
    t = info["type"]
    meta.doc_type = "thesis" if t == "dissertation" else "paper" if t in ("article", "review", "preprint") else meta.doc_type


# ---- jobs -------------------------------------------------------------------------

def _run(jid: str, fn: Callable[[str], dict | None]) -> None:
    store.update_job(jid, status="running", message="starting")
    try:
        detail = fn(jid) or {}
        store.update_job(jid, status="done", progress=1.0, message=detail.pop("message", "done"), detail=detail)
    except Cancelled:
        store.update_job(jid, status="failed", message="cancelled")
    except Exception as exc:
        log.exception("job %s failed", jid)
        store.update_job(jid, status="failed", message=f"{type(exc).__name__}: {exc}",
                         detail={"trace": traceback.format_exc()[-3000:]})
    finally:
        with _cancel_lock:
            _cancel.discard(jid)


def _stopper(jid: str) -> Callable[[], bool]:
    return lambda: jid in _cancel


def cancel_job(jid: str) -> None:
    with _cancel_lock:
        _cancel.add(jid)


def submit(kind: str, label: str, fn: Callable[[str], dict | None], detail: dict | None = None) -> str:
    jid = store.create_job(kind, label, detail)
    _jobs.submit(_run, jid, fn)
    return jid


def _ingest_file(jid: str, doc_id: str, path: Path, original_name: str, extract: bool) -> dict:
    store.update_job(jid, progress=0.02, message="reading pages")
    pages = documents.upload_page_texts(path)
    meta = documents.upload_meta(doc_id, path, pages, original_name)
    if meta.doi:
        store.update_job(jid, progress=0.05, message=f"looking up {meta.doi}")
        _apply_openalex(meta)
    store.update_job(jid, progress=0.1, message=f"embedding {len(pages)} pages")
    n = index_document(meta, pages)
    result = {"doc_id": doc_id, "title": meta.title, "pages": len(pages), "chunks": n}
    try:
        result["citations"] = import_kb_citations()
    except Exception as exc:
        log.warning("citation linking failed: %s", exc)
    if extract and settings.extract_mode == "openrouter" and settings.llm_enabled:
        def on_page(done: int, total: int) -> None:
            store.update_job(jid, progress=0.15 + 0.85 * done / max(total, 1),
                             message=f"building graph: page {done}/{total}")
        result["extraction"] = extract_document(doc_id, meta.title, pages, on_page, _stopper(jid))
    result["message"] = f"{meta.title[:80]}: {len(pages)} pages, {n} passages" + (
        f", {result['extraction']['claims']} claims" if "extraction" in result
        else "; graph pending (built by Claude Code extraction)" if settings.extract_mode == "claude" else "")
    return result


def submit_upload(filename: str, data: bytes, extract: bool = True) -> str:
    doc_id, path = documents.save_upload(filename, data)
    return submit("ingest", filename, lambda jid: _ingest_file(jid, doc_id, path, filename, extract),
                  {"doc_id": doc_id})


BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                  "Version/17.0 Safari/605.1.15",
    "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
}
DOI_ONLY = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/\S+)$", re.I)


def submit_url(source: str, extract: bool = True) -> str:
    source = source.strip()

    def work(jid: str) -> dict:
        candidates, doi = [source], ""
        m = DOI_ONLY.match(source)
        if m:
            doi = m.group(1)
            store.update_job(jid, message=f"finding an open-access PDF for {doi}")
            info = openalex_lookup(doi)
            candidates = list(info.get("pdf_urls") or [])
            if info.get("pmcid"):
                candidates.append(f"https://europepmc.org/articles/{info['pmcid']}?pdf=render")
            if not candidates:
                raise RuntimeError(f"no open-access PDF found for {doi}; download it and upload the file")
        r, errors = None, []
        for url in candidates:
            store.update_job(jid, message=f"downloading {url[:120]}")
            try:
                resp = httpx.get(url, timeout=90, follow_redirects=True, headers=BROWSER_HEADERS)
                resp.raise_for_status()
                if doi and resp.content[:5] != b"%PDF-":
                    raise RuntimeError("not a PDF (probably a login or cookie page)")
                r = resp
                break
            except Exception as exc:
                errors.append(f"{url[:80]}: {exc}"[:200])
        if r is None:
            raise RuntimeError("could not download an open-access copy; upload the PDF instead. "
                               + " | ".join(errors))
        ctype = r.headers.get("content-type", "")
        name = Path(httpx.URL(url).path).name or "download"
        if r.content[:5] == b"%PDF-":
            name = name if name.lower().endswith(".pdf") else f"{name}.pdf"
        elif "html" in ctype:
            name = f"{Path(name).stem or 'page'}.html"
        elif not Path(name).suffix:
            name = f"{name}.txt"
        doc_id, path = documents.save_upload(name, r.content)
        res = _ingest_file(jid, doc_id, path, name, extract)
        if doi and res.get("doc_id"):
            graphdb.set_document_state(res["doc_id"], doi=doi, url=f"https://doi.org/{doi}")
        return res

    return submit("ingest", source[:120], work)


def submit_kb_sync(extract: bool = True, limit: int | None = None, only: list[str] | None = None,
                   force: bool = False) -> str:
    def work(jid: str) -> dict:
        stop = _stopper(jid)
        all_papers = documents.list_kb_papers()
        papers = all_papers + documents.list_kb_supplements(all_papers)
        if only:
            papers = [p for p in papers if p.id in only or p.parent_id in only]
        state = graphdb.documents_state()
        to_index = [p for p in papers if force or state.get(p.id, {}).get("content_hash") != p.content_hash]
        if limit:
            to_index = to_index[:limit]
        # Papers whose text did not change still pick up corrected titles, journals and authors.
        stale = {p.id for p in to_index}
        for p in papers:
            if p.id not in stale and p.id in state:
                graphdb.upsert_document(p.to_dict())
        report = {"kb_papers": len(papers), "indexed": 0, "extracted": 0, "claims": 0, "errors": []}
        total = len(to_index)
        for i, p in enumerate(to_index):
            if stop():
                raise Cancelled()
            store.update_job(jid, progress=0.3 * i / max(total, 1),
                             message=f"indexing {i + 1}/{total}: {p.title[:70]}")
            try:
                index_document(p, documents.kb_page_texts(p))
                report["indexed"] += 1
            except Exception as exc:
                report["errors"].append(f"{p.id}: {exc}")
        store.update_job(jid, progress=0.3, message="importing citations")
        report["citations"] = import_kb_citations()
        if extract and settings.extract_mode == "openrouter" and settings.llm_enabled:
            state = graphdb.documents_state()
            pending = [p for p in papers if p.doc_type != "supplement" and not state.get(p.id, {}).get("extracted")]
            if limit:
                pending = pending[:limit]
            for i, p in enumerate(pending):
                if stop():
                    raise Cancelled()
                base = 0.3 + 0.7 * i / max(len(pending), 1)

                def on_page(done: int, tot: int, base=base, i=i, p=p) -> None:
                    store.update_job(
                        jid, progress=base + 0.7 / max(len(pending), 1) * done / max(tot, 1),
                        message=f"graph extraction {i + 1}/{len(pending)}: {p.title[:60]} (page {done}/{tot})",
                    )
                try:
                    c = extract_document(p.id, p.title, documents.kb_page_texts(p), on_page, stop)
                    report["extracted"] += 1
                    report["claims"] += c["claims"]
                except Cancelled:
                    raise
                except Exception as exc:
                    report["errors"].append(f"{p.id}: {exc}")
        graphdb.prune_orphan_entities()
        report["message"] = (f"{report['indexed']} indexed, {report['extracted']} extracted, "
                             f"{report['claims']} claims, {len(report['errors'])} errors")
        return report

    return submit("kb_sync", "Sync Sorghum KB library", work)


def submit_reextract(doc_id: str) -> str:
    def work(jid: str) -> dict:
        doc = graphdb.get_document(doc_id)
        if not doc:
            raise RuntimeError("document not found")
        pages = pages_for(doc)

        def on_page(done: int, total: int) -> None:
            store.update_job(jid, progress=done / max(total, 1), message=f"page {done}/{total}")
        counts = extract_document(doc_id, doc["title"], pages, on_page, _stopper(jid))
        return {**counts, "doc_id": doc_id, "message": f"{counts['claims']} claims from {counts['pages']} pages"}

    return submit("extract", f"Rebuild graph: {doc_id}", work, {"doc_id": doc_id})


def pages_for(doc: dict) -> list[str]:
    if doc.get("doc_type") == "supplement":
        return documents.supplement_pages(Path(doc.get("text_path") or ""))
    if doc.get("origin") == "kb":
        meta = DocMeta(id=doc["id"], title=doc["title"], origin="kb", file_path=doc["file_path"], file_type="pdf")
        return documents.kb_page_texts(meta)
    return documents.upload_page_texts(Path(doc["file_path"]))
