#!/usr/bin/env python3
"""Exact (substring) search over every word the KB holds.

Semantic search embeds meaning, so it cannot reliably find identifiers such as
"PI 533919", "IS 11167", "Sobic.001G123400" or a DOI. This index stores the complete
text of every page (text layer + OCR), every table row, figure text, and the abstracts
of catalogued papers without a PDF, in a SQLite FTS5 table with the trigram tokenizer,
so any substring of 3+ characters can be matched.

  python scripts/text_index.py build              # full rebuild
  python scripts/text_index.py search "PI 533919"
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "knowledge_base"
STRUCTURED = KB / "structured"
META = ROOT / "metadata"
DB_PATH = KB / "text_index.sqlite3"

# "PI 533919", "PI533919", "PI-533919", "IS 11167", "Tx430", "BTx623", "SC 748" ...
ID_RE = re.compile(r"\b([A-Za-z]{1,6})[\s\-_.]{0,2}(\d{3,})\b")
DOI_RE = re.compile(r"10\.\d{4,9}/\S+")
# words that precede numbers in ordinary prose ("in 2009", "page 112") are not accession prefixes
NOT_PREFIX = {"in", "on", "of", "at", "to", "by", "and", "or", "the", "from", "year", "years", "since",
              "for", "vs", "page", "pages", "table", "fig", "figure", "about", "than", "over", "under", "n"}
SNIPPET = 170


def _connect(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5("
        "text, paper_id UNINDEXED, title UNINDEXED, doi UNINDEXED, year UNINDEXED, "
        "page UNINDEXED, kind UNINDEXED, ref UNINDEXED, tokenize='trigram')"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS papers (paper_id TEXT PRIMARY KEY, title TEXT, doi TEXT, "
        "n_pages INTEGER, n_pages_ocr INTEGER, n_image_regions_ocr INTEGER, n_tables INTEGER, "
        "n_figures INTEGER, has_pdf INTEGER, n_supplements INTEGER)"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)")}
    if "n_supplements" not in cols:  # index built before supplements existed
        conn.execute("ALTER TABLE papers ADD COLUMN n_supplements INTEGER DEFAULT 0")
    return conn


def _paper_rows(rec: dict, paper_dir: Path) -> list[tuple]:
    from structured_store import table_rows

    pid = rec.get("paper_id") or paper_dir.name
    base = (pid, rec.get("title") or pid, rec.get("doi") or "", str(rec.get("year") or ""))
    rows = []

    def add(text: str, page, kind: str, ref: str = ""):
        if text and text.strip():
            rows.append((text, *base, str(page or ""), kind, ref))

    add(f"{rec.get('title') or ''}\n{rec.get('abstract') or ''}", 1, "abstract")
    pages_dir = paper_dir / "pages"
    for page_file in sorted(pages_dir.glob("page_*.txt")):
        add(page_file.read_text(errors="ignore"), int(page_file.stem.split("_")[1]), "page")
    for tab in rec.get("tables") or []:
        body = "\n".join(" | ".join(str(c) for c in r) for r in table_rows(pid, tab))
        add(f"{tab.get('caption_guess') or ''}\n{body}", tab.get("page"), "table", str(tab.get("id") or ""))
    for fig in rec.get("figures") or []:
        blob = "\n".join(fig.get(k) or "" for k in ("caption_guess", "nearby_text", "ocr_text"))
        add(blob, fig.get("page"), "figure", str(fig.get("id") or ""))
    refs = rec.get("references") or []
    if refs:
        add("\n".join(str(r) for r in refs), "", "references")
    from supplements import supplement_texts

    for entry, text in supplement_texts(rec):
        add(text, "", "supplement", entry.get("file") or "")
    return rows


def _paper_summary(rec: dict) -> tuple:
    stats = rec.get("stats") or {}
    return (
        rec.get("paper_id"), rec.get("title"), rec.get("doi") or "",
        stats.get("n_pages") or 0, stats.get("n_pages_ocr") or 0, stats.get("n_image_regions_ocr") or 0,
        len(rec.get("tables") or []), len(rec.get("figures") or []), 1,
        len(rec.get("supplements") or []),
    )


def update_paper(rec: dict, conn: sqlite3.Connection | None = None) -> dict:
    """Replace one structured paper's rows (called after each ingest)."""
    own = conn is None
    conn = conn or _connect()
    pid = rec.get("paper_id")
    paper_dir = STRUCTURED / pid
    conn.execute("DELETE FROM passages WHERE paper_id = ?", (pid,))
    rows = _paper_rows(rec, paper_dir)
    conn.executemany("INSERT INTO passages VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.execute("INSERT OR REPLACE INTO papers VALUES (?,?,?,?,?,?,?,?,?,?)", _paper_summary(rec))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('updated_at', ?)", (time.strftime("%Y-%m-%d %H:%M"),))
    if own:
        conn.commit()
        conn.close()
    return {"ok": True, "paper_id": pid, "passages": len(rows)}


