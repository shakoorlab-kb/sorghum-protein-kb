#!/usr/bin/env python3
"""Enrich a structured paper with figure OCR, nearby labels, graph numbers, coverage.

Designed to improve in a loop: each paper gets a coverage report of what is still missing.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRUCTURED = ROOT / "knowledge_base" / "structured"

NUM_RE = re.compile(
    r"(?<![\w./])(-?\d+(?:\.\d+)?)\s*(%|kDa|kda|mm|µm|um|nm|g/kg|mg/g|kcal|mol%|w/w|v/v)?"
    r"(?!\d)",
    re.I,
)
FACT_RE = re.compile(
    r"([^.!?\n]{0,80}(?:digestib\w*|kafirin\w*|tannin\w*|protein\s+bod\w*|lysine|bioavailab\w*)"
    r"[^.!?\n]{0,80}(?:\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s*kDa)[^.!?\n]{0,40}[.!?])",
    re.I,
)

FIGURE_TYPES = (
    ("gel", ("sds-page", "western", "blot", "electrophor", "lane ")),
    ("micrograph", ("micrograph", "tem", "sem", "electron", "immunogold", "bar =", "bar=")),
    ("graph", ("graph", "plot", "bar chart", "axis", "versus", " vs ", "r2", "r²")),
    ("schematic", ("schematic", "diagram", "overview", "workflow", "pathway")),
    ("photo", ("photograph", "grain", "kernel", "seed")),
)


def clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s


def _rects_overlap(a, b, pad: float = 12.0) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (
        ax1 + pad < bx0 or bx1 + pad < ax0 or ay1 + pad < by0 or by1 + pad < ay0
    )


def nearby_page_text(page, image_rect, extra_below: float = 56.0) -> str:
    """Vector text inside a figure or in the caption band just below it."""
    if image_rect is None:
        return ""
    x0, y0, x1, y1 = image_rect
    bits = []
    try:
        data = page.get_text("dict")
    except Exception:
        return ""
    prose = re.compile(
        r"\b(however|suggests that|we also|in at least|evidence for|"
        r"another\d|identified\d|may reflect|over-represented)\b",
        re.I,
    )
    for block in data.get("blocks") or []:
        if block.get("type") != 0:
            continue
        bbox = block.get("bbox")
        if not bbox:
            continue
        bx0, by0, bx1, by1 = bbox
        inside = _rects_overlap((x0, y0, x1, y1), bbox, pad=4)
        x_overlap = not (bx1 < x0 + 6 or bx0 > x1 - 6)
        below = (by0 >= y1 - 8) and (by0 <= y1 + extra_below) and x_overlap
        if not (inside or below):
            continue
        for line in block.get("lines") or []:
            line_txt = "".join(s.get("text", "") for s in line.get("spans") or []).strip()
            if not line_txt or len(line_txt) > 110:
                continue
            if prose.search(line_txt):
                continue
            nwords = len(line_txt.split())
            if nwords >= 8 and re.search(
                r"\b(are|have|has|that|which|despite|however|found in)\b", line_txt, re.I
            ):
                continue
            bits.append(line_txt)
    return clean(" ".join(bits))


def image_rect_for_xref(page, xref: int):
    try:
        rects = page.get_image_rects(xref)
        if rects:
            r = rects[0]
            return (float(r.x0), float(r.y0), float(r.x1), float(r.y1))
    except Exception:
        pass
    try:
        for info in page.get_image_info() or []:
            if info.get("xref") == xref and info.get("bbox"):
                b = info["bbox"]
                return (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
    except Exception:
        pass
    return None


@lru_cache(maxsize=1)
def _ocr_engine():
    try:
        from rapidocr import RapidOCR

        return RapidOCR()
    except Exception:
        pass
    try:
        from rapidocr_onnxruntime import RapidOCR

        return RapidOCR()
    except Exception:
        return None


def _keep_ocr_token(t: str) -> bool:
    t = clean(str(t))
    if not t or len(t) > 80:
        return False
    if re.fullmatch(r"[0O.\-\s]+", t):
        return False
    if re.fullmatch(r"[A-Z]{2}", t) and t not in {"MB", "BP", "HP", "TE"}:
        return False
    if re.search(r"COL0|000000", t, re.I):
        return False
    return True


def _ocr_texts(out) -> str:
    if out is None:
        return ""
    txts = getattr(out, "txts", None)
    if txts:
        return clean(" ".join(str(t) for t in txts if _keep_ocr_token(t)))
    if isinstance(out, tuple) and out:
        return _ocr_texts(out[0])
    if isinstance(out, list):
        bits = []
        for item in out:
            if isinstance(item, str) and _keep_ocr_token(item):
                bits.append(item)
            elif isinstance(item, (list, tuple)) and len(item) > 1 and _keep_ocr_token(item[1]):
                bits.append(str(item[1]))
        return clean(" ".join(bits))
    return ""


def ocr_layout(path: Path) -> tuple[str, list[str], list]:
    if not path.exists():
        return "", [], []
    engine = _ocr_engine()
    if engine is None:
        return "", [], []
    try:
        out = engine(str(path))
    except Exception:
        return "", [], []
    raw_txts = getattr(out, "txts", None)
    if raw_txts is None:
        text = _ocr_texts(out)
        return text, ([text] if text else []), []
    txts = [str(t) for t in raw_txts if _keep_ocr_token(t)]
    raw_boxes = getattr(out, "boxes", None)
    boxes: list = []
    if raw_boxes is not None:
        try:
            all_boxes = raw_boxes.tolist() if hasattr(raw_boxes, "tolist") else list(raw_boxes)
            kept = [i for i, t in enumerate(raw_txts) if _keep_ocr_token(t)]
            boxes = [all_boxes[i] for i in kept if i < len(all_boxes)]
        except Exception:
            boxes = []
    return clean(" ".join(txts)), txts, boxes


def axes_from_ocr(txts: list[str], boxes: list, width: int, height: int) -> dict:
    if not txts or not boxes or not width or not height:
        return {}
    left, bottom, top, right = [], [], [], []
    n = min(len(txts), len(boxes))
    for i in range(n):
        t = clean(str(txts[i]))
        if not t:
            continue
        b = boxes[i]
        try:
            if hasattr(b, "tolist"):
                b = b.tolist()
            xs = [float(p[0]) for p in b]
            ys = [float(p[1]) for p in b]
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        except Exception:
            continue
        if cx < width * 0.22:
            left.append(t)
        elif cx > width * 0.82:
            right.append(t)
        elif cy > height * 0.78:
            bottom.append(t)
        elif cy < height * 0.18:
            top.append(t)
    axes = {}
    if left:
        axes["y_axis"] = left[:15]
    if bottom:
        axes["x_axis"] = bottom[:15]
    if top:
        axes["title"] = top[:8]
    if right:
        axes["legend"] = right[:10]
    return axes


def axes_from_nearby(nearby: str) -> dict:
    """Recover axis ticks/units from vector text when raster OCR is empty."""
    text = clean(nearby or "")
    if len(text) < 8:
        return {}
    axes: dict = {}
    mb = re.search(r"((?:\d+\s+){2,6})\(?Mb\)?", text, re.I)
    if mb:
        ticks = re.findall(r"\d+", mb.group(0))
        axes["x_axis"] = ticks[:8] + (["Mb"] if re.search(r"\bMb\b", mb.group(0), re.I) else [])
    elif re.search(r"\bMb\b", text, re.I):
        axes["x_axis"] = re.findall(r"[A-Za-z]+\s+c\d+|[\d.]+\s*[–\-]\s*[\d.]+|\bMb\b", text)[:10]
    chrs = re.findall(r"\bChr\s*\d+\b", text, re.I)
    if chrs:
        axes.setdefault("title", [])
        axes["title"] = chrs[:8]
    ylabs = []
    for pat in (
        r"Retrotransposons",
        r"DNA transposons",
        r"Genes \(exons\)",
        r"CpG islands",
        r"Paralogues",
        r"Number of individuals",
        r"Total protein",
        r"Amino acids concentration",
    ):
        m = re.search(pat, text, re.I)
        if m:
            ylabs.append(m.group(0))
    if ylabs:
        axes["y_axis"] = ylabs[:12]
    return axes


def ocr_page_clip(page, rect, dest: Path) -> tuple[str, list[str], list, int, int]:
    if page is None or not rect:
        return "", [], [], 0, 0
    try:
        import pymupdf

        clip = pymupdf.Rect(*rect)
        w, h = float(clip.width), float(clip.height)
        if w < 20 or h < 20 or w * h > 1_200_000:
            return "", [], [], 0, 0
        scale = min(1.5, 900.0 / max(w, h))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
        if pix.width < 40 or pix.height < 40 or pix.width * pix.height > 1_600_000:
            return "", [], [], 0, 0
        pix.save(str(dest))
        text, tokens, boxes = ocr_layout(dest)
        try:
            dest.unlink()
        except Exception:
            pass
        return text, tokens, boxes, pix.width, pix.height
    except Exception:
        return "", [], [], 0, 0


def classify_figure(caption: str, ocr_text: str, nearby: str) -> str:
    blob = f"{caption} {ocr_text} {nearby}".lower()
    for kind, keys in FIGURE_TYPES:
        if any(k in blob for k in keys):
            return kind
    if re.search(r"\b(fig\.?|figure)\b", blob) and re.search(r"\d+(\.\d+)?\s*%", blob):
        return "graph"
    if len(ocr_text) < 12 and "micrograph" not in blob:
        return "image"
    return "other"


def extract_numbers(text: str) -> list[dict]:
    out, seen = [], set()
    for m in NUM_RE.finditer(text or ""):
        val, unit = m.group(1), (m.group(2) or "").strip()
        key = f"{val}|{unit.lower()}"
        if key in seen:
            continue
        seen.add(key)
        try:
            num = float(val)
        except ValueError:
            continue
        if abs(num) > 1e7:
            continue
        rec = {"value": num}
        if unit:
            rec["unit"] = unit
        out.append(rec)
        if len(out) >= 40:
            break
    return out


def extract_facts(text: str) -> list[str]:
    keys = (
        "digestib",
        "kafirin",
        "tannin",
        "protein bod",
        "lysine",
        "bioavailab",
        "cooked",
        "fermented",
    )
    facts = []
    for sent in re.split(r"(?<=[.!?])\s+", text or ""):
        s = clean(sent)
        if not (45 <= len(s) <= 280):
            continue
        low = s.lower()
        if "--- page" in low or low.startswith("fig.") or low.startswith("table "):
            continue
        if not any(k in low for k in keys):
            continue
        if not re.search(r"\d+(?:\.\d+)?\s*(%|kDa|kcal|g/kg|mg/g)", s, re.I):
            if not re.search(r"\d+(?:\.\d+)?", s):
                continue
            if not any(k in low for k in ("digestib", "kafirin", "tannin", "lysine")):
                continue
        facts.append(s)
        if len(facts) >= 20:
            break
    if facts:
        return facts
    for m in FACT_RE.finditer(text or ""):
        s = clean(m.group(1))
        if 40 <= len(s) <= 280:
            facts.append(s)
        if len(facts) >= 20:
            break
    return facts


def coverage_report(record: dict) -> dict:
    sections = record.get("sections") or {}
    figures = record.get("figures") or []
    tables = record.get("tables") or []
    refs = record.get("references") or []
    missing = []

    def nonempty(name: str, min_len: int = 80) -> bool:
        return len((sections.get(name) or "").strip()) >= min_len

    want_figs = any((c.get("kind") == "figure") for c in (record.get("captions") or [])) or len(figures) > 0
    want_tabs = any((c.get("kind") == "table") for c in (record.get("captions") or [])) or len(tables) > 0
    skip = {"front_matter", "references", "acknowledgements", "body", "abstract"}
    topic_n = sum(
        1
        for k, v in sections.items()
        if k not in skip and len((v or "").strip()) >= 80
    )

    checks = {
        "abstract": bool((record.get("abstract") or "").strip()) or nonempty("abstract", 40),
        "methods": nonempty("methods") or any("method" in k for k in sections),
        "results": nonempty("results") or topic_n >= 2,
        "discussion_or_conclusion": nonempty("discussion")
        or nonempty("conclusion")
        or nonempty("acknowledgements")
        or topic_n >= 2,
        "references": len(refs) >= 3,
        "figures": (not want_figs) or len(figures) > 0,
        "figure_captions": (not want_figs)
        or sum(1 for f in figures if (f.get("caption_guess") or "").strip()) >= max(1, len(figures) // 2),
        "figure_ocr_or_labels": (not want_figs)
        or any(
            (f.get("ocr_text") or "").strip()
            or (f.get("nearby_text") or "").strip()
            or f.get("axis_labels")
            or f.get("axes")
            for f in figures
        ),
        "tables": (not want_tabs) or len(tables) > 0,
        "table_grids": (not want_tabs) or any((t.get("n_rows") or 0) >= 2 for t in tables),
        "key_claims": len(record.get("key_claims") or []) >= 1
        or len(record.get("quantitative_facts") or []) >= 1,
        "quantitative_facts": len(record.get("quantitative_facts") or []) >= 1
        or len(record.get("key_claims") or []) >= 1,
    }
    for k, ok in checks.items():
        if not ok:
            missing.append(k)

    n = len(checks)
    score = int(round(100 * sum(1 for v in checks.values() if v) / n)) if n else 0
    return {
        "score": score,
        "checks": checks,
        "missing": missing,
        "n_figures": len(figures),
        "n_figures_with_ocr": sum(1 for f in figures if (f.get("ocr_text") or "").strip()),
        "n_figures_with_labels": sum(1 for f in figures if (f.get("nearby_text") or "").strip()),
        "n_tables": len(tables),
        "n_table_grids": sum(1 for t in tables if (t.get("n_rows") or 0) >= 2),
        "n_references": len(refs),
        "n_facts": len(record.get("quantitative_facts") or []),
    }


def build_components(record: dict) -> list[dict]:
    pid = record.get("paper_id")
    title = record.get("title")
    doi = record.get("doi")
    comps = []

    def add(kind: str, cid: str, page, text: str, extra: dict | None = None):
        text = clean(text)
        if len(text) < 8:
            return
        # Split long text into overlapping windows instead of truncating it, so nothing
        # past the first few thousand characters becomes unsearchable.
        size, overlap = 4000, 300
        starts = range(0, max(len(text) - overlap, 1), size - overlap)
        for n, i in enumerate(starts):
            rec = {
                "paper_id": pid,
                "title": title,
                "doi": doi,
                "component": kind,
                "id": cid if n == 0 else f"{cid}#{n + 1}",
                "page": page,
                "text": text[i : i + size],
            }
            if extra:
                rec.update(extra)
            comps.append(rec)

    add("abstract", "abstract", 1, record.get("abstract") or "")
    for name, body in (record.get("sections") or {}).items():
        if name == "references":
            continue
        add("section", name, None, f"{name}: {body}")
    for claim in record.get("key_claims") or []:
        add("claim", "claim", None, claim)
    for fact in record.get("quantitative_facts") or []:
        add("fact", "fact", None, fact)
    for cap in record.get("captions") or []:
        add("caption", cap.get("label") or "caption", None, f"{cap.get('label')} {cap.get('caption')}")
    for fig in record.get("figures") or []:
        blob = " ".join(
            [
                fig.get("caption_guess") or "",
                fig.get("nearby_text") or "",
                fig.get("ocr_text") or "",
                fig.get("figure_type") or "",
            ]
        )
        add(
            "figure",
            fig.get("id"),
            fig.get("page"),
            blob,
            {
                "figure_type": fig.get("figure_type"),
                "numbers": fig.get("numbers") or [],
            },
        )
    for tab in record.get("tables") or []:
        headers = " ".join(str(h) for h in (tab.get("headers") or []))
        from structured_store import table_rows

        rows = table_rows(pid, tab)
        row_txt = " | ".join(" ".join(str(c) for c in r) for r in rows)
        add(
            "table",
            tab.get("id"),
            tab.get("page"),
            f"{tab.get('caption_guess') or ''} {headers} {row_txt}",
            {"n_rows": tab.get("n_rows"), "n_cols": tab.get("n_cols")},
        )
    for img in record.get("page_image_text") or []:
        add("image_text", f"page_{img.get('page')}", img.get("page"), img.get("text") or "")
    return comps


def enrich_figures(doc, paper_dir: Path, figures: list[dict]) -> list[dict]:
    """Add nearby_text, ocr_text, figure_type, numbers to each figure."""
    fig_dir = paper_dir / "figures"
    for fig in figures:
        page_i = int(fig.get("page") or 1) - 1
        nearby = ""
        page = None
        rect = None
        try:
            page = doc.load_page(page_i)
            rect = image_rect_for_xref(page, fig.get("xref"))
            nearby = nearby_page_text(page, rect)
        except Exception:
            nearby = ""
        png = fig_dir / f"{fig['id']}.png"
        ocr, ocr_tokens, boxes = ocr_layout(png)
        w, h = int(fig.get("width") or 0), int(fig.get("height") or 0)
        if len(ocr) < 12 and len(nearby) < 40 and page is not None and rect:
            clip_ocr, clip_tokens, clip_boxes, cw, ch = ocr_page_clip(
                page, rect, fig_dir / f"{fig['id']}_clip.png"
            )
            if len(clip_ocr) > len(ocr):
                ocr, ocr_tokens, boxes = clip_ocr, clip_tokens, clip_boxes
                if cw and ch:
                    w, h = cw, ch
        caption = fig.get("caption_guess") or ""
        fig["nearby_text"] = nearby[:1500]
        fig["ocr_text"] = ocr[:2000]
        axes = axes_from_ocr(ocr_tokens, boxes, w, h)
        near_axes = axes_from_nearby(nearby)
        weak = not (axes.get("x_axis") or axes.get("y_axis")) or (
            len(" ".join((axes.get("x_axis") or []) + (axes.get("y_axis") or []))) < 8
        )
        if weak:
            for k, v in near_axes.items():
                if v and (not axes.get(k) or weak):
                    axes[k] = v
        fig["axes"] = axes
        fig["figure_type"] = classify_figure(
            caption, ocr, nearby + " " + " ".join(fig["axes"].get("x_axis") or [])
        )
        if fig["axes"] and fig["figure_type"] in {"image", "other", "photo"}:
            if fig["axes"].get("x_axis") or fig["axes"].get("y_axis"):
                fig["figure_type"] = "graph"
        fig["numbers"] = extract_numbers(
            " ".join(
                [
                    caption,
                    ocr,
                    nearby[:400],
                    " ".join(fig["axes"].get("x_axis") or []),
                    " ".join(fig["axes"].get("y_axis") or []),
                ]
            )
        )
        fig["axis_labels"] = list(
            dict.fromkeys(
                (fig["axes"].get("x_axis") or [])
                + (fig["axes"].get("y_axis") or [])
                + (fig["axes"].get("title") or [])
            )
        )[:16]
        fig["search_text"] = clean(
            " ".join(
                [
                    caption,
                    nearby,
                    ocr,
                    fig["figure_type"],
                    " ".join(fig["axis_labels"]),
                ]
            )
        )[:2500]
        meta_path = fig_dir / f"{fig['id']}.json"
        try:
            meta_path.write_text(json.dumps(fig, indent=2))
        except Exception:
            pass
    return figures


def enrich_record(record: dict, paper_dir: Path, doc=None) -> dict:
    full_text = ""
    ft = paper_dir / "fulltext.txt"
    if ft.exists():
        full_text = ft.read_text(errors="ignore")
    if doc is not None and record.get("figures"):
        record["figures"] = enrich_figures(doc, paper_dir, record["figures"])
    record["quantitative_facts"] = extract_facts(full_text)
    record["coverage"] = coverage_report(record)
    stats = dict(record.get("stats") or {})
    cov = record["coverage"]
    stats.update(
        {
            "coverage_score": cov["score"],
            "n_figure_ocr": cov["n_figures_with_ocr"],
            "n_facts": cov["n_facts"],
            "missing_components": cov["missing"],
        }
    )
    record["stats"] = stats
    comps = build_components(record)
    (paper_dir / "components.json").write_text(json.dumps(comps, indent=2))
    record["n_components"] = len(comps)
    return record


def rebuild_component_index() -> dict:
    items = []
    for d in sorted(STRUCTURED.iterdir()):
        if not d.is_dir():
            continue
        path = d / "components.json"
        if not path.exists():
            continue
        try:
            items.extend(json.loads(path.read_text()))
        except Exception:
            continue
    out = {"n_components": len(items), "items": items}
    (STRUCTURED / "component_index.json").write_text(json.dumps(out))
    return {"n_components": len(items), "n_papers": len({i.get("paper_id") for i in items})}


def search_components(query: str, component: str = "", limit: int = 12) -> list[dict]:
    path = STRUCTURED / "component_index.json"
    if not path.exists():
        rebuild_component_index()
    data = json.loads(path.read_text()) if path.exists() else {"items": []}
    q = (query or "").lower().strip()
    tokens = [t for t in re.findall(r"[a-z0-9%]+", q) if len(t) > 2]
    kind = (component or "").strip().lower()
    scored = []
    for item in data.get("items") or []:
        if kind and (item.get("component") or "").lower() != kind:
            continue
        blob = (item.get("text") or "").lower()
        if q and q in blob:
            score = 10 + blob.count(q)
        else:
            score = sum(2 if t in blob else 0 for t in tokens)
        if score <= 0:
            continue
        scored.append((score, item))
    scored.sort(key=lambda x: -x[0])
    return [it for _, it in scored[: max(1, min(int(limit), 40))]]
