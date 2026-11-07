#!/usr/bin/env python3
"""Ingest a local PDF with its bibliographic metadata, and retire the paper's abstract-only entry.

ingest_paper.py takes a DOI, URL or path, but for a local PDF it names the record after the file
("Paper #6.pdf"). This keeps the real title, DOI, authors and year, and removes the old catalogue
abstract (documents index, chunks, Chroma) once the full text is in.

  python scripts/ingest_local.py "/path/Paper #6.pdf" --doi 10.1002/cche.10643 \\
      --title "Comparative assessment of ..." --year 2023 --journal "Cereal Chemistry" \\
      --authors "Impa M. S.; Scott R. Bean"
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest_paper as ip  # noqa: E402


def retire_abstract(doi: str) -> dict:
    """Drop the abstract-only copies of a paper that now has full text."""
    doc_id = f"abstract:{doi}"
    kb = ip.KB
    idx_p = kb / "documents_index.json"
    idx = json.loads(idx_p.read_text())
    n_idx = len(idx)
    idx = [d for d in idx if d.get("doc_id") != doc_id]
    idx_p.write_text(json.dumps(idx, indent=2, ensure_ascii=False))
    ch_p = kb / "chunks.jsonl"
    lines = [ln for ln in ch_p.read_text().splitlines() if ln.strip()]
    n_ch = len(lines)
    lines = [ln for ln in lines if json.loads(ln).get("doc_id") != doc_id]
    ch_p.write_text("\n".join(lines) + "\n")
    removed = 0
    try:
        import chromadb
        from chromadb.utils import embedding_functions

        col = chromadb.PersistentClient(path=str(kb / "chroma")).get_collection(
            "sorghum_protein", embedding_function=embedding_functions.DefaultEmbeddingFunction())
        ex = col.get(where={"doc_id": doc_id})
        if ex.get("ids"):
            col.delete(ids=ex["ids"])
            removed = len(ex["ids"])
    except Exception as exc:  # the vector store is optional for this step
        return {"documents_index": n_idx - len(idx), "chunks": n_ch - len(lines), "chroma_error": str(exc)}
    return {"documents_index": n_idx - len(idx), "chunks": n_ch - len(lines), "chroma": removed}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf")
    ap.add_argument("--title", required=True)
    ap.add_argument("--doi", default="")
    ap.add_argument("--year", type=int)
    ap.add_argument("--journal", default="")
    ap.add_argument("--authors", default="", help="semicolon-separated")
    ap.add_argument("--landing-url", default="")
    a = ap.parse_args()
    doi = ip.normalize_doi(a.doi) if a.doi else None
    src = Path(a.pdf).expanduser()
    if src.read_bytes()[:5] != b"%PDF-":
        sys.exit(f"{src} is not a PDF")
    dest = ip.PAPERS / ip.safe_filename(a.title, doi)
    shutil.copy2(src, dest)
    meta = {"title": a.title, "doi": doi, "year": a.year, "journal": a.journal,
            "authors": [x.strip() for x in a.authors.split(";") if x.strip()],
            "landing_url": a.landing_url or (f"https://doi.org/{doi}" if doi else ""), "source": "local_pdf",
            "local_path": str(dest.relative_to(ip.ROOT)), "download_status": "downloaded"}
    meta = {k: v for k, v in meta.items() if v not in (None, "", [])}
    ip.update_json_list(ip.META / "downloaded.json", meta)
    ip.update_json_list(ip.META / "paper_catalog.json", meta)
    rec = ip.extract_one_pdf(dest, catalog_rec=meta, force=True)
    out = {"paper_id": rec.get("paper_id"), "title": rec.get("title"), "pages": (rec.get("stats") or {}).get("n_pages"),
           "tables": len(rec.get("tables") or []), "figures": len(rec.get("figures") or []),
           "vector": ip.index_paper_into_chroma(rec), "graph": ip.index_paper_into_graph(rec)}
    if doi:
        out["retired_abstract"] = retire_abstract(doi)
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
