"""Charts for chat answers, built only from data the answer can cite.

Two sources:
  table     rows of a supplementary table in the evidence. The model picks the table and
            columns; every plotted value is read from the table itself, never from the model.
  evidence  values stated in the numbered passages and claims. The model proposes points with
            the evidence number they come from; a point whose value does not occur in that
            evidence text is dropped.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from . import documents, graphdb, llm

# "graph" alone means the knowledge graph in this app; only chart words trigger a chart.
WANTS_CHART = re.compile(
    r"\b(plot|plotting|chart|histogram|scatter\s*plot|scatterplot|visuali[sz]e|visuali[sz]ation|"
    r"bar\s*graph|line\s*graph|boxplot|box\s*plot)\b", re.I)

MAX_POINTS = 1500
MAX_CATEGORIES = 30           # horizontal bars beyond this are cut to the top values
SCATTER_SERIES = 3            # all-pairs palette cap; further groups fold into "Other"
BAR_SERIES = 8

_NUM = re.compile(r"[-+−]?\d+(?:[.,]\d+)?")


def wants_chart(question: str) -> bool:
    return bool(WANTS_CHART.search(question))


def to_number(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("−", "-").replace(",", "")
    m = re.fullmatch(r"[<>~≈]?\s*([-+]?\d+(?:\.\d+)?)\s*%?", s)
    return float(m.group(1)) if m else None


# ---- supplementary tables --------------------------------------------------------------

def parse_rows(text: str) -> list[dict]:
    """Rows of a supplement text ("Key=Value; Key=Value" per line), tagged with their sheet."""
    rows, sheet = [], ""
    for line in text.splitlines():
        if line.startswith("Sheet: "):
            sheet = line[7:].strip()
            continue
        if "=" not in line or "; " not in line:
            continue
        parts = line.split("; ")
        if not all("=" in part for part in parts):
            continue  # captions and notes, not data rows
        rec = {}
        for part in parts:
            k, _, v = part.partition("=")
            if k.strip() and len(k) <= 80:
                rec[k.strip()] = v.strip()
        if len(rec) >= 2:
            if sheet:
                rec.setdefault("sheet", sheet)
            rows.append(rec)
    return rows


def table_rows(doc_id: str) -> list[dict]:
    r = graphdb.run("MATCH (d:Document {id: $id}) RETURN d.text_path AS p", id=doc_id)
    if not r or not r[0]["p"]:
        return []
    pages = documents.supplement_pages(Path(r[0]["p"]))
    return parse_rows("\n".join(pages))


def describe_table(doc_id: str, title: str, rows: list[dict]) -> dict:
    cols: dict[str, dict] = {}
    for rec in rows[:3000]:
        for k, v in rec.items():
            c = cols.setdefault(k, {"n": 0, "numeric": 0, "examples": []})
            c["n"] += 1
            if to_number(v) is not None:
                c["numeric"] += 1
            if len(c["examples"]) < 3 and v and v not in c["examples"]:
                c["examples"].append(v[:30])
    return {
        "doc_id": doc_id, "title": title, "rows": len(rows),
        "columns": [{"name": k, "numeric": c["numeric"] >= 0.6 * c["n"], "examples": c["examples"]}
                    for k, c in list(cols.items())[:40]],
    }


# ---- planning ------------------------------------------------------------------------------

PLAN_SYSTEM = """You design one chart that answers a researcher's request, using ONLY the data given.

Return JSON:
{"source": "table" | "evidence" | "none",
 "type": "bar" | "grouped_bar" | "scatter" | "line",
 "title": "short title saying what is shown", "x_label": "...", "y_label": "...", "unit": "unit of y or empty",
 "table": {"doc_id": "...", "x": "column", "y": "column", "label": "column naming each point (optional)",
           "group": "column defining series (optional)", "filter": {"column": "value"} (optional),
           "sheet": "sheet name (optional)"},
 "points": [{"series": "name", "x": "category or number", "y": number, "n": evidence number}],
 "reason": "one sentence"}

