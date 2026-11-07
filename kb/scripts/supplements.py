#!/usr/bin/env python3
"""Supplementary files (supplementary tables, datasets, methods) attached to KB papers.

Per-line and per-accession data (e.g. protein of each panel accession) usually lives in
supplements, not in the article PDF. This module stores them with their paper, extracts
their text, and puts them in the exact-text and semantic indexes.

  python scripts/supplements.py add <paper id|DOI|title> <file or URL>
  python scripts/supplements.py fetch <paper id|DOI|title>     # open-access supplements via Europe PMC
  python scripts/supplements.py fetch-all [--refresh]          # every KB paper with a DOI
  python scripts/supplements.py relink                         # re-extract + re-join all supplements

Files:  papers/supplements/<paper_id>/<file>
Text:   knowledge_base/structured/<paper_id>/supplements/<file>.txt
Record: paper.json "supplements" (list) and "supplements_checked" (PMC lookup result)
"""
from __future__ import annotations

import csv
import os
import io
import json
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
STRUCTURED = ROOT / "knowledge_base" / "structured"
SUPP_FILES = ROOT / "papers" / "supplements"

DATA_EXT = {".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".tab", ".txt", ".readme", ".docx", ".doc", ".pptx", ".ppt",
            ".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
MAX_ROWS_PER_SHEET = 20000
EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
HEADERS = {"User-Agent": f"SorghumProteinKB/1.0 (mailto:{os.environ.get('KB_CONTACT_EMAIL', '')})"}


# ---------------------------------------------------------------------------
# parsing (tables keep their raw rows so sheets can be linked to each other)
# ---------------------------------------------------------------------------
# accession-like IDs: "PI 180348", "PI533919", "IS 12615C", "SC 124", "BTx623"
ACC_RE = re.compile(r"^[A-Za-z]{1,6}[\s\-_]?\d{2,}[A-Za-z]?$")
# headers of columns that hold a sample/entry key used to refer to lines in other sheets
KEY_HDR_RE = re.compile(r"sample|taxa|taxon|entry|code|^no\.?$|number|genotype|accession|^id$|^#$", re.I)


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}"
    return re.sub(r"\s+", " ", str(v)).strip()


def _clean_rows(rows: list[list[str]]) -> list[list[str]]:
    return [r for r in rows if any(c for c in r)]


def _table_view(rows: list[list[str]]):
    """(header index, header, body) using the first row with 2+ filled cells as header."""
    h = next((i for i, r in enumerate(rows[:15]) if sum(bool(c) for c in r) >= 2), None)
    if h is None:
        return None
    header = [c or f"col{j + 1}" for j, c in enumerate(rows[h])]
    return h, header, rows[h + 1 :]


def _rows_to_text(title: str, rows: list[list[str]], links: dict[int, str] | None = None) -> str:
    """Rows as 'header=value; ...' lines so every row is self-describing in a search snippet.

    links maps a body-row index to an annotation (an accession ID joined from another sheet),
    written at the start of that row.
    """
    if not rows:
        return ""
    view = _table_view(rows)
    lines = [title]
    if view is None:
        lines += [" | ".join(c for c in r if c) for r in rows]
        return "\n".join(lines)
    h, header, body = view
    lines += [" | ".join(c for c in r if c) for r in rows[:h]]  # notes above the header
    lines.append("Columns: " + " | ".join(header))
    links = links or {}
    for i, r in enumerate(body[:MAX_ROWS_PER_SHEET]):
        pairs = [f"{header[j] if j < len(header) else f'col{j + 1}'}={c}" for j, c in enumerate(r) if c]
        row = "; ".join(pairs)
        lines.append(f"[{links[i]}] {row}" if i in links else row)
    if len(body) > MAX_ROWS_PER_SHEET:
        lines.append(f"[{len(body) - MAX_ROWS_PER_SHEET} more rows not indexed]")
    return "\n".join(lines)


def _ppt_text(path: Path) -> str:
    """Text of a legacy binary .ppt: walk the 'PowerPoint Document' stream for text atoms."""
    import olefile
    import struct

    with olefile.OleFileIO(str(path)) as ole:
        data = ole.openstream("PowerPoint Document").read()
    out, i = [], 0
    while i + 8 <= len(data):
        ver_inst, rtype, rlen = struct.unpack_from("<HHI", data, i)
        if ver_inst & 0x000F == 0x000F:  # container: descend into its children
            i += 8
            continue
        body = data[i + 8:i + 8 + rlen]
        if rtype == 0x0FA0:  # TextCharsAtom, UTF-16
            out.append(body.decode("utf-16-le", errors="ignore"))
        elif rtype == 0x0FA8:  # TextBytesAtom, Latin-1
            out.append(body.decode("latin-1", errors="ignore"))
        i += 8 + rlen
    placeholder = re.compile(r"^(click to edit master .*|second level|third level|fourth level|fifth level|\*)$", re.I)
    lines = [ln.strip() for t in out for ln in t.replace("\r", "\n").split("\n")]
    return "\n".join(ln for ln in lines if ln and not placeholder.match(ln))