def _catalog_abstract_rows() -> list[tuple]:
    """Papers known only from the catalog (paywalled/not downloaded): title + abstract."""
    path = META / "paper_catalog.json"
    if not path.exists():
        return []
    rows = []
    for p in json.loads(path.read_text()):
        if p.get("download_status") in ("downloaded", "already_present"):
            continue
        title = p.get("title") or ""
        abstract = p.get("abstract") or ""
        if len(title) + len(abstract) < 20:
            continue
        pid = "catalog:" + (p.get("doi") or hashlib.sha1(title.encode()).hexdigest()[:12])
        rows.append((f"{title}\n{abstract}", pid, title, p.get("doi") or "", str(p.get("year") or ""),
                     "", "catalog_abstract", ""))
    return rows


def build(path: Path = DB_PATH) -> dict:
    tmp = path.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    conn = _connect(tmp)
    n_papers = n_rows = 0
    for rec_path in sorted(STRUCTURED.glob("*/paper.json")):
        rec = json.loads(rec_path.read_text())
        src = rec.get("source_pdf")
        if src and not (ROOT / src).exists():
            continue  # stale record whose PDF was removed or quarantined
        n_rows += update_paper(rec, conn)["passages"]
        n_papers += 1
    cat = _catalog_abstract_rows()
    conn.executemany("INSERT INTO passages VALUES (?,?,?,?,?,?,?,?)", cat)
    lab = update_lab(conn)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('built_at', ?)", (time.strftime("%Y-%m-%d %H:%M"),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('n_catalog_abstracts', ?)", (str(len(cat)),))
    conn.commit()
    conn.execute("INSERT INTO passages(passages) VALUES ('optimize')")
    conn.commit()
    conn.close()
    tmp.replace(path)
    return {"ok": True, "papers": n_papers, "passages": n_rows, "catalog_abstracts": len(cat),
            "lab_datasets": lab["datasets"], "lab_samples": lab["samples"], "db": str(path)}


def update_lab(conn: sqlite3.Connection | None = None) -> dict:
    """Replace the rows of the lab's own datasets (lab_data/, parsed by scripts/lab_data.py)."""
    from lab_data import index_rows

    own = conn is None
    conn = conn or _connect()
    conn.execute("DELETE FROM passages WHERE kind = 'lab_data'")
    rows = index_rows()
    conn.executemany("INSERT INTO passages VALUES (?,?,?,?,?,?,?,?)", rows)
    n_ds = len({r[1] for r in rows})
    n_samples = sum(1 for r in rows if r[7] != "dataset card")
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('n_lab_datasets', ?)", (str(n_ds),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('n_lab_samples', ?)", (str(n_samples),))
    if own:
        conn.commit()
        conn.close()
    return {"ok": True, "datasets": n_ds, "samples": n_samples}


