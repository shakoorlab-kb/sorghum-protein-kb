#!/usr/bin/env python3
"""
Structurally extract every downloaded sorghum-protein PDF into a rich paper record.

For each paper creates:
  knowledge_base/structured/<paper_id>/
    paper.json          - full structured record
    fulltext.txt        - page-ordered plain text
    figures/fig_XXX.png + fig_XXX.json  (OCR, nearby labels, figure_type, numbers)
    tables/table_XXX.json (+ optional .csv / .md)
    pages/page_XXX.txt  - optional per-page text
    components.json     - searchable parts (figures, tables, facts, sections)
    card.json           - includes coverage score / missing parts

Also writes:
  knowledge_base/structured/index.json
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import traceback
from collections import Counter
from pathlib import Path

import pymupdf  # PyMuPDF

ROOT = Path(__file__).resolve().parents[1]
PAPERS = ROOT / "papers"
META = ROOT / "metadata"
OUT = ROOT / "knowledge_base" / "structured"
OUT.mkdir(parents=True, exist_ok=True)

SECTION_PATTERNS = [
    ("abstract", r"^\s*(abstract|summary)\s*$"),
    ("introduction", r"^\s*(\d+(\.\d+)*\.?\s*)?(introduction|background)\s*$"),
    ("methods", r"^\s*(\d+(\.\d+)*\.?\s*)?(materials?\s*(&|and)\s*methods|methods?(?:\s+summary)?|experimental(\s+procedures?)?|methodology)\s*$"),
    ("results", r"^\s*(\d+(\.\d+)*\.?\s*)?(results?(?:\s+and\s+discussion)?)\s*$"),
    ("discussion", r"^\s*(\d+(\.\d+)*\.?\s*)?(discussion|conclusions?|conclusion\s+and\s+perspectives)\s*$"),
    ("conclusion", r"^\s*(\d+(\.\d+)*\.?\s*)?(conclusions?|concluding\s+remarks|summary\s+and\s+conclusions?)\s*$"),
    ("acknowledgements", r"^\s*(acknowledg?ements?|funding|author\s+contributions?)\b"),
    ("references", r"^\s*(references|bibliography|literature\s+cited)\s*$"),
    ("supplementary", r"^\s*(supplementary(\s+materials?)?|supporting\s+information)\s*$"),
]

CAPTION_RE = re.compile(
    r"^\s*((?:Figure|Fig\.?|Table|Supplementary\s+Figure|Supplementary\s+Table)\s*"
    r"([S]?\d+[A-Za-z]?))"
    # caption forms: "Table 1. Text" / "Table 1: Text" / "Table 1 -- Text",
    # "Table 1 Composition of ..." (capitalised word, not "Table 1 shows" or "Table 1)."),
    # or "Table 1" alone on its line with the caption below
    r"\s*(?:(?:[.:|]|[-\u2013\u2014]{1,3})\s*(\S.*)|\s+((?-i:[A-Z\u0391-\u03a9(\[]).*)|[.:]?\s*)$",
    re.I | re.M,
)

SKIP_HEADING = re.compile(
    r"^(figure|fig\.|table|articles|nature|vol\b|chr\b|note\b|doi\b|received\b|"
    r"macmillan|supplementary fig|acknowledg)",
    re.I,
)
DATA_ROW_LABELS = {
    "cold",
    "hot",
    "neutral",
    "mean",
    "starter",
    "grower",
    "control",
    "total",
}
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\]\)\>\"\'<>]+", re.I)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def log(msg: str) -> None:
    print(msg, flush=True)


def clean_text(s: str) -> str:
    s = s.replace("\u00ad", "")  # soft hyphen
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def paper_id_from_path(pdf: Path, doi: str | None = None) -> str:
    if doi:
        return "doi_" + re.sub(r"[^a-zA-Z0-9]+", "_", doi.lower()).strip("_")[:120]
    h = hashlib.sha1(pdf.name.encode()).hexdigest()[:12]
    stem = re.sub(r"[^a-zA-Z0-9]+", "_", pdf.stem)[:60].strip("_")
    return f"file_{stem}_{h}"


def load_catalog_by_pdf() -> dict[str, dict]:
    """Map PDF filename / doi -> catalog record."""
    by: dict[str, dict] = {}
    for name in ("downloaded.json", "paper_catalog.json"):
        path = META / name
        if not path.exists():
            continue
        for rec in json.loads(path.read_text()):
            if rec.get("doi"):
                by[rec["doi"].lower()] = rec
            lp = rec.get("local_path")
            if lp:
                by[Path(lp).name] = rec
            title = (rec.get("title") or "").strip().lower()
            if title:
                by[f"title:{title[:120]}"] = rec
    return by


def match_catalog(pdf: Path, catalog: dict[str, dict]) -> dict:
    if pdf.name in catalog:
        return catalog[pdf.name]
    # DOI in filename
    m = DOI_RE.search(pdf.name.replace("_", "/"))
    if not m:
        # common pattern __10.xxxx_rest
        m2 = re.search(r"(10\.\d{4,9}[_/][A-Za-z0-9._\-]+)", pdf.name)
        if m2:
            doi = m2.group(1).replace("_", "/", 1)
            # only first underscore after prefix becomes slash for 10.xxxx/
            parts = m2.group(1).split("_", 1)
            if len(parts) == 2 and parts[0].startswith("10."):
                doi = parts[0] + "/" + parts[1].replace("__", "/")
            if doi.lower() in catalog:
                return catalog[doi.lower()]
            # try progressive
            for k, v in catalog.items():
                if k.startswith("10.") and k.replace("/", "_") in pdf.name.replace("__", "_"):
                    return v
    else:
        doi = m.group(0).rstrip(".,;")
        if doi.lower() in catalog:
            return catalog[doi.lower()]
    return {}


def extract_pdf_metadata(doc: pymupdf.Document) -> dict:
    meta = doc.metadata or {}
    return {
        "pdf_title": meta.get("title") or "",
        "pdf_author": meta.get("author") or "",
        "pdf_subject": meta.get("subject") or "",
        "pdf_keywords": meta.get("keywords") or "",
        "pdf_creator": meta.get("creator") or "",
        "pdf_producer": meta.get("producer") or "",
        "page_count": doc.page_count,
    }


def extract_pages_text(doc: pymupdf.Document, pdf: Path | None = None, ocr: bool = True) -> list[dict]:
    """Per-page text: text layer, OCR for scanned pages, and OCR of raster regions (page_text.py)."""
    from page_text import extract_document

    pages = []
    for p in extract_document(doc, pdf, ocr=ocr):
        p["text"] = clean_text(p["text"])
        pages.append(p)
    return pages


def _page_file_text(p: dict) -> str:
    """Everything known about a page, as stored in pages/page_NNN.txt (used by exact search)."""
    parts = [p["text"]]
    if p.get("rows_text"):
        parts.append(f"[OCR rows]\n{p['rows_text']}")
    if p.get("image_text"):
        parts.append(f"[Text in images on this page]\n{p['image_text']}")
    return "\n\n".join(x for x in parts if x and x.strip()) + "\n"


def extract_doi_from_text(text: str) -> str | None:
    m = DOI_RE.search(text[:8000])
    if not m:
        return None
    return m.group(0).rstrip(".,;)")


def extract_abstract(full_text: str) -> str:
    # Prefer Abstract ... Introduction block
    patterns = [
        r"(?is)\bAbstract\b\s*[:\-]?\s*(.+?)(?=\n\s*(?:Keywords?|Key\s*words|1[\.\s]|Introduction|Background|I\.\s|Copyright|©)\b)",
        r"(?is)\bSummary\b\s*[:\-]?\s*(.+?)(?=\n\s*(?:Keywords?|Introduction|Background)\b)",
    ]
    for pat in patterns:
        m = re.search(pat, full_text)
        if m:
            abs_ = clean_text(m.group(1))
            if 80 < len(abs_) < 5000:
                return abs_
    return ""


def extract_keywords(full_text: str) -> list[str]:
    m = re.search(r"(?i)\bKey\s*words?\b\s*[:\-]?\s*(.+)", full_text[:12000])
    if not m:
        return []
    raw = re.split(
        r"(?i)\n\s*(?:1[\.\s]+)?introduction\b|\n\s*\n|\n\s*abstract\b",
        m.group(1),
        maxsplit=1,
    )[0]
    parts = re.split(r"[;·|]", raw)
    keys = []
    for p in parts:
        p = clean_text(p.split("\n")[0])
        if not (2 < len(p) < 60):
            continue
        if re.search(r"introduction|copyright|license", p, re.I):
            continue
        keys.append(p)
    return keys[:12]


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:60] or "section"


def _is_topic_heading(line: str, prev: str = "", nxt: str = "") -> bool:
    s = line.strip()
    if not (24 <= len(s) <= 70):
        return False
    if s.endswith((".", "?", "!", ",", ";", ":", "-", "\u2013", "\u2014")):
        return False
    if SKIP_HEADING.match(s):
        return False
    if re.search(r"\d|,|&|@", s):
        return False
    if re.search(
        r"\b(include|includes|included|may|is|are|was|were|have|has|had|"
        r"suggest|suggests|formed|although|help us|fact that)\b|"
        r"facilitat",
        s,
        re.I,
    ):
        return False
    words = s.split()
    if not (4 <= len(words) <= 10):
        return False
    if not re.match(r"^[A-Z]", s):
        return False
    prev = (prev or "").strip()
    if prev and not prev.startswith("--- Page") and not prev.endswith((".", "?", "!")):
        return False
    nxt = (nxt or "").strip()
    if nxt and (len(nxt) < 40 or nxt[:1].islower()):
        return False
    small = {"of", "and", "its", "the", "a", "an", "in", "for", "to", "from", "on"}
    caps = sum(1 for w in words if w[:1].isupper() or w.lower() in small)
    title_case = caps >= len(words) - 1
    sentence_case = words[0][:1].isupper() and caps >= 2
    return title_case or sentence_case


def split_sections(full_text: str) -> dict[str, str]:
    lines = full_text.splitlines()
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > 80:
            continue
        for name, pat in SECTION_PATTERNS:
            if re.match(pat, stripped, re.I):
                hits.append((i, name))
                break
    imrad = {n for _, n in hits}
    use_topics = not ({"methods", "results"} <= imrad or {"results", "discussion"} <= imrad)
    if use_topics:
        extra = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or len(stripped) > 80:
                continue
            if any(i == hi for hi, _ in hits):
                continue
            prev = lines[i - 1] if i else ""
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if _is_topic_heading(stripped, prev, nxt):
                extra.append((i, _slug(stripped)))
        hits = sorted(hits + extra, key=lambda x: x[0])
    filtered = []
    for i, name in hits:
        if filtered and filtered[-1][1] == name:
            continue
        filtered.append((i, name))
    sections: dict[str, str] = {}
    if not filtered:
        sections["body"] = full_text
        return sections
    if filtered[0][0] > 0:
        sections["front_matter"] = "\n".join(lines[: filtered[0][0]]).strip()
    for idx, (start, name) in enumerate(filtered):
        end = filtered[idx + 1][0] if idx + 1 < len(filtered) else len(lines)
        content = "\n".join(lines[start + 1 : end]).strip()
        heading = lines[start].strip()
        if name not in {
            "abstract",
            "introduction",
            "methods",
            "results",
            "discussion",
            "conclusion",
            "references",
            "acknowledgements",
            "supplementary",
            "front_matter",
        }:
            content = f"{heading}\n\n{content}".strip()
        if name in sections and sections[name]:
            sections[name] += "\n\n" + content
        else:
            sections[name] = content
    return sections


def extract_captions(full_text: str) -> list[dict]:
    page_at = []
    for m in re.finditer(r"--- Page (\d+) ---", full_text):
        page_at.append((m.start(), int(m.group(1))))

    def page_for(pos: int) -> int | None:
        cur = None
        for start, p in page_at:
            if start <= pos:
                cur = p
            else:
                break
        return cur

    captions = []
    for m in CAPTION_RE.finditer(full_text):
        label = clean_text(m.group(1))
        rest = clean_text(m.group(3) or m.group(4) or "")
        kind = "table" if "table" in label.lower() else "figure"
        start = m.end()
        extra = full_text[start : start + 500]
        stop = re.search(
            r"\n\s*(?:Figure|Fig\.?|Table)\s+[S]?\d+|\n\s*\n\s*\n",
            extra,
            re.I,
        )
        if stop:
            extra = extra[: stop.start()]
        caption = clean_text((rest + " " + extra).strip())
        captions.append(
            {
                "kind": kind,
                "label": label,
                "caption": caption[:1200],
                "page": page_for(m.start()),
            }
        )
    # one caption per label: keep the occurrence with the most caption text
    best: dict[str, dict] = {}
    order: list[str] = []
    for c in captions:
        key = re.sub(r"\s+", " ", c["label"].lower())
        if key not in best:
            order.append(key)
            best[key] = c
        elif len(c["caption"]) > len(best[key]["caption"]):
            best[key] = c
    return [best[k] for k in order]


def _split_author_year_refs(text: str) -> list[str]:
    text = re.sub(r"(?is)\n\s*Table\s+\d+.*?(?=\n[A-Z]|\Z)", "\n", text)
    parts = re.split(
        r"\n(?=(?:[A-Z]\.){2,}[A-Z]\.?-?\s|[A-Z][A-Za-z'\-]+\s+[A-Z]{1,4}[\s,\.])",
        text.strip(),
    )
    refs = []
    for p in parts:
        p = clean_text(p)
        if len(p) < 40 or len(p) > 900:
            continue
        if not YEAR_RE.search(p):
            continue
        refs.append(p[:800])
    return refs


def extract_references(sections: dict[str, str], full_text: str) -> list[str]:
    ref_text = sections.get("references") or ""
    if not ref_text:
        m = re.search(r"(?is)\n\s*References\s*\n(.+)$", full_text)
        if m:
            ref_text = m.group(1)
    if not ref_text:
        m = re.search(r"(?is)\nReceived .+\n(.+)$", full_text)
        if m:
            ref_text = m.group(1)
    parts = re.split(r"\n\s*(?=\[\d+\]|\d+\.\s+[A-Z])", ref_text) if ref_text else []
    refs = []
    for p in parts:
        p = clean_text(p)
        if len(p) < 30:
            continue
        refs.append(p[:800])
    numbered = [r for r in refs if re.match(r"(\[\d+\]|\d+\.)", r)]
    if len(numbered) >= 3:
        return numbered[:400]
    ay = _split_author_year_refs(ref_text or (full_text or "")[-20000:])
    if len(ay) >= 3:
        return ay[:400]
    tail = (full_text or "")[-16000:]
    extra = []
    for m in re.finditer(
        r"(?m)^\s*(\d{1,3})\.\s+([A-Z][\s\S]{20,400}?)(?=^\s*\d{1,3}\.\s+[A-Z]|\Z)",
        tail,
    ):
        p = clean_text(f"{m.group(1)}. {m.group(2)}")
        if 40 <= len(p) <= 800:
            extra.append(p)
    if extra:
        return extra[:400]
    return (ay or refs)[:400]


def _page_figure_captions(page_text: str) -> list[str]:
    caps = []
    for m in CAPTION_RE.finditer(page_text):
        if "table" in m.group(1).lower():
            continue
        caps.append(clean_text(m.group(0))[:700])
    return caps


def extract_images(doc: pymupdf.Document, fig_dir: Path) -> list[dict]:
    fig_dir.mkdir(parents=True, exist_ok=True)
    figures = []
    n = 0
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        images = page.get_images(full=True)
        page_text = page.get_text("text")
        page_caps = _page_figure_captions(page_text)
        page_img_i = 0
        for img in images:
            xref = img[0]
            try:
                pix = pymupdf.Pixmap(doc, xref)
                if pix.n > 4:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                if pix.width < 80 or pix.height < 80:
                    continue
                if pix.width * pix.height < 12000:
                    continue
                n += 1
                fname = f"fig_{n:03d}.png"
                fpath = fig_dir / fname
                if pix.alpha:
                    pix = pymupdf.Pixmap(pix, 0)
                pix.save(str(fpath))
                if page_img_i < len(page_caps):
                    cap = page_caps[page_img_i]
                elif page_caps:
                    cap = page_caps[-1]
                else:
                    cap = ""
                page_img_i += 1
                rec = {
                    "id": f"fig_{n:03d}",
                    "file": f"figures/{fname}",
                    "page": page_index + 1,
                    "width": pix.width,
                    "height": pix.height,
                    "xref": xref,
                    "caption_guess": cap,
                    "mime_type": "image/png",
                }
                (fig_dir / f"fig_{n:03d}.json").write_text(json.dumps(rec, indent=2))
                figures.append(rec)
            except Exception:
                continue
    return figures


def assign_captions_to_figures(figures: list[dict], captions: list[dict], fig_dir: Path) -> list[dict]:
    """Prefer same-page unused figure captions; keep existing if already specific."""
    unused = [c for c in captions if c.get("kind") == "figure"]
    for fig in figures:
        existing = fig.get("caption_guess") or ""
        page = fig.get("page")
        pick = None
        for c in unused:
            if c.get("page") == page:
                pick = c
                break
        if pick is None and unused and not existing:
            pick = unused[0]
        if pick:
            unused = [c for c in unused if c is not pick]
            label = pick.get("label") or ""
            text = f"{label}. {pick.get('caption') or ''}".strip()
            if (not existing) or (label and label.lower() not in existing.lower()):
                fig["caption_guess"] = text[:700]
                fig["caption_label"] = label
                (fig_dir / f"{fig['id']}.json").write_text(json.dumps(fig, indent=2))
    return figures


def extract_tables_find_tables(doc: pymupdf.Document, table_dir: Path) -> list[dict]:
    """Use PyMuPDF table finder when available."""
    table_dir.mkdir(parents=True, exist_ok=True)
    tables = []
    n = 0
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        try:
            tf = page.find_tables()
        except Exception:
            continue
        if not tf:
            continue
        for t in tf.tables:
            n += 1
            try:
                data = t.extract()
            except Exception:
                data = []
            # normalize
            rows = []
            for row in data or []:
                rows.append([("" if c is None else str(c).strip()) for c in row])
            # drop empty
            rows = [r for r in rows if any(c for c in r)]
            if len(rows) < 2:
                continue
            tid = f"table_{n:03d}"
            page_text = page.get_text("text")
            headers, data_rows = _infer_table_headers(page_text, rows)
            if _is_junk_table(headers, data_rows, page_text[:400]):
                n -= 1
                continue
            md = _rows_to_markdown(([headers] + data_rows) if headers else rows)
            csv_text = _rows_to_csv(([headers] + data_rows) if headers else rows)
            cap = ""
            for m in CAPTION_RE.finditer(page_text):
                if "table" in m.group(1).lower():
                    cap = clean_text(m.group(0))[:600]
                    break
            rec = {
                "id": tid,
                "page": page_index + 1,
                "n_rows": len(data_rows),
                "n_cols": max((len(r) for r in data_rows or rows), default=0),
                "caption_guess": cap,
                "headers": headers,
                "rows": data_rows,
                "markdown_file": f"tables/{tid}.md",
                "csv_file": f"tables/{tid}.csv",
                "json_file": f"tables/{tid}.json",
            }
            md_rows = ([headers] + data_rows) if headers else rows
            (table_dir / f"{tid}.json").write_text(json.dumps({**rec, "all_rows": md_rows}, indent=2))
            (table_dir / f"{tid}.md").write_text(md)
            (table_dir / f"{tid}.csv").write_text(csv_text)
            tables.append(rec)
    return tables


def _trim_table_rows(rows: list[list[str]]) -> list[list[str]]:
    rows = [[("" if c is None else str(c).strip()) for c in r] for r in rows]
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return []
    ncols = max(len(r) for r in rows)
    keep = [
        i
        for i in range(ncols)
        if any((r[i] if i < len(r) else "").strip() for r in rows)
    ]
    return [[(r[i] if i < len(r) else "") for i in keep] for r in rows]


def _is_junk_table(headers: list[str], rows: list[list[str]], caption: str = "") -> bool:
    blob = " ".join(
        [caption or "", " ".join(headers or [])]
        + [" ".join(r[:4]) for r in (rows or [])[:4]]
    ).lower()
    if any(k in blob for k in ("citable link", "terms of use", "hdl.handle.net")):
        return True
    first = ((headers[0] if headers else "") or (rows[0][0] if rows and rows[0] else "")).lower()
    if first in {"citation", "title page"}:
        return True
    return False


def _is_prose_row(row: list[str]) -> bool:
    lab = (row[0] if row else "") or ""
    if len(lab) > 80:
        return True
    if re.search(
        r"\b(respectively|correlation|we found|as shown|using the wet|"
        r"vitaminic|growth promoter|zinc[- ]*mg)\b",
        lab,
        re.I,
    ):
        return True
    if lab.count(",") >= 3:
        return True
    return False


def _numeric_tail(row: list[str]) -> bool:
    tail = [c for c in (row[1:] if len(row) > 1 else row) if str(c).strip()]
    if not tail:
        return False
    ok = 0
    for c in tail:
        if re.fullmatch(r"-?\d+(?:[.,]\d+)?", str(c).strip()):
            ok += 1
    return ok >= max(1, len(tail) - 1)


def _headers_from_block(block: str, ncols: int) -> list[str] | None:
    b = block or ""
    if re.search(r"Mean\s*\(%\)", b, re.I) and re.search(r"Maximum\s*\(%\)", b, re.I):
        header = ["Amino_acid", "Mean_%", "Maximum_%", "Minimum_%"]
    elif re.search(r"\bSWT\b|\bSNT\b|sorghum with tannin", b, re.I):
        header = [
            "Ingredient",
            "Starter_Corn",
            "Starter_SWT",
            "Starter_SNT",
            "Grower_Corn",
            "Grower_SWT",
            "Grower_SNT",
        ]
    elif re.search(r"Sorghum\s*\(HT\)|high tannin", b, re.I):
        header = ["Treatment", "Corn", "Sorghum_HT", "Sorghum_LT", "Mean"]
    else:
        return None
    return header


def _fit_rows(rows: list[list[str]], ncols: int) -> list[list[str]]:
    out = []
    for r in rows:
        r = list(r) + [""] * ncols
        out.append(r[:ncols])
    return out


def _infer_table_headers(block: str, rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    rows = _trim_table_rows(rows)
    rows = [r for r in rows if not _is_prose_row(r)]
    if not rows:
        return [], []
    ncols = max(len(r) for r in rows)
    forced = _headers_from_block(block, ncols)
    if forced:
        first = " ".join(rows[0]).lower()
        if any(h.lower() in first for h in forced if len(h) > 3):
            rows = rows[1:]
        return forced, _fit_rows(rows, len(forced))
    first_lab = (rows[0][0] or "").strip().lower()
    if first_lab in DATA_ROW_LABELS or (len(rows) >= 2 and _numeric_tail(rows[0]) and _numeric_tail(rows[1])):
        header = ["name"] + [f"col_{i}" for i in range(1, ncols)]
        return header[:ncols], rows
    return rows[0], rows[1:]


def extract_tables_from_text_blocks(
    full_text: str, table_dir: Path, start_n: int = 0
) -> list[dict]:
    """Recover tables that are text (not drawn grids), e.g. older PageMaker PDFs."""
    table_dir.mkdir(parents=True, exist_ok=True)
    blocks = list(
        re.finditer(
            r"(?ims)^[ \t]*(Table\s+([S]?\d+[A-Za-z]?)\s*[.:\u2013\-|][ \t]*)(.{20,}?)(?=^[ \t]*Table\s+[S]?\d+|^--- Page |\Z)",
            full_text,
        )
    )
    tables = []
    seen = set()
    n = start_n
    for m in blocks:
        label = clean_text(f"Table {m.group(2)}")
        if label.lower() in seen:
            continue
        seen.add(label.lower())
        body = clean_text(m.group(3))[:4000]
        rows = _numeric_rows_from_block(m.group(0))
        if len(rows) < 2 and len(body) < 40:
            continue
        n += 1
        tid = f"table_{n:03d}"
        headers, data_rows = _infer_table_headers(m.group(0), rows)
        if _is_junk_table(headers, data_rows, m.group(0)[:400]):
            n -= 1
            continue
        rec = {
            "id": tid,
            "page": None,
            "n_rows": len(data_rows) or None,
            "n_cols": max((len(r) for r in data_rows or rows), default=0) or None,
            "caption_guess": f"{label}. {body[:400]}".strip(),
            "headers": headers,
            "rows": data_rows,
            "source": "text_block",
            "markdown_file": f"tables/{tid}.md",
            "csv_file": f"tables/{tid}.csv",
            "json_file": f"tables/{tid}.json",
        }
        md_rows = ([headers] + data_rows) if headers else rows
        (table_dir / f"{tid}.json").write_text(json.dumps({**rec, "all_rows": md_rows, "text_block": body}, indent=2))
        if md_rows:
            (table_dir / f"{tid}.md").write_text(_rows_to_markdown(md_rows))
            (table_dir / f"{tid}.csv").write_text(_rows_to_csv(md_rows))
        else:
            (table_dir / f"{tid}.md").write_text(f"**{label}**\n\n{body}\n")
        tables.append(rec)
    return tables


def _numeric_rows_from_block(block: str) -> list[list[str]]:
    """Rebuild wrapped PDF table rows: a label line followed by several numeric lines."""
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    rows: list[list[str]] = []
    buf_label: str | None = None
    buf_nums: list[str] = []

    def flush() -> None:
        nonlocal buf_label, buf_nums
        if buf_label and len(buf_nums) >= 2:
            rows.append([buf_label] + buf_nums)
        buf_label, buf_nums = None, []

    for line in lines:
        if re.match(
            r"^(Using |We |As shown|This result|Compared to|However\b)",
            line,
            re.I,
        ):
            flush()
            break
        if re.match(
            r"^(Table\b|[12]\s*-|Diets|Chambers|Ingredients|Starter|Grower|"
            r"Amino Acids|Mean\s*\(|Maximum\s*\(|Minimum\s*\()",
            line,
            re.I,
        ):
            flush()
            continue
        if re.fullmatch(r"[-–—]+", line):
            buf_nums.append("")
            continue
        cells = [n.replace(",", ".") for n in re.findall(r"\d+[.,]\d+", line)]
        label = clean_text(re.sub(r"\d+[.,]\d+", " ", line))
        label = re.sub(r"\b[AB]\b", "", label).strip(" -")
        if label and re.search(r"[A-Za-z]{3,}", label) and not _is_prose_row([label]):
            flush()
            buf_label, buf_nums = label, cells
        else:
            buf_nums.extend(cells)
    flush()
    return rows


def extract_tables_from_text(captions: list[dict], sections: dict, full_text: str) -> list[dict]:
    """Fallback: keep table captions even if grid extraction fails."""
    out = []
    for i, c in enumerate([x for x in captions if x["kind"] == "table"], 1):
        out.append(
            {
                "id": f"table_caption_{i:03d}",
                "page": c.get("page"),
                "n_rows": None,
                "n_cols": None,
                "caption_guess": f"{c['label']}. {c['caption']}".strip(),
                "headers": [],
                "rows": [],
                "source": "caption_only",
            }
        )
    return out


def _rows_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    cols = max(len(r) for r in rows)
    norm = [r + [""] * (cols - len(r)) for r in rows]
    header = norm[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for r in norm[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines) + "\n"


def _rows_to_csv(rows: list[list[str]]) -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def extract_key_claims(sections: dict[str, str], abstract: str) -> list[str]:
    """Claim sentences from abstract/results plus other content sections."""
    blobs = [
        abstract,
        sections.get("results", "")[:8000],
        sections.get("discussion", "")[:8000],
        sections.get("conclusion", "")[:5000],
    ]
    for name, body in (sections or {}).items():
        if name in {"front_matter", "references", "acknowledgements"}:
            continue
        if name not in {"results", "discussion", "conclusion"}:
            blobs.append((body or "")[:4000])
    blob = "\n".join(blobs)
    sents = re.split(r"(?<=[.!?])\s+", blob)
    keys = (
        "digestib",
        "kafirin",
        "protein",
        "tannin",
        "bioavailab",
        "lysine",
        "fermentation",
        "cooking",
        "genome",
        "drought",
        "gene",
    )
    out = []
    for s in sents:
        s = clean_text(s)
        if not (60 <= len(s) <= 350):
            continue
        low = s.lower()
        if "--- page" in low:
            continue
        if sum(1 for k in keys if k in low) >= 2:
            out.append(s)
        if len(out) >= 12:
            break
    return out


def process_one(
    pdf: Path,
    catalog: dict[str, dict],
    force: bool = False,
    rich: bool = True,
) -> dict:
    cat = match_catalog(pdf, catalog)
    doi = (cat.get("doi") or "").lower() or None
    # try from filename early for id stability
    pid = paper_id_from_path(pdf, doi)
    paper_dir = OUT / pid
    paper_json = paper_dir / "paper.json"
    if paper_json.exists() and not force:
        return json.loads(paper_json.read_text())
    # supplements are attached separately (supplements.py); keep them across re-extraction
    kept = {}
    if paper_json.exists():
        try:
            prev = json.loads(paper_json.read_text())
            kept = {k: prev[k] for k in ("supplements", "supplements_checked") if k in prev}
        except Exception:
            kept = {}

    paper_dir.mkdir(parents=True, exist_ok=True)
    fig_dir = paper_dir / "figures"
    table_dir = paper_dir / "tables"
    if force:
        for d in (fig_dir, table_dir):
            if d.exists():
                shutil.rmtree(d)
    pages_dir = paper_dir / "pages"
    pages_dir.mkdir(exist_ok=True)

    doc = pymupdf.open(pdf)
    try:
        pdf_meta = extract_pdf_metadata(doc)
        pages = extract_pages_text(doc, pdf)
        for p in pages:
            (pages_dir / f"page_{p['page']:03d}.txt").write_text(_page_file_text(p))

        full_text = "\n\n".join(f"--- Page {p['page']} ---\n{p['text']}" for p in pages)
        (paper_dir / "fulltext.txt").write_text(full_text)

        text_doi = extract_doi_from_text(full_text)
        if not doi and text_doi:
            doi = text_doi.lower()
            # rename id if we discovered DOI and folder was file_*
            new_pid = paper_id_from_path(pdf, doi)
            if new_pid != pid and not (OUT / new_pid / "paper.json").exists():
                # keep current; also record canonical id
                pass

        abstract = cat.get("abstract") or extract_abstract(full_text)
        keywords = extract_keywords(full_text)
        if cat.get("pdf_keywords"):
            pass
        sections = split_sections(full_text)
        captions = extract_captions(full_text)
        references = extract_references(sections, full_text)

        figures = extract_images(doc, fig_dir)
        figures = assign_captions_to_figures(figures, captions, fig_dir)

        tables = extract_tables_find_tables(doc, table_dir)
        have_caps = {(t.get("caption_guess") or "")[:48] for t in tables}
        text_tables = extract_tables_from_text_blocks(
            full_text, table_dir, start_n=len(tables)
        )
        for e in text_tables:
            key = (e.get("caption_guess") or "")[:48]
            if key not in have_caps:
                tables.append(e)
                have_caps.add(key)
        if not tables:
            tables = extract_tables_from_text(captions, sections, full_text)
        elif len(tables) < len([c for c in captions if c["kind"] == "table"]):
            extras = extract_tables_from_text(captions, sections, full_text)
            have = {(t.get("caption_guess") or "")[:40] for t in tables}
            for e in extras:
                if (e.get("caption_guess") or "")[:40] not in have:
                    tables.append(e)

        # authors
        authors = cat.get("authors") or []
        if isinstance(authors, str):
            authors = [authors]
        if not authors and pdf_meta.get("pdf_author"):
            authors = [a.strip() for a in re.split(r";|,| and ", pdf_meta["pdf_author"]) if a.strip()]

        title = cat.get("title") or pdf_meta.get("pdf_title") or pdf.stem.replace("_", " ")
        title = re.sub(r"<[^>]+>", "", title)
        year = cat.get("year")
        if not year:
            m = YEAR_RE.search(full_text[:2000])
            year = int(m.group(0)) if m else None

        claims = extract_key_claims(sections, abstract or "")

        # journal guess from front matter
        journal = ""
        front = sections.get("front_matter", "")[:1500]
        for line in front.splitlines()[:15]:
            if re.search(r"journal|foods|nutrients|plants|plos|nature|plant physiol", line, re.I):
                journal = clean_text(line)[:200]
                break

        record = {
            "paper_id": pid,
            "title": clean_text(title),
            "authors": authors,
            "year": year,
            "doi": doi,
            "journal": journal,
            "keywords": keywords,
            "abstract": clean_text(abstract or ""),
            "cited_by_count": cat.get("cited_by_count"),
            "is_oa": cat.get("is_oa"),
            "landing_url": cat.get("landing_url") or (f"https://doi.org/{doi}" if doi else None),
            "source_pdf": str(pdf.relative_to(ROOT)),
            "pdf_metadata": pdf_meta,
            "stats": {
                "n_pages": pdf_meta.get("page_count"),
                "n_chars": len(full_text),
                "n_sections": len(sections),
                "n_figures": len(figures),
                "n_tables": len(tables),
                "n_references": len(references),
                "n_captions": len(captions),
                "n_key_claims": len(claims),
                "n_pages_ocr": sum(1 for p in pages if p.get("source") == "ocr_page"),
                "n_image_regions_ocr": sum(p.get("ocr_regions") or 0 for p in pages),
            },
            "section_names": list(sections.keys()),
            "sections": {k: v for k, v in sections.items()},
            "figures": figures,
            "tables": [
                {k: v for k, v in t.items() if k != "all_rows"}
                for t in tables
            ],
            "captions": captions,
            "references": references,
            "key_claims": claims,
            # per-page provenance; image_text holds OCR of pictures (tables/figures saved as images)
            "page_text_sources": [
                {"page": p["page"], "source": p.get("source"), "text_layer_chars": p.get("text_layer_chars"),
                 "chars": len(p["text"]), "image_ocr_regions": p.get("ocr_regions") or 0}
                for p in pages
            ],
            "page_image_text": [
                {"page": p["page"], "text": p["image_text"]} for p in pages if p.get("image_text")
            ],
            "files": {
                "paper_json": f"knowledge_base/structured/{pid}/paper.json",
                "fulltext": f"knowledge_base/structured/{pid}/fulltext.txt",
                "figures_dir": f"knowledge_base/structured/{pid}/figures",
                "tables_dir": f"knowledge_base/structured/{pid}/tables",
                "pages_dir": f"knowledge_base/structured/{pid}/pages",
            },
        }

        record.update(kept)
        if rich:
            from rich_enrich import enrich_record

            record = enrich_record(record, paper_dir, doc=doc)

        # store a lighter sections file too for big bodies
        (paper_dir / "sections.json").write_text(
            json.dumps({k: (v[:20000] + ("…" if len(v) > 20000 else "")) for k, v in sections.items()}, indent=2)
        )
        paper_json.write_text(json.dumps(record, indent=2))
        # compact index card
        (paper_dir / "card.json").write_text(
            json.dumps(
                {
                    "paper_id": pid,
                    "title": record["title"],
                    "authors": authors[:12],
                    "year": year,
                    "doi": doi,
                    "journal": journal,
                    "keywords": keywords,
                    "abstract": (record["abstract"] or "")[:600],
                    "stats": record["stats"],
                    "coverage": record.get("coverage"),
                    "section_names": record["section_names"],
                    "figure_ids": [f["id"] for f in record.get("figures") or figures],
                    "table_ids": [t["id"] for t in tables],
                    "landing_url": record["landing_url"],
                    "source_pdf": record["source_pdf"],
                },
                indent=2,
            )
        )
        return record
    finally:
        doc.close()


def build_index(records: list[dict]) -> None:
    cards = []
    for r in records:
        cards.append(
            {
                "paper_id": r["paper_id"],
                "title": r.get("title"),
                "year": r.get("year"),
                "doi": r.get("doi"),
                "authors": (r.get("authors") or [])[:8],
                "n_figures": (r.get("stats") or {}).get("n_figures", 0),
                "n_tables": (r.get("stats") or {}).get("n_tables", 0),
                "n_pages": (r.get("stats") or {}).get("n_pages", 0),
                "section_names": r.get("section_names") or [],
                "keywords": r.get("keywords") or [],
                "path": f"knowledge_base/structured/{r['paper_id']}/paper.json",
            }
        )
    cards.sort(key=lambda c: (-(c.get("year") or 0), c.get("title") or ""))
    index = {
        "n_papers": len(cards),
        "total_figures": sum(c.get("n_figures") or 0 for c in cards),
        "total_tables": sum(c.get("n_tables") or 0 for c in cards),
        "papers": cards,
    }
    (OUT / "index.json").write_text(json.dumps(index, indent=2))
    # also a titles lookup
    by_doi = {c["doi"]: c["paper_id"] for c in cards if c.get("doi")}
    (OUT / "doi_to_id.json").write_text(json.dumps(by_doi, indent=2))


PILOT_FRAGMENTS = [
    "pnas.080076297",
    "plants12081662",
    "s1516-635x2004000100007",
    "nature07723",
]


def _select_pdfs(argv: list[str]) -> list[Path]:
    all_pdfs = sorted(PAPERS.glob("*.pdf"))
    if "--pilot" in argv:
        idx = argv.index("--pilot")
        n = 3
        if idx + 1 < len(argv) and argv[idx + 1].isdigit():
            n = int(argv[idx + 1])
        picked = []
        for frag in PILOT_FRAGMENTS:
            for pdf in all_pdfs:
                if frag.lower() in pdf.name.lower() and pdf not in picked:
                    picked.append(pdf)
                    break
        if len(picked) < n:
            for pdf in all_pdfs:
                if pdf not in picked:
                    picked.append(pdf)
                if len(picked) >= n:
                    break
        return picked[:n]
    if "--ids" in argv:
        idx = argv.index("--ids")
        frags = (argv[idx + 1] if idx + 1 < len(argv) else "").split(",")
        picked = []
        for frag in frags:
            frag = frag.strip().lower()
            if not frag:
                continue
            for pdf in all_pdfs:
                if frag in pdf.name.lower() or frag in pdf.stem.lower():
                    picked.append(pdf)
                    break
        return picked
    if "--limit" in argv:
        idx = argv.index("--limit")
        n = int(argv[idx + 1]) if idx + 1 < len(argv) else 5
        return all_pdfs[:n]
    return all_pdfs


def main() -> None:
    force = "--force" in sys.argv
    rich = "--no-rich" not in sys.argv
    catalog = load_catalog_by_pdf()
    pdfs = _select_pdfs(sys.argv)
    log(f"Extracting structured content from {len(pdfs)} PDFs (force={force}, rich={rich})")
    records = []
    errors = []
    for i, pdf in enumerate(pdfs, 1):
        log(f"[{i}/{len(pdfs)}] {pdf.name[:80]}")
        try:
            rec = process_one(pdf, catalog, force=force, rich=rich)
            stats = rec.get("stats") or {}
            cov = (rec.get("coverage") or {}).get("score")
            log(
                f"  -> {rec.get('paper_id')} | pages={stats.get('n_pages')} "
                f"figs={stats.get('n_figures')} tables={stats.get('n_tables')} "
                f"refs={stats.get('n_references')} coverage={cov} "
                f"ocr={stats.get('n_figure_ocr')} missing={stats.get('missing_components')}"
            )
            records.append(rec)
        except Exception as e:
            log(f"  ERROR: {e}")
            errors.append({"pdf": pdf.name, "error": str(e), "trace": traceback.format_exc()[-800:]})
    # reload all cards from disk for complete index
    all_recs = []
    for d in sorted(OUT.iterdir()):
        if d.is_dir() and (d / "paper.json").exists():
            all_recs.append(json.loads((d / "paper.json").read_text()))
    build_index(all_recs)
    try:
        from rich_enrich import rebuild_component_index

        cidx = rebuild_component_index()
        log(f"Component index: {cidx}")
    except Exception as e:
        log(f"Component index skipped: {e}")
    (OUT / "extraction_errors.json").write_text(json.dumps(errors, indent=2))
    log(f"\nDone. Structured papers: {len(all_recs)}")
    log(f"Index: {OUT / 'index.json'}")
    if errors:
        log(f"Errors: {len(errors)} -> {OUT / 'extraction_errors.json'}")


if __name__ == "__main__":
    main()