def _parse(path: Path) -> list[dict]:
    """Parts of a supplement: tables as {'part','title','rows'}, prose as {'part','text'}."""
    ext = path.suffix.lower()
    if ext == ".xls":
        import xlrd

        wb = xlrd.open_workbook(str(path))
        parts = []
        for ws in wb.sheets():
            rows = _clean_rows([[_cell(ws.cell_value(r, c)) for c in range(ws.ncols)] for r in range(ws.nrows)])
            if rows:
                parts.append({"part": ws.name, "title": f"Sheet: {ws.name}", "rows": rows})
        return parts
    if ext == ".doc":
        # legacy Word: convert to .docx with macOS textutil, then parse as .docx (keeps tables)
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / (path.stem + ".docx")
            subprocess.run(["textutil", "-convert", "docx", "-output", str(out), str(path)],
                           check=True, capture_output=True, timeout=300)
            return _parse(out)
    if ext == ".pptx":
        from pptx import Presentation

        parts = []
        for n, slide in enumerate(Presentation(str(path)).slides, 1):
            texts = []
            for sh in slide.shapes:
                if sh.has_text_frame and sh.text_frame.text.strip():
                    texts.append(sh.text_frame.text)
                if getattr(sh, "has_table", False) and sh.has_table:
                    rows = _clean_rows([[_cell(c.text) for c in r.cells] for r in sh.table.rows])
                    if rows:
                        parts.append({"part": f"slide {n} table", "title": f"Slide {n} table", "rows": rows})
            if texts:
                parts.append({"part": f"slide {n}", "text": "\n".join(texts)})
        return parts
    if ext == ".ppt":
        text = _ppt_text(path)
        return [{"part": "slides", "text": text}] if text.strip() else []
    if ext in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        parts = []
        for ws in wb.worksheets:
            rows = _clean_rows([[_cell(c) for c in r] for r in ws.iter_rows(values_only=True)])
            if rows:
                parts.append({"part": ws.title, "title": f"Sheet: {ws.title}", "rows": rows})
        wb.close()
        return parts
    if ext in (".csv", ".tsv", ".tab"):
        raw = path.read_text(errors="ignore")
        delim = "\t" if ext in (".tsv", ".tab") else None
        if delim is None:
            try:
                delim = csv.Sniffer().sniff(raw[:5000]).delimiter
            except csv.Error:
                delim = ","
        rows = _clean_rows([[_cell(c) for c in r] for r in csv.reader(io.StringIO(raw), delimiter=delim)])
        return [{"part": path.name, "title": f"File: {path.name}", "rows": rows}] if rows else []
    if ext == ".docx":
        import docx

        d = docx.Document(str(path))
        parts = [{"part": "text", "text": "\n".join(p.text for p in d.paragraphs if p.text.strip())}]
        for i, t in enumerate(d.tables, 1):
            rows = _clean_rows([[_cell(c.text) for c in r.cells] for r in t.rows])
            if rows:
                parts.append({"part": f"table {i}", "title": f"Table {i}", "rows": rows})
        return [p for p in parts if p.get("rows") or (p.get("text") or "").strip()]
    if ext == ".pdf":
        from page_text import pdf_full_text

        return [{"part": "pdf", "text": pdf_full_text(path)}]
    if ext in (".txt", ".readme"):
        return [{"part": path.name, "text": path.read_text(errors="ignore")}]
    if ext in IMAGE_EXT:
        # a table published as an image: if a checked transcription '<stem>_transcribed.csv' was attached,
        # it carries the data and the image is kept only as the source; otherwise OCR the image
        if (path.parent / f"{path.stem}_transcribed.csv").exists():
            return []
        from PIL import Image
        from page_text import layout_rows, ocr_png_items

        img = Image.open(path).convert("RGB")
        img.thumbnail((3200, 3200))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return [{"part": path.name, "text": "\n".join(layout_rows(ocr_png_items(buf.getvalue())))}]
    return []


