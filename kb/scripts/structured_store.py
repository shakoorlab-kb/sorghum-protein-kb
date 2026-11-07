#!/usr/bin/env python3
"""Helpers to load structured paper records for MCP / CLI."""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRUCTURED = ROOT / "knowledge_base" / "structured"


def load_index() -> dict:
    path = STRUCTURED / "index.json"
    if not path.exists():
        return {"n_papers": 0, "papers": []}
    return json.loads(path.read_text())


def resolve_paper_id(query: str) -> str | None:
    """Resolve paper_id, DOI, or title substring to paper_id."""
    q = (query or "").strip()
    if not q:
        return None
    index = load_index()
    papers = index.get("papers") or []

    # exact paper_id
    for p in papers:
        if p.get("paper_id") == q:
            return q
    # path-like
    if (STRUCTURED / q / "paper.json").exists():
        return q

    ql = q.lower()
    # DOI
    if ql.startswith("10."):
        for p in papers:
            if (p.get("doi") or "").lower() == ql:
                return p["paper_id"]
        doi_map_path = STRUCTURED / "doi_to_id.json"
        if doi_map_path.exists():
            m = json.loads(doi_map_path.read_text())
            if ql in m:
                return m[ql]

    # title substring / fuzzy
    hits = []
    for p in papers:
        title = (p.get("title") or "").lower()
        if ql in title or title in ql:
            hits.append(p)
    if len(hits) == 1:
        return hits[0]["paper_id"]
    if hits:
        # prefer newest
        hits.sort(key=lambda x: -(x.get("year") or 0))
        return hits[0]["paper_id"]

    # token overlap
    tokens = [t for t in re.findall(r"[a-z0-9]+", ql) if len(t) > 3]
    if tokens:
        scored = []
        for p in papers:
            title = (p.get("title") or "").lower()
            score = sum(1 for t in tokens if t in title)
            if score >= max(2, len(tokens) // 2):
                scored.append((score, p))
        if scored:
            scored.sort(key=lambda x: (-x[0], -(x[1].get("year") or 0)))
            return scored[0][1]["paper_id"]
    return None


def load_card(paper_id: str) -> dict | None:
    path = STRUCTURED / paper_id / "card.json"
    if path.exists():
        return json.loads(path.read_text())
    paper = load_paper(paper_id, light=True)
    return paper


def load_paper(paper_id: str, light: bool = False) -> dict | None:
    path = STRUCTURED / paper_id / "paper.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if light:
        # strip huge section bodies
        light_sections = {
            k: (v[:1500] + "…" if isinstance(v, str) and len(v) > 1500 else v)
            for k, v in (data.get("sections") or {}).items()
        }
        return {
            "paper_id": data.get("paper_id"),
            "title": data.get("title"),
            "authors": data.get("authors"),
            "year": data.get("year"),
            "doi": data.get("doi"),
            "journal": data.get("journal"),
            "keywords": data.get("keywords"),
            "abstract": data.get("abstract"),
            "landing_url": data.get("landing_url"),
            "stats": data.get("stats"),
            "section_names": data.get("section_names"),
            "sections_preview": light_sections,
            "coverage": data.get("coverage"),
            "quantitative_facts": (data.get("quantitative_facts") or [])[:12],
            "n_components": data.get("n_components"),
            "figures": [
                {
                    k: f.get(k)
                    for k in (
                        "id",
                        "page",
                        "caption_guess",
                        "file",
                        "width",
                        "height",
                        "figure_type",
                        "ocr_text",
                        "nearby_text",
                        "numbers",
                        "axes",
                        "axis_labels",
                    )
                }
                for f in (data.get("figures") or [])
            ],
            "tables": [
                {
                    k: t.get(k)
                    for k in ("id", "page", "caption_guess", "n_rows", "n_cols", "headers", "markdown_file")
                }
                for t in (data.get("tables") or [])
            ],
            "key_claims": data.get("key_claims") or [],
            "n_references": len(data.get("references") or []),
            "files": data.get("files"),
        }
    return data


def get_section(paper_id: str, section_name: str, max_chars: int = 12000) -> str:
    data = load_paper(paper_id, light=False)
    if not data:
        return ""
    sections = data.get("sections") or {}
    # fuzzy section name
    key = section_name.lower().strip()
    if key in sections:
        text = sections[key]
    else:
        match = None
        for k in sections:
            if key in k.lower() or k.lower() in key:
                match = k
                break
        if not match:
            return ""
        text = sections[match]
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n…[truncated]"
    return text


def list_figures(paper_id: str) -> list[dict]:
    data = load_paper(paper_id, light=True)
    if not data:
        return []
    return data.get("figures") or []


def get_figure_path(paper_id: str, figure_id: str) -> Path | None:
    # accept fig_001 or 1 or fig_1
    fid = figure_id.strip()
    if re.fullmatch(r"\d+", fid):
        fid = f"fig_{int(fid):03d}"
    if re.fullmatch(r"fig_?\d+", fid, re.I):
        num = int(re.sub(r"\D", "", fid))
        fid = f"fig_{num:03d}"
    path = STRUCTURED / paper_id / "figures" / f"{fid}.png"
    if path.exists():
        return path
    # search
    fig_dir = STRUCTURED / paper_id / "figures"
    if fig_dir.exists():
        for p in fig_dir.glob("*.png"):
            if fid.lower() in p.stem.lower():
                return p
    return None


def get_figure_meta(paper_id: str, figure_id: str) -> dict | None:
    path = get_figure_path(paper_id, figure_id)
    if not path:
        return None
    meta_path = path.with_suffix(".json")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"id": path.stem}
    meta["absolute_path"] = str(path)
    meta["relative_path"] = str(path.relative_to(ROOT))
    return meta