def coverage(conn: sqlite3.Connection | None = None) -> dict:
    own = conn is None
    conn = conn or _connect()
    r = conn.execute(
        "SELECT COUNT(*), SUM(n_pages), SUM(n_pages_ocr), SUM(n_image_regions_ocr), SUM(n_tables), SUM(n_figures), "
        "SUM(n_supplements), SUM(n_supplements > 0) FROM papers"
    ).fetchone()
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    if own:
        conn.close()
    return {
        "papers_with_full_text": r[0] or 0, "pages": r[1] or 0, "pages_ocr": r[2] or 0,
        "image_regions_ocr": r[3] or 0, "tables": r[4] or 0, "figures": r[5] or 0,
        "supplement_files": r[6] or 0, "papers_with_supplements": r[7] or 0,
        "catalog_abstracts": int(meta.get("n_catalog_abstracts") or 0),
        "lab_datasets": int(meta.get("n_lab_datasets") or 0), "lab_samples": int(meta.get("n_lab_samples") or 0),
        "built_at": meta.get("built_at"), "updated_at": meta.get("updated_at"),
    }


def _fts_phrase(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _row_snippet(text: str, span: tuple[int, int]) -> str | None:
    """For tables/supplements: just the matching row, prefixed with its sheet/part name."""
    a, b = span
    start = text.rfind("\n", 0, a) + 1
    end = text.find("\n", b)
    line = text[start : end if end != -1 else len(text)].strip()
    if not line or len(line) > 2000:
        return None
    head = ""
    for marker in ("\nSheet: ", "\n[", "Sheet: ", "["):
        i = text.rfind(marker, 0, start + 1)
        if i != -1:
            head = text[i:].lstrip("\n").split("\n", 1)[0].strip()[:80]
            break
    hl = line.replace(text[a:b], f"**{text[a:b]}**", 1)
    return f"{head}: {hl}" if head and head != line else hl


def _snippet(text: str, span: tuple[int, int], kind: str = "") -> str:
    if kind in ("supplement", "table", "lab_data"):
        row = _row_snippet(text, span)
        if row:
            return row
    a, b = span
    lo, hi = max(0, a - SNIPPET), min(len(text), b + SNIPPET)
    s = text[lo:a] + "**" + text[a:b] + "**" + text[b:hi]
    s = re.sub(r"\s+", " ", s).strip()
    return ("…" if lo > 0 else "") + s + ("…" if hi < len(text) else "")


def _patterns(query: str) -> list[dict]:
    """Turn a query into match patterns: identifiers get separator/zero-padding tolerance."""
    q = query.strip()
    pats = []
    for m in DOI_RE.finditer(q):
        doi = m.group(0).rstrip(".,;)")
        pats.append({"label": doi, "fts": doi, "re": re.compile(re.escape(doi), re.I), "loose": None})
        q = q.replace(m.group(0), " ")
    for prefix, digits in ID_RE.findall(q):
        if prefix.lower() in NOT_PREFIX:
            continue
        core = digits.lstrip("0") or "0"
        pats.append({
            "label": f"{prefix.upper()} {digits}",
            "fts": core,
            "re": re.compile(rf"(?<![A-Za-z]){re.escape(prefix)}[\s\-_.]{{0,2}}0*{core}(?!\d)", re.I),
            # the number alone, e.g. a table whose header says "PI" and cells hold only digits;
            # short numbers ("SC 110") would match everywhere, so only for 5+ digits
            "loose": re.compile(rf"(?<![\d.]){core}(?![\d.])") if len(core) >= 5 else None,
        })
    if not pats:
        terms = [t for t in re.split(r"\s+", q) if len(t) >= 3]
        if terms:
            phrase = " ".join(terms)
            pats.append({"label": phrase, "fts": phrase, "re": re.compile(re.escape(phrase), re.I), "loose": None})
    return pats


def has_identifier(query: str) -> bool:
    """True when a query names an accession/line/gene ID or DOI that exact search should check."""
    if DOI_RE.search(query or ""):
        return True
    return any(prefix.lower() not in NOT_PREFIX for prefix, _ in ID_RE.findall(query or ""))


def search(query: str, limit: int = 30) -> dict:
    if not DB_PATH.exists():
        return {"ok": False, "error": "text index not built; run: python scripts/text_index.py build"}
    conn = _connect()
    out = {"ok": True, "query": query, "coverage": coverage(conn), "terms": []}
    for pat in _patterns(query):
        exact, loose = [], []
        for row in conn.execute(
            "SELECT text, paper_id, title, doi, year, page, kind, ref FROM passages WHERE passages MATCH ? LIMIT 2000",
            (_fts_phrase(pat["fts"]),),
        ):
            text, pid, title, doi, year, page, kind, ref = row
            hit = {"paper_id": pid, "title": title, "doi": doi, "year": year, "page": page, "kind": kind, "ref": ref}
            m = pat["re"].search(text)
            if m:
                exact.append({**hit, "snippet": _snippet(text, m.span(), kind)})
            elif pat["loose"] is not None:
                m = pat["loose"].search(text)
                if m:
                    loose.append({**hit, "snippet": _snippet(text, m.span(), kind)})
        out["terms"].append({
            "term": pat["label"],
            "exact": exact[:limit],
            "n_exact": len(exact),
            "number_only": loose[: max(5, limit // 3)],
            "n_number_only": len(loose),
        })
    conn.close()
    return out


def format_results(res: dict, max_per_term: int = 15) -> str:
    if not res.get("ok"):
        return res.get("error") or "text index unavailable"
    cov = res["coverage"]
    lines = [
        f"Exact search: {res['query']}",
        (f"Searched the complete text of {cov['papers_with_full_text']} papers ({cov['pages']} pages, "
         f"{cov['pages_ocr']} scanned pages OCR'd, {cov['image_regions_ocr']} image regions OCR'd), "
         f"{cov['tables']} tables, {cov['figures']} figures, {cov['supplement_files']} supplementary files "
         f"({cov['papers_with_supplements']} papers), plus titles/abstracts of "
         f"{cov['catalog_abstracts']} catalogued papers without a PDF"
         + (f", and {cov['lab_samples']} records (NIR samples and field plots) in {cov['lab_datasets']} lab dataset(s)." if cov.get("lab_datasets") else ".")),
    ]
    if not res["terms"]:
        lines.append("No searchable term (use 3+ characters).")
    for t in res["terms"]:
        lines.append("")
        if not t["n_exact"] and not t["n_number_only"]:
            lines.append(f"### {t['term']}: not found anywhere in the KB text")
            continue
        lines.append(f"### {t['term']}: {t['n_exact']} exact match(es)"
                     + (f", {t['n_number_only']} number-only match(es)" if t["n_number_only"] else ""))
        for h in t["exact"][:max_per_term]:
            where = f"{h['kind']} {h['ref']}".strip() + (f", page {h['page']}" if h["page"] else "")
            if h["kind"] == "lab_data":
                lines.append(f"- Lab dataset: {h['title']} [{h['paper_id']}; {h['ref']}]")
            else:
                lines.append(f"- {h['title']} ({h['year'] or 'n.d.'}) DOI: {h['doi'] or 'n/a'} [{h['paper_id']}; {where}]")
            lines.append(f"  {h['snippet']}")
        if t["number_only"]:
            lines.append("  Number-only matches (the digits appear without the prefix; check whether they are the same accession):")
            for h in t["number_only"][:5]:
                where = f"{h['kind']} {h['ref']}".strip() + (f", page {h['page']}" if h["page"] else "")
                lines.append(f"  - {h['title']} ({h['year'] or 'n.d.'}) [{h['paper_id']}; {where}]: {h['snippet']}")
    lines.append("")
    lines.append("Not covered: supplements of papers that are not open access in PubMed Central (add them with "
                 "add_supplement), and catalogued papers whose PDF is missing (only their title/abstract is searched).")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "build":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        print(json.dumps(build(), indent=2))
    elif len(sys.argv) >= 3 and sys.argv[1] == "search":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        print(format_results(search(" ".join(sys.argv[2:]))))
    else:
        print(__doc__)