def _id_maps(tables: list[dict]) -> list[dict]:
    """Find key tables: an accession-ID column plus a unique sample/entry column (e.g. Table S1:
    'Sample ID' -> 'ID in Database'). Returns {where, key_header, map: key -> 'ID=...; Name=...'}."""
    maps = []
    for t in tables:
        view = _table_view(t["rows"])
        if view is None or len(view[2]) < 5:
            continue
        _, header, body = view

        def col(j):
            return [r[j] if j < len(r) else "" for r in body]

        id_cols = []
        for j in range(len(header)):
            vals = [x for x in col(j) if x]
            if len(vals) >= 5 and sum(bool(ACC_RE.match(x)) for x in vals) >= 0.5 * len(vals):
                id_cols.append(j)
        if not id_cols:
            continue
        idc = id_cols[0]
        key_c = None
        for j in range(len(header)):
            if j in id_cols or not KEY_HDR_RE.search(header[j] or ""):
                continue
            vals = [x for x in col(j) if x]
            if (len(vals) >= 0.8 * len(body) and len(set(vals)) >= 0.9 * len(vals)
                    and max(len(x) for x in vals) <= 12):
                key_c = j
                break
        if key_c is None:
            continue
        name_c = next((j for j in range(len(header))
                       if j not in (idc, key_c) and re.search(r"name|cultivar|variety", header[j] or "", re.I)), None)
        m = {}
        for r in body:
            key = r[key_c] if key_c < len(r) else ""
            acc = r[idc] if idc < len(r) else ""
            if key and acc:
                ann = f"{header[idc]}={acc}"
                if name_c is not None and name_c < len(r) and r[name_c]:
                    ann += f"; {header[name_c]}={r[name_c]}"
                m[key] = ann
        if m:
            maps.append({"where": t["where"], "key_header": header[key_c], "map": m, "table": t})
    return maps


def _links_for(t: dict, maps: list[dict]) -> dict[int, str]:
    """Row annotations for a table whose key column refers to samples defined in a key table."""
    view = _table_view(t["rows"])
    if view is None:
        return {}
    _, header, body = view
    # a table that already names its accessions needs no join
    for j in range(len(header)):
        vals = [r[j] for r in body if j < len(r) and r[j]]
        if len(vals) >= 5 and sum(bool(ACC_RE.match(x)) for x in vals) >= 0.5 * len(vals):
            return {}
    for m in maps:
        if m["table"] is t:
            continue
        for j, name in enumerate(header):
            if not (KEY_HDR_RE.search(name or "") or name == m["key_header"]):
                continue
            vals = [r[j] for r in body if j < len(r) and r[j]]
            if len(vals) < 5:
                continue
            hits = sum(v in m["map"] for v in vals)
            if hits >= 0.8 * len(vals) and len(vals) >= 0.5 * len(m["map"]):
                return {
                    i: f"Linked by sample number {name}={r[j]} to {m['where']} ({m['key_header']}): {m['map'][r[j]]}"
                    for i, r in enumerate(body) if j < len(r) and r[j] in m["map"]
                }
    return {}


# ---------------------------------------------------------------------------
# storage + indexing
# ---------------------------------------------------------------------------
def _load_record(pid: str) -> dict:
    return json.loads((STRUCTURED / pid / "paper.json").read_text())


def _save_record(rec: dict) -> None:
    (STRUCTURED / rec["paper_id"] / "paper.json").write_text(json.dumps(rec, indent=2))


def _store_file(pid: str, src: Path, label: str = "", source_url: str = "") -> dict:
    dest_dir = SUPP_FILES / pid
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return {
        "file": dest.name,
        "label": label,
        "source_url": source_url,
        "path": str(dest.relative_to(ROOT)),
        "text_file": f"knowledge_base/structured/{pid}/supplements/{dest.name}.txt",
    }


def render_paper_supplements(pid: str) -> dict:
    """(Re)write the text of all of a paper's supplements, joining sheets that refer to lines by
    sample number to the sheet that defines those numbers."""
    rec = _load_record(pid)
    entries = rec.get("supplements") or []
    parsed = []
    for e in entries:
        try:
            parsed.append((e, _parse(ROOT / e["path"])))
        except Exception as exc:
            e["error"] = f"{type(exc).__name__}: {exc}"
            parsed.append((e, []))
    tables = [{"where": f"{e['file']} / {p['part']}", "rows": p["rows"], "part": p}
              for e, parts in parsed for p in parts if "rows" in p]
    maps = _id_maps(tables)
    total_linked = 0
    txt_dir = STRUCTURED / pid / "supplements"
    txt_dir.mkdir(parents=True, exist_ok=True)
    for e, parts in parsed:
        texts, n_rows, n_linked = [], 0, 0
        for p in parts:
            if "rows" in p:
                t = next(t for t in tables if t["part"] is p)
                links = _links_for(t, maps)
                n_linked += len(links)
                n_rows += len(p["rows"])
                body = _rows_to_text(p["title"], p["rows"], links)
            else:
                body = p.get("text") or ""
            if body.strip():
                texts.append(f"[{p['part']}]\n{body}")
        text = "\n\n".join(texts)
        (txt_dir / f"{e['file']}.txt").write_text(text)
        e.update({"parts": [p["part"] for p in parts], "n_rows": n_rows, "chars": len(text),
                  "n_linked_rows": n_linked})
        total_linked += n_linked
    rec["supplements"] = entries
    _save_record(rec)
    return {"files": len(entries), "linked_rows": total_linked,
            "key_tables": [f"{m['where']} ({m['key_header']} -> {len(m['map'])} lines)" for m in maps]}