def get_figure_base64(paper_id: str, figure_id: str) -> dict | None:
    path = get_figure_path(paper_id, figure_id)
    if not path:
        return None
    meta = get_figure_meta(paper_id, figure_id) or {}
    data = path.read_bytes()
    # keep tool responses reasonable
    if len(data) > 2_500_000:
        return {
            **meta,
            "error": "Image too large to inline; use absolute_path / resource URI",
            "size_bytes": len(data),
        }
    return {
        **meta,
        "mime_type": "image/png",
        "size_bytes": len(data),
        "base64": base64.b64encode(data).decode("ascii"),
    }


def list_tables(paper_id: str) -> list[dict]:
    data = load_paper(paper_id, light=True)
    if not data:
        return []
    return data.get("tables") or []


def _candidate_table_ids(table_id: str) -> list[str]:
    tid = table_id.strip()
    cands = [tid]
    if re.fullmatch(r"\d+", tid):
        n = int(tid)
        cands += [f"table_{n:03d}", f"table_caption_{n:03d}"]
    elif re.fullmatch(r"table_?\d+", tid, re.I):
        n = int(re.sub(r"\D", "", tid))
        cands += [f"table_{n:03d}", f"table_caption_{n:03d}"]
    elif re.fullmatch(r"table_caption_?\d+", tid, re.I):
        n = int(re.sub(r"\D", "", tid))
        cands += [f"table_caption_{n:03d}"]
    # unique preserve order
    out, seen = [], set()
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def get_table(paper_id: str, table_id: str, fmt: str = "markdown") -> str:
    paper_dir = STRUCTURED / paper_id
    for tid in _candidate_table_ids(table_id):
        if fmt == "csv":
            p = paper_dir / "tables" / f"{tid}.csv"
            if p.exists():
                return p.read_text()
        if fmt in ("markdown", "md"):
            p = paper_dir / "tables" / f"{tid}.md"
            if p.exists():
                return p.read_text()
        p = paper_dir / "tables" / f"{tid}.json"
        if p.exists():
            data = json.loads(p.read_text())
            if fmt == "json":
                return json.dumps(data, indent=2)
            rows = data.get("all_rows") or ([data.get("headers", [])] + data.get("rows", []))
            if rows:
                cols = max(len(r) for r in rows)
                norm = [r + [""] * (cols - len(r)) for r in rows]
                lines = [
                    "| " + " | ".join(norm[0]) + " |",
                    "| " + " | ".join("---" for _ in norm[0]) + " |",
                ]
                for r in norm[1:]:
                    lines.append("| " + " | ".join(r) + " |")
                cap = data.get("caption_guess") or ""
                return (f"**{cap}**\n\n" if cap else "") + "\n".join(lines)
            if data.get("caption_guess"):
                return data["caption_guess"]
            return json.dumps(data, indent=2)
    # caption-only fallback from paper.json
    paper = load_paper(paper_id, light=False)
    if paper:
        cands = set(_candidate_table_ids(table_id))
        for t in paper.get("tables") or []:
            if t.get("id") in cands or any(c in (t.get("id") or "") for c in cands):
                return t.get("caption_guess") or json.dumps(t, indent=2)
    return ""