Rules:
- Prefer "table" when a supplementary table has the requested variables: then leave "points" empty.
- "evidence": every point must be a number stated in evidence item n (copy it exactly); one point per
  stated value; do not compute, average or estimate. "x" is the category (genotype, treatment,
  study) or a number for scatter/line.
- "bar" compares one measure across categories; "grouped_bar" one measure across categories for
  2-8 series (e.g. raw vs cooked); "scatter" two numeric measures; "line" a measure over an ordered
  numeric x (time, dose, days).
- Never two different units on one axis. If the request needs that, pick the main measure.
- "none" when the data cannot support a chart; say why in "reason"."""


def _evidence_text(items: list[dict], limit: int = 30) -> str:
    out = []
    for it in items[:limit]:
        extra = ""
        if it.get("kind") == "claim" and it.get("claim"):
            c = it["claim"]
            extra = f" | value: {c.get('value') or ''} | context: {c.get('context') or ''}"
        out.append(f"[{it['n']}] {it['title'][:90]} p{it['page']}: {it['text'][:700]}{extra}")
    return "\n".join(out)


def plan(question: str, items: list[dict], tables: list[dict]) -> dict:
    msg = (f"Request: {question}\n\nSupplementary tables:\n{json.dumps(tables, ensure_ascii=False)[:9000] or '(none)'}"
           f"\n\nEvidence:\n{_evidence_text(items)}")
    try:
        return llm.complete_json([{"role": "system", "content": PLAN_SYSTEM},
                                  {"role": "user", "content": msg}], purpose="chart", max_tokens=2500)
    except Exception as exc:
        return {"source": "none", "reason": f"chart planning failed: {exc}"}


# ---- building ------------------------------------------------------------------------------

def _fold_series(points: list[dict], cap: int) -> list[dict]:
    order: list[str] = []
    for p in points:
        if p["series"] not in order:
            order.append(p["series"])
    if len(order) <= cap:
        return points
    keep = set(order[:cap])
    for p in points:
        if p["series"] not in keep:
            p["series"] = "Other"
    return points


def build_from_table(spec: dict, tables: dict[str, dict]) -> tuple[dict | None, str]:
    t = spec.get("table") or {}
    doc_id = t.get("doc_id")
    if doc_id not in tables:
        return None, "the chosen table is not in the evidence"
    rows = tables[doc_id]["rows_data"]
    x, y = t.get("x"), t.get("y")
    label, group = t.get("label") or "", t.get("group") or ""
    flt = t.get("filter") or {}
    sheet = t.get("sheet") or ""
    ctype = spec.get("type") or "bar"
    pts = []
    for rec in rows:
        if sheet and rec.get("sheet") and rec.get("sheet") != sheet:
            continue
        if any(str(rec.get(k, "")).strip().lower() != str(v).strip().lower() for k, v in flt.items()):
            continue
        yv = to_number(rec.get(y))
        if yv is None:
            continue
        xv_raw = rec.get(x, "")
        xv = to_number(xv_raw) if ctype in ("scatter", "line") else (xv_raw or rec.get(label, ""))
        if xv is None or xv == "":
            continue
        pts.append({"x": xv, "y": yv, "label": rec.get(label, "") if label else "",
                    "series": rec.get(group, "") or "all" if group else "all"})
        if len(pts) >= MAX_POINTS:
            break
    if len(pts) < 2:
        return None, f"fewer than two rows have numeric '{y}'" + (f" and '{x}'" if ctype in ("scatter", "line") else "")
    return {"points": pts, "source_docs": [doc_id], "n_rows": len(rows)}, ""


def _numbers_in(text: str) -> list[float]:
    return [float(m.replace(",", "").replace("−", "-")) for m in _NUM.findall(text.replace(",", ""))]


def build_from_evidence(spec: dict, items: list[dict]) -> tuple[dict | None, str]:
    by_n = {it["n"]: it for it in items}
    pts, dropped = [], 0
    ctype = spec.get("type") or "bar"
    for p in spec.get("points") or []:
        try:
            n = int(p.get("n"))
        except (TypeError, ValueError):
            dropped += 1
            continue
        it = by_n.get(n)
        yv = to_number(p.get("y"))
        if not it or yv is None:
            dropped += 1
            continue
        text = it["text"]
        if it.get("claim"):
            text += " " + (it["claim"].get("value") or "") + " " + (it["claim"].get("context") or "")
        if not any(abs(v - yv) <= max(1e-9, abs(yv) * 0.001) for v in _numbers_in(text)):
            dropped += 1
            continue
        xv = to_number(p.get("x")) if ctype in ("scatter", "line") else str(p.get("x") or "").strip()
        if xv is None or xv == "":
            dropped += 1
            continue
        pts.append({"x": xv, "y": yv, "label": str(p.get("label") or ""), "series": str(p.get("series") or "all"),
                    "n": n})
    if len(pts) < 2:
        return None, "the evidence does not state enough comparable numbers"
    return {"points": pts, "source_docs": sorted({by_n[p["n"]]["doc_id"] for p in pts}), "dropped": dropped}, ""


def make_chart(question: str, items: list[dict]) -> dict:
    """Return {"chart": {...}} or {"none": reason}."""
    supp_docs: dict[str, str] = {}
    for it in items:
        if "__supp_" in it["doc_id"]:
            supp_docs.setdefault(it["doc_id"], it["title"])
    # Tables of the papers the evidence cites, even when no row of them was retrieved.
    papers = list(dict.fromkeys(it["doc_id"] for it in items if "__supp_" not in it["doc_id"]))[:5]
    for r in graphdb.run(
        "MATCH (s:Document)-[:SUPPLEMENT_OF]->(p:Document) WHERE p.id IN $ids "
        "RETURN s.id AS id, s.title AS title", ids=papers,
    ):
        supp_docs.setdefault(r["id"], r["title"])
    tables: dict[str, dict] = {}
    for did, title in list(supp_docs.items())[:6]:
        rows = table_rows(did)
        if rows:
            tables[did] = {**describe_table(did, title, rows), "rows_data": rows}
    spec = plan(question, items, [{k: v for k, v in t.items() if k != "rows_data"} for t in tables.values()])
    source = spec.get("source")
    if source == "table":
        data, why = build_from_table(spec, tables)
    elif source == "evidence":
        data, why = build_from_evidence(spec, items)
    else:
        return {"none": spec.get("reason") or "no chartable numbers in the evidence"}
    if not data:
        return {"none": why}
    ctype = spec.get("type") if spec.get("type") in ("bar", "grouped_bar", "scatter", "line") else "bar"
    pts = data["points"]
    pts = _fold_series(pts, SCATTER_SERIES if ctype in ("scatter", "line") else BAR_SERIES)
    if ctype == "bar":
        # One value per category; the tallest first, cut to a readable number of bars.
        pts = sorted(pts, key=lambda p: -p["y"])
        truncated = len(pts) > MAX_CATEGORIES
        pts = pts[:MAX_CATEGORIES]
    else:
        truncated = False
    by_doc = {it["doc_id"]: it for it in items}
    sources = []
    for did in data["source_docs"]:
        it = by_doc.get(did)
        if it:
            sources.append({"n": it["n"], "doc_id": did, "title": it["title"], "page": it["page"],
                            "year": it.get("year")})
        elif did in tables:
            sources.append({"n": None, "doc_id": did, "title": tables[did]["title"], "page": 1, "year": None})
    for src in sources:  # some KB titles carry escaped HTML (<i>in vitro</i>)
        src["title"] = re.sub(r"<[^>]+>", "", html.unescape(html.unescape(src["title"]))).strip()
    return {"chart": {
        "type": ctype, "title": str(spec.get("title") or "")[:140],
        "x_label": str(spec.get("x_label") or "")[:80], "y_label": str(spec.get("y_label") or "")[:80],
        "unit": str(spec.get("unit") or "")[:30], "source": source, "points": pts,
        "sources": sources, "truncated": truncated,
        "n_rows": data.get("n_rows"), "dropped": data.get("dropped", 0),
        "note": str(spec.get("reason") or "")[:240],
    }}