def supplement_texts(rec: dict) -> list[tuple[dict, str]]:
    """(supplement entry, extracted text) for a record; used by the indexes."""
    out = []
    for s in rec.get("supplements") or []:
        path = ROOT / s.get("text_file", "")
        if path.exists():
            out.append((s, path.read_text(errors="ignore")))
    return out


def reindex(pid: str) -> dict:
    """Push a paper's current record (with supplements) into the exact and semantic indexes."""
    rec = _load_record(pid)
    import text_index
    from ingest_paper import index_paper_into_chroma

    return {"text_index": text_index.update_paper(rec), "vector_index": index_paper_into_chroma(rec)}


def _attach(pid: str, files: list[Path], source_url: str = "", label: str = "") -> list[dict]:
    rec = _load_record(pid)
    current = {s["file"]: s for s in rec.get("supplements") or []}
    added = []
    for f in files:
        if f.suffix.lower() not in DATA_EXT:
            continue
        try:
            entry = _store_file(pid, f, label=label, source_url=source_url)
        except Exception as exc:
            entry = {"file": f.name, "error": f"{type(exc).__name__}: {exc}"}
        if "error" not in entry:
            current[entry["file"]] = entry
        added.append(entry)
    rec["supplements"] = list(current.values())
    _save_record(rec)
    # extract text for all of the paper's supplements together, so sheets can be linked
    render_paper_supplements(pid)
    rendered = {e["file"]: e for e in _load_record(pid).get("supplements") or []}
    return [rendered.get(a.get("file"), a) if "error" not in a else a for a in added]


def _download(url: str, dest_dir: Path) -> Path:
    r = requests.get(url, headers=HEADERS, timeout=180)
    r.raise_for_status()
    name = Path(urllib.parse.urlparse(url).path).name or "supplement"
    cd = r.headers.get("content-disposition") or ""
    m = re.search(r'filename="?([^";]+)', cd)
    if m:
        name = m.group(1)
    dest = dest_dir / re.sub(r"[^\w.\-]+", "_", name)
    dest.write_bytes(r.content)
    return dest


def _unpack(path: Path, dest_dir: Path) -> list[Path]:
    if path.suffix.lower() != ".zip":
        return [path]
    out = []
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            name = Path(info.filename).name
            if info.is_dir() or not name or Path(name).suffix.lower() not in DATA_EXT:
                continue
            target = dest_dir / name
            target.write_bytes(z.read(info))
            out.append(target)
    return out


def add_supplement(paper_query: str, source: str, label: str = "") -> dict:
    """Attach a supplementary file (local path or URL; a .zip is unpacked) to a KB paper."""
    import structured_store as store

    pid = store.resolve_paper_id(paper_query)
    if not pid:
        return {"ok": False, "error": f"Could not resolve paper {paper_query!r}"}
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        src = Path(source).expanduser()
        if src.exists():
            files = _unpack(src, tmpdir) if src.suffix.lower() == ".zip" else [src]
            url = ""
        elif source.startswith(("http://", "https://")):
            files = _unpack(_download(source, tmpdir), tmpdir)
            url = source
        else:
            return {"ok": False, "error": "source must be an existing file path or an http(s) URL"}
        added = _attach(pid, files, source_url=url, label=label)
    return {"ok": True, "paper_id": pid, "added": added, "index": reindex(pid)}


def _pmcid_for_doi(doi: str) -> str | None:
    r = requests.get(
        f"{EUROPEPMC}/search",
        params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "lite"},
        headers=HEADERS, timeout=(10, 30),
    )
    r.raise_for_status()
    for hit in (r.json().get("resultList") or {}).get("result") or []:
        if hit.get("pmcid"):
            return hit["pmcid"]
    return None