def search_components(query: str, component: str = "", limit: int = 12) -> list[dict]:
    from rich_enrich import search_components as _search

    return _search(query, component=component, limit=limit)


def search_structured(keyword: str, limit: int = 15) -> list[dict]:
    kw = keyword.lower().strip()
    out = []
    for p in load_index().get("papers") or []:
        blob = " ".join(
            [
                p.get("title") or "",
                " ".join(p.get("authors") or []),
                " ".join(p.get("keywords") or []),
                p.get("doi") or "",
            ]
        ).lower()
        # also peek abstract from card
        card_path = STRUCTURED / p["paper_id"] / "card.json"
        if card_path.exists():
            card = json.loads(card_path.read_text())
            blob += " " + (card.get("abstract") or "").lower()
        if kw in blob:
            out.append(p)
    out.sort(key=lambda x: -(x.get("year") or 0))
    return out[:limit]


def table_rows(paper_id: str, table: dict) -> list[list[str]]:
    """All rows of a stored table (paper.json keeps only headers/preview; files keep everything)."""
    tid = table.get("id")
    if not tid:
        return []
    path = STRUCTURED / paper_id / "tables" / f"{tid}.json"
    if path.exists():
        try:
            return json.loads(path.read_text()).get("all_rows") or []
        except Exception:
            pass
    return table.get("rows") or []


def record_index_text(rec: dict, max_table_chars: int = 12000, max_supplement_chars: int = 40000) -> str:
    """The full searchable text of a structured paper, shared by every index.

    Includes body sections (text layer, or OCR for scanned pages), claims, facts,
    figure captions/OCR, every table row, and OCR of raster regions on text pages.
    """
    pid = rec.get("paper_id") or ""
    title = rec.get("title") or pid
    parts = [
        f"Title: {title}",
        f"Authors: {', '.join(rec.get('authors') or [])}",
        f"DOI: {rec.get('doi') or ''}",
        f"Abstract: {rec.get('abstract') or ''}",
    ]
    for name, body in (rec.get("sections") or {}).items():
        if name != "references" and body:
            parts.append(f"## {name}\n{body}")
    parts += [f"Claim: {c}" for c in rec.get("key_claims") or []]
    parts += [f"Quantitative fact: {f}" for f in rec.get("quantitative_facts") or []]
    for fig in rec.get("figures") or []:
        blob = " ".join(fig.get(k) or "" for k in ("caption_guess", "nearby_text", "ocr_text")).strip()
        if blob:
            parts.append(f"Figure {fig.get('id')} ({fig.get('figure_type') or 'image'}) page {fig.get('page')}: {blob}")
    for tab in rec.get("tables") or []:
        rows = table_rows(pid, tab)
        body = "\n".join(" | ".join(str(c) for c in r) for r in rows)[:max_table_chars]
        head = f"Table {tab.get('id')} page {tab.get('page')}: {tab.get('caption_guess') or ''}".strip()
        if body or tab.get("caption_guess"):
            parts.append(f"{head}\n{body}")
    for img in rec.get("page_image_text") or []:
        parts.append(f"Text in images on page {img.get('page')}:\n{img.get('text')}")
    from supplements import supplement_texts

    for entry, text in supplement_texts(rec):
        # capped for the semantic index; the exact-text index keeps every row
        parts.append(f"Supplementary file {entry.get('file')}:\n{text[:max_supplement_chars]}")
    return "\n\n".join(p for p in parts if p and p.strip())
