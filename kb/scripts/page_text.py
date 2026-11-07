#!/usr/bin/env python3
"""Complete text for every PDF page: the text layer plus OCR wherever text is hidden.

Two cases need OCR:
  - scanned pages: no usable text layer (or a garbled one), so the whole page is OCR'd
    and becomes the page's body text;
  - raster regions on normal pages: tables, figure labels or whole sections saved as
    pictures. Each sizable image with no text layer over it is OCR'd and kept as the
    page's image_text (searchable, but kept out of the section/claim heuristics).

OCR output is grouped into reading-order lines, so table rows stay on one line
(e.g. "PI 533919  12.4  71.2"), which is what exact identifier search relies on.
Results are cached per PDF (keyed by file hash) in knowledge_base/ocr_cache/.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "knowledge_base" / "ocr_cache"

OCR_VERSION = 2           # bump to invalidate cached OCR after changing the parameters below
PAGE_DPI = 220            # full-page OCR resolution for scanned pages
MIN_LAYER_CHARS = 200     # pages with less text-layer text than this are treated as scanned
MIN_IMAGE_FRAC = 0.06     # skip images smaller than this fraction of the page
MAX_LAYER_CHARS_IN_IMAGE = 40  # an image with more text-layer text over it is already readable
REGION_ZOOM = 3.0         # render image regions at 3x (216 DPI) before OCR
MAX_SIDE_PX = 3200        # cap on rendered size (also the OCR engine's max side)
MIN_SCORE = 0.5           # drop low-confidence OCR boxes


@lru_cache(maxsize=1)
def _engine():
    try:
        from rapidocr import RapidOCR

        return RapidOCR(params={"Global.max_side_len": MAX_SIDE_PX, "Global.log_level": "error"})
    except Exception:
        return None


def ocr_available() -> bool:
    return _engine() is not None


def _clean_line(s: str) -> str:
    return re.sub(r"[ \t]+", " ", (s or "").replace(" ", " ")).strip()


def looks_garbled(text: str) -> bool:
    """A text layer made of (cid:NN) codes or replacement glyphs is as good as none."""
    t = text.strip()
    if not t:
        return True
    if t.count("(cid:") > 20 or t.count("�") > 20:
        return True
    alnum = sum(ch.isalnum() for ch in t)
    return alnum / max(len(t), 1) < 0.35


def ocr_png_items(png: bytes) -> list[dict]:
    """OCR a PNG into text boxes {x0, x1, y, h, t} (cached raw, so layout can change freely)."""
    engine = _engine()
    if engine is None or not png:
        return []
    try:
        out = engine(png)
    except Exception:
        return []
    txts = list(getattr(out, "txts", None) or [])
    boxes = getattr(out, "boxes", None)
    scores = list(getattr(out, "scores", None) or [1.0] * len(txts))
    if not txts or boxes is None:
        return [{"x0": 0, "x1": 0, "y": float(i), "h": 1, "t": _clean_line(t)} for i, t in enumerate(txts) if _clean_line(t)]
    boxes = boxes.tolist() if hasattr(boxes, "tolist") else list(boxes)
    items = []
    for box, text, score in zip(boxes, txts, scores):
        text = _clean_line(str(text))
        if not text or float(score) < MIN_SCORE:
            continue
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append({"x0": round(min(xs), 1), "x1": round(max(xs), 1),
                      "y": round((min(ys) + max(ys)) / 2, 1), "h": round(max(ys) - min(ys), 1), "t": text})
    return items


def _group_lines(items: list[dict]) -> list[str]:
    """Group boxes with the same baseline into lines; wide gaps become double spaces (table columns)."""
    if not items:
        return []
    heights = sorted(i["h"] for i in items)
    tol = max(4.0, 0.5 * heights[len(heights) // 2])
    lines: list[list[dict]] = []
    for it in sorted(items, key=lambda i: i["y"]):
        if lines and abs(it["y"] - lines[-1][0]["y"]) <= tol:
            lines[-1].append(it)
        else:
            lines.append([it])
    out = []
    for line in lines:
        line.sort(key=lambda i: i["x0"])
        parts = [line[0]["t"]]
        for prev, cur in zip(line, line[1:]):
            gap = cur["x0"] - prev["x1"]
            parts.append(("  " if gap > 1.2 * max(prev["h"], 1) else " ") + cur["t"])
        out.append("".join(parts))
    return out


def layout_rows(items: list[dict]) -> list[str]:
    """Full-width lines: keeps every table row together (used for exact search)."""
    return _group_lines(items)


def layout_columns(items: list[dict]) -> list[str]:
    """Reading order for two-column pages: left column, then right, split at full-width blocks."""
    if len(items) < 12:
        return _group_lines(items)
    width = max(i["x1"] for i in items) or 1.0
    best = None
    for k in range(35, 66):
        xg = width * k / 100
        crossing = sum(1 for i in items if i["x0"] < xg < i["x1"])
        if best is None or crossing < best[1]:
            best = (xg, crossing)
    xg, crossing = best
    left = sum(1 for i in items if i["x1"] <= xg)
    right = sum(1 for i in items if i["x0"] >= xg)
    if crossing > 0.12 * len(items) or left < 5 or right < 5:
        return _group_lines(items)
    out: list[str] = []
    seg: list[dict] = []

    def flush():
        out.extend(_group_lines([i for i in seg if i["x1"] <= xg]))
        out.extend(_group_lines([i for i in seg if i["x1"] > xg]))
        seg.clear()

    full: list[dict] = []
    for it in sorted(items, key=lambda i: i["y"]):
        if it["x0"] < xg < it["x1"]:
            if seg:
                flush()
            full.append(it)
        else:
            if full:
                out.extend(_group_lines(full))
                full = []
            seg.append(it)
    if seg:
        flush()
    if full:
        out.extend(_group_lines(full))
    return out


def _file_key(pdf_path: Path) -> str:
    h = hashlib.sha1()
    with open(pdf_path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:20]


class OcrCache:
    def __init__(self, pdf_path: Path | None):
        self.path = None
        self.data: dict = {}
        self.dirty = False
        if pdf_path is not None and Path(pdf_path).exists():
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self.path = CACHE_DIR / f"{_file_key(Path(pdf_path))}.json"
            if self.path.exists():
                try:
                    self.data = json.loads(self.path.read_text())
                except Exception:
                    self.data = {}

    def get(self, key: str, compute):
        key = f"v{OCR_VERSION}:{key}"
        if key not in self.data:
            self.data[key] = compute()
            self.dirty = True
        return self.data[key]

    def save(self) -> None:
        if self.path is not None and self.dirty:
            self.path.write_text(json.dumps(self.data))
            self.dirty = False


def _render_png(page, zoom: float, clip=None) -> bytes:
    import pymupdf

    rect = clip or page.rect
    zoom = min(zoom, MAX_SIDE_PX / max(rect.width, rect.height, 1))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
    return pix.tobytes("png")


def _image_regions(page) -> list:
    """Sizable image rectangles on the page, merged when they overlap."""
    import pymupdf

    page_area = max(page.rect.get_area(), 1.0)
    rects = []
    for info in page.get_images(full=True):
        try:
            for r in page.get_image_rects(info[0]):
                r = r & page.rect
                if not r.is_empty and r.get_area() / page_area >= MIN_IMAGE_FRAC:
                    rects.append(pymupdf.Rect(r))
        except Exception:
            continue
    merged: list = []
    for r in sorted(rects, key=lambda x: (x.y0, x.x0)):
        for m in merged:
            if m.intersects(r):
                m |= r
                break
        else:
            merged.append(r)
    return merged


def extract_page(page, cache: OcrCache, ocr: bool = True) -> dict:
    """Return {page, text, image_text, source, text_layer_chars, ocr_regions[, rows_text]}.

    text       body text in reading order (text layer, or OCR for scanned pages)
    rows_text  scanned pages only: the same OCR as full-width rows (table rows intact)
    image_text OCR of raster regions on text pages, as full-width rows
    """
    index = page.number
    layer = page.get_text("text") or ""
    rec = {
        "page": index + 1,
        "text": layer,
        "image_text": "",
        "source": "text_layer",
        "text_layer_chars": len(layer.strip()),
        "ocr_regions": 0,
    }
    if not ocr or not ocr_available():
        return rec

    if len(layer.strip()) < MIN_LAYER_CHARS or looks_garbled(layer):
        items = cache.get(f"p{index}:page", lambda: ocr_png_items(_render_png(page, PAGE_DPI / 72.0)))
        ocr_text = "\n".join(layout_columns(items))
        if len(ocr_text.strip()) > len(layer.strip()) or looks_garbled(layer):
            rec["text"] = ocr_text
            rec["rows_text"] = "\n".join(layout_rows(items))
            rec["source"] = "ocr_page"
        return rec

    chunks = []
    for r in _image_regions(page):
        if len((page.get_text("text", clip=r) or "").strip()) > MAX_LAYER_CHARS_IN_IMAGE:
            continue
        key = f"p{index}:img:{r.x0:.0f},{r.y0:.0f},{r.x1:.0f},{r.y1:.0f}"
        items = cache.get(key, lambda r=r: ocr_png_items(_render_png(page, REGION_ZOOM, clip=r)))
        lines = layout_rows(items)
        if lines:
            chunks.append("\n".join(lines))
            rec["ocr_regions"] += 1
    rec["image_text"] = "\n\n".join(chunks)
    return rec


def extract_document(doc, pdf_path: Path | None = None, ocr: bool = True) -> list[dict]:
    cache = OcrCache(pdf_path)
    try:
        return [extract_page(doc.load_page(i), cache, ocr=ocr) for i in range(doc.page_count)]
    finally:
        cache.save()


def pdf_full_text(pdf_path: Path, ocr: bool = True) -> str:
    """Whole-document text (text layer + OCR) for callers that only need a string."""
    import pymupdf

    doc = pymupdf.open(pdf_path)
    try:
        pages = extract_document(doc, pdf_path, ocr=ocr)
    finally:
        doc.close()
    parts = []
    for p in pages:
        body = p["text"]
        if p["image_text"]:
            body += f"\n[Text in images on page {p['page']}]\n{p['image_text']}"
        parts.append(f"--- Page {p['page']} ---\n{body}")
    return "\n\n".join(parts)


if __name__ == "__main__":
    import sys

    import pymupdf

    for arg in sys.argv[1:]:
        d = pymupdf.open(arg)
        for p in extract_document(d, Path(arg)):
            print(f"p{p['page']:>3} {p['source']:<10} layer={p['text_layer_chars']:>5} "
                  f"text={len(p['text']):>5} image_ocr_regions={p['ocr_regions']} image_text={len(p['image_text'])}")