def fetch_supplements(paper_query: str, refresh: bool = False) -> dict:
    """Fetch open-access supplements from Europe PMC (via the paper's DOI) and index them."""
    import structured_store as store

    pid = store.resolve_paper_id(paper_query)
    if not pid:
        return {"ok": False, "error": f"Could not resolve paper {paper_query!r}"}
    rec = _load_record(pid)
    checked = rec.get("supplements_checked") or {}
    if checked and not refresh:
        return {"ok": True, "paper_id": pid, "skipped": "already checked", "checked": checked,
                "files": [s["file"] for s in rec.get("supplements") or []]}
    doi = rec.get("doi")
    status: dict = {"at": time.strftime("%Y-%m-%d"), "source": "europepmc"}
    added: list = []
    if not doi:
        status["result"] = "no DOI"
    else:
        try:
            pmcid = _pmcid_for_doi(doi)
            status["pmcid"] = pmcid
            if not pmcid:
                status["result"] = "not in PubMed Central"
            else:
                resp = requests.get(f"{EUROPEPMC}/{pmcid}/supplementaryFiles", headers=HEADERS, timeout=(10, 180))
                if resp.status_code != 200 or resp.content[:2] != b"PK":
                    status["result"] = f"no supplementary files (HTTP {resp.status_code})"
                else:
                    with tempfile.TemporaryDirectory() as tmp:
                        z = Path(tmp) / f"{pmcid}.zip"
                        z.write_bytes(resp.content)
                        files = _unpack(z, Path(tmp))
                        added = _attach(pid, files, source_url=f"{EUROPEPMC}/{pmcid}/supplementaryFiles")
                    status["result"] = f"{sum('error' not in a for a in added)} data file(s)"
        except Exception as exc:
            status["result"] = f"error: {type(exc).__name__}: {exc}"
    rec = _load_record(pid)
    rec["supplements_checked"] = status
    _save_record(rec)
    out = {"ok": True, "paper_id": pid, "checked": status, "added": added}
    if added:
        out["index"] = reindex(pid)
    return out


def relink_all(reindex_papers: bool = True) -> dict:
    """Re-render every paper's supplements (e.g. after improving parsing or linking) and reindex."""
    out = {"papers": 0, "linked_rows": 0, "key_tables": []}
    for path in sorted(STRUCTURED.glob("*/paper.json")):
        rec = json.loads(path.read_text())
        if not rec.get("supplements"):
            continue
        res = render_paper_supplements(rec["paper_id"])
        out["papers"] += 1
        out["linked_rows"] += res["linked_rows"]
        out["key_tables"] += [f"{rec['paper_id']}: {k}" for k in res["key_tables"]]
        if reindex_papers:
            reindex(rec["paper_id"])
        print(f"{rec['paper_id'][:60]:<60} linked rows: {res['linked_rows']}", flush=True)
    return out


def fetch_all(refresh: bool = False) -> dict:
    summary = {"papers": 0, "with_supplements": 0, "files": 0, "not_in_pmc": 0, "errors": []}
    for path in sorted(STRUCTURED.glob("*/paper.json")):
        rec = json.loads(path.read_text())
        src = rec.get("source_pdf")
        if src and not (ROOT / src).exists():
            continue
        summary["papers"] += 1
        print(f"... {rec['paper_id'][:60]}", end="\r", flush=True)
        res = fetch_supplements(rec["paper_id"], refresh=refresh)
        result = str((res.get("checked") or {}).get("result") or "")
        n = len([a for a in res.get("added") or [] if "error" not in a]) or (
            len(res.get("files") or []) if res.get("skipped") else 0)
        if n:
            summary["with_supplements"] += 1
            summary["files"] += n
        if "not in PubMed Central" in result:
            summary["not_in_pmc"] += 1
        if result.startswith("error"):
            summary["errors"].append({"paper_id": rec["paper_id"], "error": result})
        print(f"{rec['paper_id'][:60]:<60} {result or res.get('skipped')}", flush=True)
        time.sleep(0.2)
    return summary


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    args = sys.argv[1:]
    if len(args) == 3 and args[0] == "add":
        print(json.dumps(add_supplement(args[1], args[2]), indent=2, default=str))
    elif len(args) >= 2 and args[0] == "fetch":
        print(json.dumps(fetch_supplements(args[1], refresh="--refresh" in args), indent=2, default=str))
    elif args and args[0] == "relink":
        print(json.dumps(relink_all(), indent=2))
    elif args and args[0] == "fetch-all":
        print(json.dumps(fetch_all(refresh="--refresh" in args), indent=2))
    else:
        print(__doc__)
