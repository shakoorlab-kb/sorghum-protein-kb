"""The document side of the source of truth: what each document is, its page texts,
and the original file rendered page by page with a quote highlighted on it.

Two origins:
  kb      papers already extracted by the Sorghum KB (read-only mount, page texts
          precomputed there including OCR of scanned pages)
  upload  files the admin adds through the app (stored under data/uploads/<doc_id>/)
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf as fitz

from .config import settings

TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".html", ".htm"}
SUPPORTED_EXTS = {".pdf", ".docx", *TEXT_EXTS}
# Non-PDF documents are paged by characters so citations still have a "page".
TEXT_PAGE_CHARS = 3500


@dataclass
class DocMeta:
    id: str
    title: str
    origin: str                      # kb | upload
    file_path: str                   # absolute path of the original
    file_type: str                   # pdf | docx | text
    year: int | None = None
    doi: str = ""
    journal: str = ""
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    doc_type: str = "paper"          # paper | patent | thesis | report | document
    keywords: list[str] = field(default_factory=list)
    url: str = ""
    n_pages: int = 0
    content_hash: str = ""
    parent_id: str = ""              # supplements: the paper they belong to
    text_path: str = ""              # supplements: extracted text in the KB

    def to_dict(self) -> dict:
        return asdict(self)


# ---- KB papers --------------------------------------------------------------

def kb_structured_dir() -> Path:
    return settings.kb_dir / "knowledge_base" / "structured"


def _year(v) -> int | None:
    try:
        y = int(str(v)[:4])
        return y if 1800 < y < 2100 else None
    except (TypeError, ValueError):
        return None


def list_kb_papers() -> list[DocMeta]:
    out: list[DocMeta] = []
    root = kb_structured_dir()
    if not root.exists():
        return out
    for pj in sorted(root.glob("*/paper.json")):
        try:
            d = json.loads(pj.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        src = settings.kb_dir / (d.get("source_pdf") or "")
        if not src.is_file():
            continue
        pages = sorted((pj.parent / "pages").glob("page_*.txt"))
        h = hashlib.sha1()
        h.update(str(src.stat().st_size).encode())
        for p in pages:
            h.update(f"{p.name}:{p.stat().st_size}".encode())
        authors = d.get("authors") or []
        out.append(DocMeta(
            id=d.get("paper_id") or pj.parent.name,
            title=(d.get("title") or pj.parent.name).strip(),
            origin="kb",
            file_path=str(src),
            file_type="pdf",
            year=_year(d.get("year")),
            doi=(d.get("doi") or "").strip(),
            journal=(d.get("journal") or "").strip(),
            authors=[a for a in authors if isinstance(a, str)][:60],
            abstract=(d.get("abstract") or "").strip()[:6000],
            doc_type=d.get("document_type") or _guess_type(d.get("title") or "", d.get("journal") or ""),
            keywords=[k for k in (d.get("keywords") or []) if isinstance(k, str)][:30],
            url=d.get("landing_url") or (f"https://doi.org/{d['doi']}" if d.get("doi") else ""),
            n_pages=len(pages),
            content_hash=h.hexdigest(),
        ))
    return out


def kb_page_texts(doc: DocMeta) -> list[str]:
    if doc.doc_type == "supplement":
        return supplement_pages(Path(doc.text_path))
    pages_dir = kb_structured_dir() / doc.id / "pages"
    return [p.read_text(errors="replace") for p in sorted(pages_dir.glob("page_*.txt"))]


# Supplementary tables can be huge (genome-wide gene lists); the first part is indexed for the
# app, and the MCP's exact search still covers every row.
SUPPLEMENT_CHARS = 600_000


def supplement_pages(text_path: Path) -> list[str]:
    """Supplement text split into pages by lines (sheets have one record per line, no blank lines)."""
    try:
        text = text_path.read_text(errors="replace")[:SUPPLEMENT_CHARS]
    except OSError:
        return []
    pages, buf = [], []
    size = 0
    for line in text.splitlines():
        if buf and size + len(line) > TEXT_PAGE_CHARS:
            pages.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        pages.append("\n".join(buf))
    return pages


_GENERIC_LABEL = re.compile(r"^\s*(supplementary file.*|)\s*$", re.I)


def list_kb_supplements(papers: list[DocMeta] | None = None) -> list[DocMeta]:
    """Each supplementary file of a KB paper as its own document, linked to the paper."""
    parents = {p.id: p for p in (papers if papers is not None else list_kb_papers())}
    out: list[DocMeta] = []
    for pid, parent in parents.items():
        pj = kb_structured_dir() / pid / "paper.json"
        try:
            sups = json.loads(pj.read_text()).get("supplements") or []
        except (OSError, json.JSONDecodeError):
            continue
        for s in sups:
            name = s.get("file") or ""
            text = settings.kb_dir / (s.get("text_file") or "")
            if not name or not text.is_file():
                continue
            original = settings.kb_dir / (s.get("path") or "")
            label = "" if _GENERIC_LABEL.match(s.get("label") or "") else s["label"].strip()
            n = len(supplement_pages(text))
            if not n or not text.read_text(errors="replace").strip():
                continue  # e.g. an image whose OCR produced nothing; its transcription is indexed instead
            out.append(DocMeta(
                id=f"{pid}__supp_{hashlib.sha1(name.encode()).hexdigest()[:10]}",
                title=f"{parent.title} [supplement: {f'{label}, {name}' if label else name}]",
                origin="kb",
                file_path=str(original if original.is_file() else text),
                file_type="text",
                year=parent.year, doi=parent.doi, journal=parent.journal, authors=parent.authors,
                doc_type="supplement", url=parent.url, n_pages=n,
                content_hash=hashlib.sha1(f"{name}:{text.stat().st_size}:{SUPPLEMENT_CHARS}".encode()).hexdigest(),
                parent_id=pid, text_path=str(text),
            ))
    return out


def _guess_type(title: str, journal: str) -> str:
    t = f"{title} {journal}".lower()
    if "thesis" in t or "dissertation" in t:
        return "thesis"
    if "patent" in t:
        return "patent"
    return "paper"


# ---- uploads ----------------------------------------------------------------

def upload_dir(doc_id: str) -> Path:
    return settings.uploads_dir / doc_id


def new_upload_id(filename: str, data: bytes) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", Path(filename).stem.lower()).strip("_")[:48] or "document"
    return f"up_{stem}_{hashlib.sha1(data).hexdigest()[:10]}"


def save_upload(filename: str, data: bytes) -> tuple[str, Path]:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise ValueError(f"unsupported file type {ext or '(none)'}; use PDF, DOCX or text")
    doc_id = new_upload_id(filename, data)
    d = upload_dir(doc_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"original{ext}"
    path.write_bytes(data)
    (d / "source.json").write_text(json.dumps({"filename": filename, "bytes": len(data)}))
    return doc_id, path


def file_type_of(path: Path) -> str:
    ext = path.suffix.lower()
    return "pdf" if ext == ".pdf" else "docx" if ext == ".docx" else "text"


_ocr = None


def _ocr_page(page: fitz.Page) -> str:
    """OCR one scanned page (rapidocr, CPU). Empty string when OCR is unavailable."""
    global _ocr
    try:
        if _ocr is None:
            from rapidocr import RapidOCR
            _ocr = RapidOCR()
        pix = page.get_pixmap(dpi=200)
        result = _ocr(pix.tobytes("png"))
        txts = getattr(result, "txts", None) or ()
        return "\n".join(txts)
    except Exception:
        return ""


def extract_page_texts(path: Path) -> list[str]:
    """Text of each page of an uploaded file (PDF text layer, OCR for scanned pages)."""
    ft = file_type_of(path)
    if ft == "pdf":
        texts = []
        with fitz.open(path) as pdf:
            for page in pdf:
                t = page.get_text("text") or ""
                if len(t.strip()) < 40 and page.get_images():
                    t = _ocr_page(page) or t
                texts.append(t)
        return texts
    if ft == "docx":
        import docx
        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs]
        for table in d.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        text = "\n".join(parts)
    else:
        text = path.read_text(errors="replace")
        if path.suffix.lower() in {".html", ".htm"}:
            text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
    return _paginate(text)


def upload_page_texts(path: Path) -> list[str]:
    """Page texts of an upload, extracted once and cached next to the file."""
    cache = path.parent / "pages.json"
    if cache.exists():
        return json.loads(cache.read_text())
    pages = extract_page_texts(path)
    cache.write_text(json.dumps(pages))
    return pages


def _paginate(text: str) -> list[str]:
    pages, buf = [], ""
    for para in re.split(r"\n\s*\n", text):
        if buf and len(buf) + len(para) > TEXT_PAGE_CHARS:
            pages.append(buf)
            buf = ""
        buf = f"{buf}\n\n{para}" if buf else para
    if buf.strip():
        pages.append(buf)
    return pages or [""]


DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;)\]])", re.I)


def upload_meta(doc_id: str, path: Path, pages: list[str], original_name: str) -> DocMeta:
    title = Path(original_name).stem.replace("_", " ").strip()
    if file_type_of(path) == "pdf":
        with fitz.open(path) as pdf:
            mt = (pdf.metadata or {}).get("title") or ""
            if len(mt.strip()) > 8 and not mt.lower().startswith(("microsoft", "untitled")):
                title = mt.strip()
    head = "\n".join(pages[:2])
    m = DOI_RE.search(head)
    return DocMeta(
        id=doc_id, title=title, origin="upload", file_path=str(path),
        file_type=file_type_of(path), doi=m.group(1).rstrip(".") if m else "",
        doc_type="document", n_pages=len(pages),
        content_hash=hashlib.sha1(path.read_bytes()).hexdigest(),
    )


# ---- rendering + highlights --------------------------------------------------

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("­", "").replace("-\n", "")
    return re.sub(r"\s+", " ", s).strip().lower()


def render_page_png(file_path: str, page: int, dpi: int = 110) -> bytes:
    dpi = max(50, min(int(dpi), 220))
    key = hashlib.sha1(f"{file_path}:{page}:{dpi}".encode()).hexdigest()
    cache = settings.render_cache / f"{key}.png"
    if cache.exists():
        return cache.read_bytes()
    with fitz.open(file_path) as pdf:
        if not 1 <= page <= pdf.page_count:
            raise IndexError(page)
        png = pdf[page - 1].get_pixmap(dpi=dpi).tobytes("png")
    settings.render_cache.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(png)
    return png


def page_size(file_path: str, page: int) -> tuple[float, float]:
    with fitz.open(file_path) as pdf:
        r = pdf[page - 1].rect
        return r.width, r.height


def highlight_rects(file_path: str, page: int, quote: str) -> list[list[float]]:
    """Rectangles (PDF points, top-left origin) covering `quote` on the page.

    Searches the whole quote first, then its longest sentence-ish fragments, so a
    quote that crosses a column break or hyphenation still lights up most of its
    text. Returns [] when the page is a scan whose text came from OCR.
    """
    quote = (quote or "").strip()
    if not quote:
        return []
    with fitz.open(file_path) as pdf:
        if not 1 <= page <= pdf.page_count:
            return []
        pg = pdf[page - 1]
        flat = re.sub(r"\s+", " ", quote)
        hits = pg.search_for(flat, quads=False)
        if not hits:
            pieces = [p.strip() for p in re.split(r"(?<=[.;:,)])\s+", flat) if len(p.strip()) > 18]
            for piece in pieces[:12]:
                hits.extend(pg.search_for(piece[:180], quads=False))
            # Runs of words: cover text split by hyphenated line breaks or column changes.
            words = flat.split()
            for i in range(0, max(len(words) - 2, 1), 4):
                frag = " ".join(words[i:i + 6])
                if len(frag) > 12:
                    hits.extend(pg.search_for(frag, quads=False))
        seen, uniq = set(), []
        for r in hits:
            key = (round(r.x0), round(r.y0), round(r.x1), round(r.y1))
            if key not in seen:
                seen.add(key)
                uniq.append(r)
        hits = uniq
        return [[round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)] for r in hits[:80]]


def locate_quote(pages: list[str], quote: str, hint_page: int | None = None) -> int | None:
    """1-based page whose text contains the quote (normalised), preferring the hint."""
    q = _norm(quote)[:160]
    if len(q) < 12:
        return hint_page
    order = list(range(len(pages)))
    if hint_page and 1 <= hint_page <= len(pages):
        order.remove(hint_page - 1)
        order.insert(0, hint_page - 1)
    for i in order:
        if q in _norm(pages[i]):
            return i + 1
    return hint_page


def quote_in_text(quote: str, text: str) -> bool:
    """True when the quote (or its first 120 normalised chars) occurs in the text."""
    q = _norm(quote)
    if len(q) < 10:
        return False
    t = _norm(text)
    if q in t or q in _norm(text.replace("-\n", "- ")).replace("- ", "-"):
        return True
    # OCR text splits words as "digesti- bility"; compare with those joins removed on both sides.
    unsplit = re.compile(r"(?<=[a-z])- (?=[a-z])")
    if unsplit.sub("", q) in unsplit.sub("", _norm(text.replace("-\n", "- "))):
        return True
    # Line-numbered manuscripts put margin numbers inside sentences; ignore bare numbers on
    # both sides (the words must still match in order).
    def words(x: str) -> str:
        return " ".join(w for w in re.findall(r"[a-z0-9]+", x) if not w.isdigit())
    qw = words(q)
    if len(qw) > 30 and qw in words(t):
        return True
    q2 = re.sub(r"[^a-z0-9 ]", "", q)
    t2 = re.sub(r"[^a-z0-9 ]", "", t)
    return q2[:120] in t2 if len(q2) > 20 else False
