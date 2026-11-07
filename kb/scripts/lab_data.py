#!/usr/bin/env python3
"""The lab's own measurement datasets (per-sample grain composition, e.g. NIR predictions).

Papers report other groups' numbers; these workbooks hold Shakoor Lab samples. Each workbook in
lab_data/ is parsed into a canonical dataset (one record per sample: means, replicate scans,
quality flags), reviewed for data problems, and put into the exact-text, semantic and graph indexes.

  python scripts/lab_data.py build              # parse lab_data/, write knowledge_base/lab_data/, update indexes
  python scripts/lab_data.py review             # data-quality review of every dataset
  python scripts/lab_data.py query "PI 533919"  # samples of one genotype across datasets

Source:  lab_data/<workbook>.xlsx|.csv            (never modified)
Notes:   lab_data/<workbook>.meta.json            (optional: title, description, year, QC thresholds)
Output:  knowledge_base/lab_data/<dataset_id>/dataset.json  (card, calibration, QC rule, issues, summaries)
         knowledge_base/lab_data/<dataset_id>/samples.json  (canonical samples with their scans)

Workbook layouts handled: header row anywhere in a sheet (data rows above and below it), a
means sheet plus a replicate-scan sheet joined by sample ID even when the two sheets write the ID
differently ("2025 9-84" vs "9_84"), notes sheets, and NIR calibration-statistics sheets.
"""
from __future__ import annotations

import csv
import json
import math
import re
import statistics as st
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB_DIR = ROOT / "lab_data"
OUT_DIR = ROOT / "knowledge_base" / "lab_data"
SOURCE_EXT = {".xlsx", ".xlsm", ".csv"}

# canonical trait key -> (label, unit); NIR values in these workbooks are on a dry weight basis
TRAITS = {
    "protein": ("Protein", "g/100 g grain"),
    "lysine_grain": ("Lysine", "g/100 g grain"),
    "lysine_protein": ("Lysine share of protein", "g/100 g protein"),
    "starch": ("Starch", "g/100 g grain"),
    "amylose_grain": ("Amylose", "g/100 g grain"),
    "amylose_starch": ("Amylose share of starch", "g/100 g starch"),
    "fat": ("Crude fat", "g/100 g grain"),
    "moisture": ("Moisture", "g/100 g"),
}
# ratios the lab derives from two means; they are not compared with the mean of the scans
DERIVED = {"lysine_protein", "amylose_starch"}
# a negative or text value in one of these makes the whole sample invalid
CORE = ("protein", "lysine_grain", "starch")
# Mahalanobis-distance codes used in column headers -> traits they cover
MD_CODES = {
    "mc": ["moisture"], "moisture": ["moisture"],
    "pc": ["protein"], "protein": ["protein"],
    "lys": ["lysine_grain", "lysine_protein"], "lysine": ["lysine_grain", "lysine_protein"],
    "sc": ["starch"], "starch": ["starch"],
    "aml": ["amylose_grain", "amylose_starch"], "amylose": ["amylose_grain", "amylose_starch"],
    "cf": ["fat"], "fat": ["fat"], "crude fat": ["fat"],
}
# calibration-model names -> the trait they predict
CAL_NAMES = {"moisture": "moisture", "protein": "protein", "lysine": "lysine_grain", "starch": "starch",
             "amylose": "amylose_grain", "cf": "fat", "crude fat": "fat", "fat": "fat"}
# NIR prediction quality from the average Mahalanobis distance, as stated in the notes of the
# HPI-SAP workbook ("MD < 4.0 very good; 4-7 accuracy low but may be used; > 7 not reliable")
DEFAULT_MD_RULE = {"good_below": 4.0, "unreliable_above": 7.0,
                   "source": "HPI-SAP (AR set) NIR predictions-R1.xlsx, sheet Comments"}
QC_ORDER = ("good", "usable", "unreliable", "invalid")
FIELDS = [
    ("sample", r"^(usda id#?|sid|sample id|sample|id|entry id)$"),
    ("plot", r"^plot( id)?$"),
    ("genotype", r"^(variety|genotype|line|accession|pedigree|entry name|name)$"),
    ("location", r"^(field location|location|site|environment|env)$"),
    ("rep", r"^rep(licate)?( scan)?$"),
    ("box", r"^box$"),
    ("comment", r"^comments?$|^notes?$"),
]


# ---------------------------------------------------------------------------
# cells and headers
# ---------------------------------------------------------------------------
def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return re.sub(r"\s+", " ", str(v)).strip()


def _num(v) -> tuple[float | None, str | None]:
    """(number, offending text) for a cell; text that is not a number is returned as the second item."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, None
    if isinstance(v, bool):
        return None, str(v)
    if isinstance(v, (int, float)):
        return (float(v), None) if math.isfinite(v) else (None, str(v))
    try:
        return float(str(v).replace(",", "").strip()), None
    except ValueError:
        return None, str(v).strip()


def classify(header) -> tuple[str, str | None] | None:
    """('trait', key) | ('md', code) | ('md_avg', None) | ('field', name) | None for a header cell."""
    t = re.sub(r"\s+", " ", _text(header)).lower()
    if not t:
        return None
    if re.fullmatch(r"(avg|average|mean) ?md|md ?(avg|average|mean)", t):
        return ("md_avg", None)
    m = re.fullmatch(r"md[_ ]?([a-z]+)", t)
    if m and m.group(1) in MD_CODES:
        return ("md", m.group(1))
    m = re.fullmatch(r"m-? ?distance.*?(crude fat|protein|lysine|starch|amylose|fat|moisture)", t)
    if m:
        return ("md", m.group(1))
    if re.match(r"lys(ine)?\b", t):
        return ("trait", "lysine_protein" if "protein" in t else "lysine_grain")
    if re.match(r"amylos", t):
        return ("trait", "amylose_starch" if "starch)" in t or "100g starch" in t.replace(" ", "") else "amylose_grain")
    if re.match(r"(crude )?protein\b", t):
        return ("trait", "protein")
    if re.match(r"starch\b", t):
        return ("trait", "starch")
    if re.match(r"(crude )?fat\b", t):
        return ("trait", "fat")
    if re.match(r"moisture\b", t) or t == "mc":
        return ("trait", "moisture")
    for name, pat in FIELDS:
        if re.fullmatch(pat, t):
            return ("field", name)
    return None


def sample_key(v) -> str:
    """Join key for a sample ID written differently in different sheets: '2025 9-84' == '9_84'."""
    s = _text(v)
    s = re.sub(r"^(19|20)\d{2}\s+(?=\S)", "", s)
    return re.sub(r"[\s\-]+", "_", s).upper()


def canon_genotype(v) -> str:
    s = _text(v)
    m = re.fullmatch(r"(?i)pi\s*[-_]?\s*(\d+)", s)
    return f"PI {m.group(1)}" if m else s


def genotype_match(query: str, genotype: str) -> bool:
    q, g = canon_genotype(query).lower(), (genotype or "").lower()
    if not q or not g:
        return False
    if re.fullmatch(r"pi \d+", q):
        return q == g
    return q == g or q in g


def condition(comment: str) -> dict:
    """Grain condition from free-text comments ('moldy, very glumey', 'Glumes/Mold')."""
    c = (comment or "").lower()
    out = {}
    for key, pat in (("mold", r"mou?ld"), ("glumes", r"glum")):
        m = re.search(rf"(bit |very |slightly )?\w*{pat}", c)
        if m:
            out[key] = {"bit ": "slight", "slightly ": "slight", "very ": "heavy"}.get(m.group(1) or "", "present")
    return out


# ---------------------------------------------------------------------------
# workbook reading
# ---------------------------------------------------------------------------
def _sheets(path: Path) -> list[tuple[str, list[tuple[int, list]]]]:
    """[(sheet name, [(1-based row number, raw cells)])] keeping only non-empty rows."""
    if path.suffix.lower() == ".csv":
        raw = path.read_text(errors="ignore")
        try:
            delim = csv.Sniffer().sniff(raw[:5000]).delimiter
        except csv.Error:
            delim = ","
        rows = [(i + 1, r) for i, r in enumerate(csv.reader(raw.splitlines(), delimiter=delim))]
        return [(path.stem, [(n, r) for n, r in rows if any(_text(c) for c in r)])]
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = [(i + 1, list(r)) for i, r in enumerate(ws.iter_rows(values_only=True))]
        out.append((ws.title, [(n, r) for n, r in rows if any(_text(c) for c in r)]))
    wb.close()
    return out


def _find_header(rows: list[tuple[int, list]]):
    """The row with at least three trait columns, wherever it sits in the sheet."""
    for n, r in rows:
        cols = {j: classify(c) for j, c in enumerate(r)}
        if sum(1 for v in cols.values() if v and v[0] == "trait") >= 3:
            mapping, seen = {}, set()
            for j, v in cols.items():
                if v and (v[0] != "field" or v[1] not in seen):
                    mapping[j] = v
                    if v[0] == "field":
                        seen.add(v[1])
            return n, [_text(c) for c in r], mapping
    return None


def _calibration(rows: list[tuple[int, list]]) -> list[dict] | None:
    head = next(((n, [(_text(c)).lower() for c in r]) for n, r in rows
                 if {"r2", "rmsecv"} <= {_text(c).lower() for c in r}), None)
    if head is None:
        return None
    n0, h = head
    idx = {name: h.index(name) for name in ("n", "lv", "r2", "rmsecv", "slope", "min", "max") if name in h}
    name_col = next(j for j, c in enumerate(h) if c and c not in idx)
    models = []
    for n, r in rows:
        if n <= n0:
            continue
        name = _text(r[name_col]) if name_col < len(r) else ""
        vals = {k: _num(r[j])[0] if j < len(r) else None for k, j in idx.items()}
        if not name or vals.get("r2") is None:
            continue
        models.append({"model": name, "trait": CAL_NAMES.get(name.lower()), **vals})
    return models


def _notes(rows: list[tuple[int, list]]) -> list[str]:
    return [" ".join(_text(c) for c in r if _text(c)) for _, r in rows]


# ---------------------------------------------------------------------------
# dataset assembly
# ---------------------------------------------------------------------------
def _slug(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.lower())).strip("_")[:60]


def _clean_title(stem: str) -> str:
    return re.sub(r"\s*\(\d+\)\s*$", "", stem).strip()


def _parse_rows(sheet: str, rows, header) -> list[dict]:
    hn, hcells, mapping = header
    recs = []
    for n, r in rows:
        if n == hn:
            continue
        get = {}
        for j, (kind, key) in mapping.items():
            v = r[j] if j < len(r) else None
            get.setdefault(kind, {})[key] = v
        f = get.get("field", {})
        sid = _text(f.get("sample"))
        traits = get.get("trait", {})
        if not sid or not any(_num(v)[0] is not None or _text(v) for v in traits.values()):
            continue
        # a second header row (e.g. a units or group row) has text in every trait cell
        if all(classify(v) for v in traits.values() if _text(v)):
            if any(_text(v) for v in traits.values()):
                continue
        values, bad = {}, {}
        for k, v in traits.items():
            x, txt = _num(v)
            if x is not None:
                values[k] = x
            elif txt:
                bad[k] = txt
        md = {}
        for code, v in get.get("md", {}).items():
            x, _ = _num(v)
            if x is not None:
                for t in MD_CODES[code]:
                    md[t] = x
        md_avg = _num(get.get("md_avg", {}).get(None))[0] if "md_avg" in get else None
        rep = _num(f.get("rep"))[0]
        recs.append({
            "sheet": sheet, "row": n, "sample_id": sid, "key": sample_key(sid),
            "plot": _text(f.get("plot")), "genotype": canon_genotype(f.get("genotype")),
            "location": _text(f.get("location")), "box": _text(f.get("box")),
            "rep": int(rep) if rep is not None else None, "comment": _text(f.get("comment")),
            "values": values, "bad_cells": bad, "md": md, "md_avg": md_avg,
        })
    return recs


def _stats(xs: list[float]) -> dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": round(st.mean(xs), 4), "sd": round(st.stdev(xs), 4) if len(xs) > 1 else None,
            "min": round(min(xs), 4), "max": round(max(xs), 4)}


def pearson(xs: list[float], ys: list[float]) -> dict:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 4:
        return {"n": n}
    mx, my = st.mean(p[0] for p in pairs), st.mean(p[1] for p in pairs)
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    syy = sum((y - my) ** 2 for _, y in pairs)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    if sxx == 0 or syy == 0:
        return {"n": n}
    r = sxy / math.sqrt(sxx * syy)
    z, se = math.atanh(max(min(r, 0.999999), -0.999999)), 1 / math.sqrt(n - 3)
    t = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
    # two-sided p from the normal approximation to t (n here is always large enough for that)
    p = math.erfc(abs(t) / math.sqrt(2))
    return {"n": n, "r": round(r, 3), "ci95": [round(math.tanh(z - 1.96 * se), 3), round(math.tanh(z + 1.96 * se), 3)],
            "p": float(f"{p:.2g}"), "slope": round(sxy / sxx, 4)}


def _band(md_avg: float | None, rule: dict) -> str | None:
    if md_avg is None:
        return None
    if md_avg < rule["good_below"]:
        return "good"
    return "usable" if md_avg <= rule["unreliable_above"] else "unreliable"


def _swaps(samples: dict[str, dict]) -> list[str]:
    """Detect replicate scans that were swapped between two samples (each mean fits the other's scan)."""
    def fits(mean_vals, scans):
        for k, v in mean_vals.items():
            xs = [s["values"].get(k) for s in scans]
            if k in DERIVED or None in xs or not xs:
                continue
            if abs(st.mean(xs) - v) > max(0.02 * abs(v), 0.05):
                return False
        return True

    bad = [s for s in samples.values() if s.get("reps") and not fits(s["values"], s["reps"])]
    found = []
    for i, a in enumerate(bad):
        for b in bad[i + 1:]:
            if len(a["reps"]) != len(b["reps"]):
                continue
            for k in range(len(a["reps"])):
                ra, rb = list(a["reps"]), list(b["reps"])
                ra[k], rb[k] = b["reps"][k], a["reps"][k]
                if fits(a["values"], ra) and fits(b["values"], rb):
                    found.append(f"scan {a['reps'][k].get('rep') or k + 1} of {a['sample_id']} and {b['sample_id']} appear "
                                 f"swapped in the scan sheet: each sample's mean matches its own other scans plus the "
                                 f"other sample's scan {a['reps'][k].get('rep') or k + 1}")
    return found


def parse_workbook(path: Path) -> dict:
    meta_path = path.with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    title = meta.get("title") or _clean_title(path.stem)
    ds_id = meta.get("id") or _slug(_clean_title(path.stem))
    notes, calibration, tables = [], None, []
    for name, rows in _sheets(path):
        if not rows:
            continue
        cal = _calibration(rows)
        if cal:
            calibration = cal
            legend = [ln for ln in _notes(rows) if ":" in ln and len(ln) < 160]
            notes += [f"[{name}] {ln}" for ln in legend]
            continue
        header = _find_header(rows)
        if header is None:
            notes += [f"[{name}] {ln}" for ln in _notes(rows)]
            continue
        recs = _parse_rows(name, rows, header)
        roles = {k for _, (kind, k) in header[2].items() if kind == "field"}
        dup = sum(c - 1 for c in Counter(r["key"] for r in recs).values() if c > 1)
        role = "scans" if "rep" in roles or dup >= 0.1 * max(1, len(recs)) else "means"
        tables.append({"sheet": name, "role": role, "header_row": header[0], "columns": header[1], "records": recs})

    rule = dict(meta.get("md_rule") or DEFAULT_MD_RULE)
    rule_note = meta.get("md_rule_note") or (
        "thresholds stated in this workbook's notes" if any("MD" in n and "<" in n for n in notes)
        else "thresholds taken from the HPI-SAP workbook notes; this workbook states none (confirm with the NIR lab)")
    means = [r for t in tables if t["role"] == "means" for r in t["records"]]
    scans = [r for t in tables if t["role"] == "scans" for r in t["records"]]
    by_key = defaultdict(list)
    for r in scans:
        by_key[r["key"]].append(r)

    cal_range = {m["trait"]: (m.get("min"), m.get("max")) for m in calibration or [] if m.get("trait")}
    issues: list[dict] = []

    def issue(kind, text, sample=None):
        issues.append({"kind": kind, "text": text, **({"sample": sample} if sample else {})})

    samples: dict[str, dict] = {}
    base = means if means else []
    if not means:  # only scans: means are computed here
        for key, rs in by_key.items():
            vals = {k: st.mean([r["values"][k] for r in rs if k in r["values"]]) for k in TRAITS
                    if any(k in r["values"] for r in rs)}
            md = {k: st.mean([r["md"][k] for r in rs if k in r["md"]]) for k in TRAITS if any(k in r["md"] for r in rs)}
            base.append({**rs[0], "values": vals, "md": md, "md_avg": None, "rep": None, "bad_cells": {}, "computed_mean": True})
    for m in base:
        if m["key"] in samples:
            issue("duplicate", f"sample {m['sample_id']} appears twice in the means sheet (rows {samples[m['key']]['source']['row']} and {m['row']})", m["sample_id"])
            continue
        reps = sorted(by_key.get(m["key"], []), key=lambda r: (r["rep"] or 0, r["row"]))
        comments = sorted({c for c in [m["comment"]] + [r["comment"] for r in reps] if c})
        s = {
            "sample_id": m["sample_id"], "key": m["key"], "plot": m["plot"] or next((r["plot"] for r in reps if r["plot"]), ""),
            "genotype": m["genotype"] or next((r["genotype"] for r in reps if r["genotype"]), ""),
            "location": m["location"] or next((r["location"] for r in reps if r["location"]), "") or meta.get("location", ""),
            "box": m["box"], "values": {k: round(v, 6) for k, v in m["values"].items()}, "md": m["md"],
            "comment": "; ".join(comments), "condition": condition(" ".join(comments)),
            "n_scans": len(reps), "source": {"sheet": m["sheet"], "row": m["row"]},
            "reps": [{"rep": r["rep"] or i + 1, "sample_id": r["sample_id"], "values": r["values"], "md": r["md"],
                      "comment": r["comment"], "row": r["row"], "sheet": r["sheet"]} for i, r in enumerate(reps)],
            "flags": [],
        }
        if m.get("computed_mean"):
            s["flags"].append("means computed from the scans (no means sheet)")
        md_vals = [v for k, v in s["md"].items() if k not in DERIVED]
        s["md_avg"] = m["md_avg"] if m["md_avg"] is not None else (round(st.mean(md_vals), 4) if md_vals else None)
        s["md_avg_source"] = "workbook" if m["md_avg"] is not None else "mean of trait MDs"
        if s["md_avg"] is None:
            s["flags"].append("no Mahalanobis distance reported; prediction quality unknown")
        for k, txt in m["bad_cells"].items():
            s["flags"].append(f"{k} cell holds text {txt!r} instead of a number")
            issue("text_in_number", f"{m['sample_id']}: {TRAITS[k][0]} cell holds {txt!r} (sheet {m['sheet']}, row {m['row']})", m["sample_id"])
        for k, v in s["values"].items():
            if v < 0:
                s["flags"].append(f"negative {k} ({v:.2f}): impossible value")
            lo, hi = cal_range.get(k, (None, None))
            if lo is not None and hi is not None and not (lo <= v <= hi) and v >= 0:
                s["flags"].append(f"{k} {v:.3g} outside calibration range {lo:.3g} to {hi:.3g}")
        for k, v in s["md"].items():
            if k not in DERIVED and v > rule["unreliable_above"]:
                s["flags"].append(f"MD for {k} {v:.1f} > {rule['unreliable_above']:g}")
        if not s["location"] and any(x["location"] for x in base):
            s["flags"].append("no field location")
            issue("missing_location", f"{m['sample_id']} (plot {s['plot'] or '?'}, {s['genotype'] or '?'}) has no field location", m["sample_id"])
        if not s["genotype"]:
            s["flags"].append("no genotype")
        if reps:
            for k, v in s["values"].items():
                xs = [r["values"].get(k) for r in reps]
                if k in DERIVED or None in xs:
                    continue
                if abs(st.mean(xs) - v) > max(0.02 * abs(v), 0.05):
                    s["flags"].append(f"mean {k} {v:.3g} differs from the mean of its scans ({st.mean(xs):.3g})")
        invalid = any(s["values"].get(k, 0) < 0 for k in CORE) or any(k in CORE for k in m["bad_cells"])
        s["qc"] = "invalid" if invalid else (_band(s["md_avg"], rule) or "usable")
        samples[m["key"]] = s

    if scans:
        n_scans = Counter(s["n_scans"] for s in samples.values())
        usual = n_scans.most_common(1)[0][0]
        for s in samples.values():
            if s["n_scans"] != usual:
                s["flags"].append(f"{s['n_scans']} scans (usual {usual})")
                issue("scan_count", f"{s['sample_id']} has {s['n_scans']} scans (the others have {usual})", s["sample_id"])
        orphan = sorted(set(by_key) - set(samples))
        if orphan:
            issue("orphan_scans", f"{len(orphan)} scanned sample IDs have no mean row: {', '.join(orphan[:8])}")
        id_forms = {(r["sample_id"] != samples[r["key"]]["sample_id"]) for r in scans if r["key"] in samples}
        if True in id_forms:
            ex = next(r for r in scans if r["key"] in samples and r["sample_id"] != samples[r["key"]]["sample_id"])
            issue("id_format", f"the scan sheet writes sample IDs differently from the means sheet "
                               f"(e.g. {ex['sample_id']!r} vs {samples[ex['key']]['sample_id']!r}); joined after normalising")
    for text in _swaps(samples):
        issue("swapped_scans", text)
    for s in samples.values():
        mism = [f for f in s["flags"] if f.startswith("mean ") and "differs" in f]
        if mism and not any(s["sample_id"] in i["text"] for i in issues if i["kind"] == "swapped_scans"):
            issue("mean_vs_scans", f"{s['sample_id']}: " + "; ".join(mism), s["sample_id"])
    for t in tables:
        above = sum(1 for r in t["records"] if r["row"] < t["header_row"])
        if above:
            issue("layout", f"sheet {t['sheet']!r}: header is on row {t['header_row']} with {above} data rows above it "
                            f"(sorted after the header was added?); all rows were read")
    neg = Counter(k for s in samples.values() for k, v in s["values"].items() if v < 0)
    if neg:
        issue("negative_values", "negative predictions (physically impossible): "
              + ", ".join(f"{TRAITS[k][0]} in {n} sample(s)" for k, n in neg.most_common()))
    inv = [s["sample_id"] for s in samples.values() if s["qc"] == "invalid"]
    if inv:
        issue("invalid_samples", f"{len(inv)} sample(s) unusable (negative or text core values): {', '.join(inv[:10])}")

    recs = list(samples.values())
    qc_counts = Counter(s["qc"] for s in recs)
    analysis = [s for s in recs if s["qc"] in ("good", "usable")]
    locations = Counter(s["location"] or "(not recorded)" for s in recs)
    traits_present = [k for k in TRAITS if any(k in s["values"] for s in recs)]
    summary = {
        "analysis_set": "samples with QC good or usable",
        "traits": {k: {"all_valid": _stats([s["values"].get(k) for s in recs if s["qc"] != "invalid"]),
                       "analysis_set": _stats([s["values"].get(k) for s in analysis]),
                       "good_only": _stats([s["values"].get(k) for s in recs if s["qc"] == "good"]),
                       "by_location": {loc: _stats([s["values"].get(k) for s in analysis if (s["location"] or "(not recorded)") == loc])
                                       for loc in locations}}
                   for k in traits_present},
        "correlations": {f"{x}~{y}": pearson([s["values"].get(x) for s in analysis], [s["values"].get(y) for s in analysis])
                         for x, y in [("protein", "lysine_protein"), ("protein", "lysine_grain"), ("protein", "starch"),
                                      ("starch", "amylose_starch"), ("protein", "fat")] if x in traits_present and y in traits_present},
    }
    by_geno = defaultdict(dict)
    for s in analysis:
        if s["genotype"] and s["location"]:
            by_geno[s["genotype"]].setdefault(s["location"], s)
    locs = [l for l in locations if l != "(not recorded)"]
    if len(locs) >= 2:
        summary["between_locations"] = {}
        for i, a in enumerate(locs):
            for b in locs[i + 1:]:
                both = [g for g, d in by_geno.items() if a in d and b in d]
                summary["between_locations"][f"{a} vs {b}"] = {
                    k: {**pearson([by_geno[g][a]["values"].get(k) for g in both], [by_geno[g][b]["values"].get(k) for g in both]),
                        "mean_difference": round(st.mean(by_geno[g][a]["values"][k] - by_geno[g][b]["values"][k] for g in both
                                                         if k in by_geno[g][a]["values"] and k in by_geno[g][b]["values"]), 4) if both else None}
                    for k in ("protein", "lysine_grain", "lysine_protein", "starch") if k in traits_present}
    dataset = {
        "id": ds_id, "title": title, "source_file": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "description": meta.get("description") or "", "year": meta.get("year"), "meta": meta,
        "measurement": meta.get("measurement") or "NIR (near-infrared spectroscopy) predictions of whole-grain composition",
        "basis": "dry weight basis" if any("dwb" in " ".join(t["columns"]).lower() for t in tables) else "",
        "sheets": [{"sheet": t["sheet"], "role": t["role"], "header_row": t["header_row"], "rows": len(t["records"])} for t in tables],
        "n_samples": len(recs), "n_scans": len(scans), "n_genotypes": len({s["genotype"] for s in recs if s["genotype"]}),
        "locations": dict(locations), "traits": {k: {"label": TRAITS[k][0], "unit": TRAITS[k][1]} for k in traits_present},
        "notes": notes, "calibration": calibration, "qc_rule": {**rule, "applies": rule_note},
        "qc_counts": {k: qc_counts.get(k, 0) for k in QC_ORDER},
        "conditions": dict(Counter(f"{k}:{v}" for s in recs for k, v in s["condition"].items())),
        "issues": issues, "summary": summary, "built_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    return {"dataset": dataset, "samples": recs}


# ---------------------------------------------------------------------------
# text for the indexes
# ---------------------------------------------------------------------------
def _fmt(k: str, v: float) -> str:
    return f"{v:.3f}" if k == "lysine_grain" else f"{v:.2f}"


def values_text(values: dict) -> str:
    return "; ".join(f"{TRAITS[k][0].lower()}={_fmt(k, values[k])} {TRAITS[k][1]}" for k in TRAITS if k in values)


def sample_text(ds: dict, s: dict) -> str:
    head = [f"Lab data: {ds['title']}", f"sample {s['sample_id']}"]
    for label, key in (("plot", "plot"), ("genotype", "genotype"), ("location", "location")):
        if s.get(key):
            head.append(f"{label} {s[key]}")
    flags = f" ({'; '.join(s['flags'][:4])})" if s["flags"] else ""
    md = f"avg MD {s['md_avg']:.2f}" if s.get("md_avg") is not None else "avg MD n/a"
    lines = [" | ".join(head + [f"mean of {s['n_scans'] or 1} scan(s), {ds['basis'] or 'as reported'}: {values_text(s['values'])}",
                                f"{md}, QC {s['qc']}{flags}"] + ([f"comment {s['comment']}"] if s["comment"] else []))]
    for r in s["reps"]:
        lines.append(f"sample {s['sample_id']} scan {r['rep']}: {values_text(r['values'])}"
                     + (f" | comment {r['comment']}" if r["comment"] else ""))
    return "\n".join(lines)


def card_text(ds: dict) -> str:
    """Dataset description for semantic search and for get_lab_dataset."""
    lines = [f"Lab dataset: {ds['title']} ({ds['id']})", f"Source file: {ds['source_file']}"]
    if ds["description"]:
        lines.append(ds["description"])
    lines.append(f"{ds['measurement']}, {ds['basis'] or 'basis not stated'}. {ds['n_samples']} samples "
                 f"({ds['n_genotypes']} genotypes), {ds['n_scans']} replicate scans. Locations: "
                 + ", ".join(f"{k} ({v})" for k, v in ds["locations"].items()) + ".")
    lines.append("Traits: " + ", ".join(f"{v['label']} ({v['unit']})" for v in ds["traits"].values()) + ".")
    r = ds["qc_rule"]
    lines.append(f"QC from average Mahalanobis distance (MD): good < {r['good_below']:g}, usable {r['good_below']:g} to "
                 f"{r['unreliable_above']:g}, unreliable > {r['unreliable_above']:g} ({r['applies']}); invalid = negative or "
                 f"text values in protein, lysine or starch. Counts: "
                 + ", ".join(f"{k} {v}" for k, v in ds["qc_counts"].items()) + ".")
    if ds["calibration"]:
        def cal(m):
            parts = [m["model"]] + [f"{k.upper() if k != 'rmsecv' else 'RMSECV'}={m[k]:g}" for k in ("n", "lv", "r2", "rmsecv", "slope")
                                    if m.get(k) is not None]
            if m.get("min") is not None and m.get("max") is not None:
                parts.append(f"range {m['min']:.3g} to {m['max']:.3g}")
            return " ".join(parts)
        lines.append("NIR calibration models (R2 and RMSECV from cross-validation): " + "; ".join(cal(m) for m in ds["calibration"]) + ".")
    for n in ds["notes"]:
        lines.append(f"Note {n}")
    sm = ds["summary"]
    lines.append(f"Trait summary ({sm['analysis_set']}):")
    for k, t in sm["traits"].items():
        a = t["analysis_set"]
        if a.get("n"):
            lines.append(f"  {TRAITS[k][0]}: mean {a['mean']:.3g}, SD {a['sd'] or 0:.3g}, range {a['min']:.3g} to {a['max']:.3g} {TRAITS[k][1]} (n = {a['n']})"
                         + "".join(f"; {loc} mean {b['mean']:.3g} (n = {b['n']})" for loc, b in t["by_location"].items() if b.get("n")))
    for pair, c in sm["correlations"].items():
        if c.get("r") is not None:
            x, y = pair.split("~")
            lines.append(f"  Correlation {TRAITS[x][0]} vs {TRAITS[y][0]}: r = {c['r']} (95% CI {c['ci95'][0]} to {c['ci95'][1]}, n = {c['n']}, p = {c['p']})")
    for pair, d in (sm.get("between_locations") or {}).items():
        lines.append(f"  Same genotypes, {pair}: " + "; ".join(
            f"{TRAITS[k][0]} r = {c.get('r')} (n = {c.get('n')}, mean difference {c.get('mean_difference')})" for k, c in d.items()))
    if ds["issues"]:
        lines.append("Data issues found when the workbook was parsed:")
        lines += [f"  - {i['text']}" for i in ds["issues"]]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# build + index
# ---------------------------------------------------------------------------
def build(update_indexes: bool = True) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    built = []
    sources = sorted(p for p in LAB_DIR.glob("*") if p.suffix.lower() in SOURCE_EXT and not p.name.startswith("~$"))
    import field_trials

    trials = []
    for path in sources:
        if field_trials.is_field_workbook(path):
            trials.append(field_trials.parse_workbook(path))
            continue
        res = parse_workbook(path)
        ds = res["dataset"]
        d = OUT_DIR / ds["id"]
        d.mkdir(parents=True, exist_ok=True)
        for old in ("plots.json",):
            (d / old).unlink(missing_ok=True)
        (d / "dataset.json").write_text(json.dumps(ds, indent=1, ensure_ascii=False))
        (d / "samples.json").write_text(json.dumps(res["samples"], ensure_ascii=False))
        built.append(ds)
    field_trials.cross_checks(trials)
    for res in trials:
        ds = res["dataset"]
        d = OUT_DIR / ds["id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "samples.json").unlink(missing_ok=True)
        (d / "dataset.json").write_text(json.dumps(ds, indent=1, ensure_ascii=False, default=str))
        (d / "plots.json").write_text(json.dumps(res["plots"], ensure_ascii=False, default=str))
        built.append(ds)
    field_trials._CACHE.clear()
    PUBLISHED.write_text(json.dumps(published_values(), ensure_ascii=False))
    keep = {ds["id"] for ds in built}
    for d in OUT_DIR.iterdir():
        if d.is_dir() and d.name not in keep:
            for f in d.iterdir():
                f.unlink()
            d.rmdir()
    _CACHE.clear()
    out = {"ok": True, "datasets": [{"id": ds["id"], "kind": ds.get("kind", "nir"),
                                     "records": ds.get("n_samples", ds.get("n_plots")), "qc": ds.get("qc_counts"),
                                     "issues": len(ds["issues"])} for ds in built]}
    if update_indexes:
        import text_index

        out["text_index"] = text_index.update_lab()
        out["vector_index"] = index_vectors(built)
        out["graph"] = index_graph(built)
    return out


def index_rows() -> list[tuple]:
    """Rows for the exact-text index: one passage per sample plus the dataset card."""
    rows = []
    for ds, samples in load_all():
        pid, year = f"lab:{ds['id']}", str(ds.get("year") or "")
        rows.append((card_text(ds), pid, ds["title"], "", year, "", "lab_data", "dataset card"))
        for s in samples:
            rows.append((sample_text(ds, s), pid, ds["title"], "", year, "", "lab_data", s["sample_id"]))
    import field_trials

    for ds, plots in field_trials.load_all():
        pid, year = f"lab:{ds['id']}", str(ds.get("year") or "")
        rows.append((field_trials.card_text(ds), pid, ds["title"], "", year, "", "lab_data", "dataset card"))
        for u in ds.get("unparsed_sheets") or []:
            rows.append((f"Field trial: {ds['title']} | sheet {u['sheet']}\n{u['text']}", pid, ds["title"], "", year, "", "lab_data", f"sheet {u['sheet']}"))
        for p in plots:
            rows.append((field_trials.plot_text(ds, p), pid, ds["title"], "", year, "", "lab_data", f"plot {p['plot']}"))
    return rows


def index_vectors(datasets: list[dict]) -> dict:
    try:
        import chromadb
        from chromadb.utils import embedding_functions
        from build_knowledge_base import chunk_text
    except Exception as exc:
        return {"ok": False, "error": f"chroma unavailable: {exc}"}
    import hashlib

    col = chromadb.PersistentClient(path=str(ROOT / "knowledge_base" / "chroma")).get_or_create_collection(
        name="sorghum_protein", embedding_function=embedding_functions.DefaultEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"})
    old = col.get(where={"source_type": "lab_data"})
    if old.get("ids"):
        col.delete(ids=old["ids"])
    n = 0
    import field_trials

    for ds in datasets:
        parts = chunk_text(field_trials.card_text(ds) if ds.get("kind") == "field_trial" else card_text(ds))
        ids = [hashlib.sha1(f"lab:{ds['id']}:{i}:{p[:40]}".encode()).hexdigest()[:16] for i, p in enumerate(parts)]
        col.add(ids=ids, documents=parts, metadatas=[{
            "title": f"{'Field trial' if ds.get('kind') == 'field_trial' else 'Lab dataset'}: {ds['title']}"[:300], "source_type": "lab_data", "path": f"knowledge_base/lab_data/{ds['id']}/dataset.json",
            "year": str(ds.get("year") or ""), "doi": "", "doc_id": f"lab:{ds['id']}", "chunk_index": i} for i in range(len(parts))])
        n += len(parts)
    return {"ok": True, "chunks": n}


FIELD_GRAPH_TRAITS = {"days_to_anthesis": ("TRAIT", "flowering time"), "plant_height": ("TRAIT", "plant height"),
                      "lodging_pct": ("TRAIT", "lodging"), "tillering_pct": ("TRAIT", "tillering"),
                      "tillers_per_plant": ("TRAIT", "tillering"), "panicle_tillers": ("TRAIT", "tillering"),
                      "stem_diameter_3rd": ("TRAIT", "stem diameter"), "stem_diameter_6th": ("TRAIT", "stem diameter"),
                      "biomass": ("TRAIT", "biomass"), "leaf_biomass": ("TRAIT", "biomass"),
                      "stalk_panicle_biomass": ("TRAIT", "biomass"), "weed_cover_pct": ("TRAIT", "weed pressure"),
                      "stand_count": ("TRAIT", "stand establishment")}
GRAPH_TRAITS = {"protein": ("TRAIT", "protein content"), "lysine_grain": ("TRAIT", "lysine content"),
                "lysine_protein": ("TRAIT", "lysine content"), "starch": ("TRAIT", "starch content"),
                "amylose_grain": ("TRAIT", "amylose content"), "amylose_starch": ("TRAIT", "amylose content"),
                "fat": ("TRAIT", "fat content"), "moisture": ("TRAIT", "grain moisture")}


def index_graph(datasets: list[dict]) -> dict:
    from knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph()
    n = 0
    with kg.connect() as conn:
        for row in conn.execute("SELECT paper_id FROM paper_map WHERE paper_id LIKE 'lab:%'").fetchall():
            conn.execute("DELETE FROM edges WHERE paper_id=?", (row[0],))
            conn.execute("DELETE FROM paper_map WHERE paper_id=?", (row[0],))
        for ds in datasets:
            pid = f"lab:{ds['id']}"
            node = kg._upsert_node(conn, "DATASET", f"{'Field trial' if ds.get('kind') == 'field_trial' else 'Lab dataset'}: {ds['title']}",
                                   aliases=[pid, ds["id"]],
                                   properties={"paper_id": pid, "status": "lab_data", "n_samples": ds.get("n_samples", ds.get("n_plots")),
                                               "source_file": ds["source_file"]}, identity=pid)
            conn.execute("INSERT INTO paper_map(paper_id,node_id,doi) VALUES(?,?,?) ON CONFLICT(paper_id) DO UPDATE SET "
                         "node_id=excluded.node_id", (pid, node, ""))
            if ds.get("kind") == "field_trial":
                ev = f"Field trial {ds['site']} {ds['year']}: {ds['n_plots']} plots, {ds['n_genotypes']} genotypes in {ds['source_file']}"
                targets = {FIELD_GRAPH_TRAITS[k] for k in ds["traits"] if k in FIELD_GRAPH_TRAITS} | {("METHOD", "field phenotyping"), ("ORGANISM", "Sorghum bicolor")}
            else:
                ev = f"{ds['measurement']} for {ds['n_samples']} samples ({ds['n_genotypes']} genotypes) in {ds['source_file']}"
                targets = {GRAPH_TRAITS[k] for k in ds["traits"]} | {("METHOD", "near-infrared spectroscopy"), ("ORGANISM", "Sorghum bicolor")}
            for ntype, name in sorted(targets):
                tgt = kg._upsert_node(conn, ntype, name)
                rel = "MEASURES" if ntype == "TRAIT" else "USES_METHOD" if ntype == "METHOD" else "STUDIES"
                kg._upsert_edge(conn, node, rel, tgt, pid, evidence=ev, confidence=1.0, extraction_method="lab_data")
                n += 1
    kg.prune_orphans()
    return {"ok": True, "edges": n}


# ---------------------------------------------------------------------------
# loading + queries (used by the MCP tools)
# ---------------------------------------------------------------------------
_CACHE: dict = {}


def load_all() -> list[tuple[dict, list[dict]]]:
    out = []
    if not OUT_DIR.exists():
        return out
    for d in sorted(OUT_DIR.iterdir()):
        f, g = d / "dataset.json", d / "samples.json"
        if not (f.exists() and g.exists()):
            continue
        stamp = (f.stat().st_mtime, g.stat().st_mtime)
        if _CACHE.get(d.name, (None,))[0] != stamp:
            _CACHE[d.name] = (stamp, json.loads(f.read_text()), json.loads(g.read_text()))
        out.append(_CACHE[d.name][1:])
    return out


def resolve_dataset(query: str) -> list[tuple[dict, list[dict]]]:
    allds = load_all()
    if not query:
        return allds
    q = query.lower()
    hit = [x for x in allds if q in (x[0]["id"].lower(), x[0]["title"].lower())]
    return hit or [x for x in allds if q in x[0]["id"].lower() or q in x[0]["title"].lower()]


def qc_allowed(qc: str) -> set[str]:
    q = (qc or "usable").lower()
    return {"good": {"good"}, "usable": {"good", "usable"}, "reliable": {"good", "usable"},
            "unreliable": {"unreliable"}, "invalid": {"invalid"}, "all": set(QC_ORDER)}.get(q, {"good", "usable"})


def trait_key(name: str) -> str | None:
    if not name:
        return None
    t = name.strip().lower().replace(" ", "_")
    if t in TRAITS:
        return t
    aliases = {"lysine": "lysine_grain", "lys": "lysine_grain", "lysine_share": "lysine_protein",
               "lysine_per_protein": "lysine_protein", "amylose": "amylose_grain", "amylose_share": "amylose_starch",
               "crude_fat": "fat", "oil": "fat", "crude_protein": "protein", "cp": "protein"}
    return aliases.get(t)


def find_samples(genotype="", sample="", dataset="", location="", qc="usable", trait="", min_value=None,
                 max_value=None, sort_by="", descending=True) -> list[tuple[dict, dict]]:
    allowed = qc_allowed(qc)
    tk, sk = trait_key(trait), trait_key(sort_by) or trait_key(trait)
    out = []
    for ds, samples in resolve_dataset(dataset):
        for s in samples:
            if s["qc"] not in allowed:
                continue
            if genotype and not genotype_match(genotype, s["genotype"]):
                continue
            if sample and sample_key(sample) not in (s["key"], sample_key(s["plot"])):
                continue
            if location and location.lower() not in (s["location"] or "").lower():
                continue
            if tk:
                v = s["values"].get(tk)
                if v is None or (min_value is not None and v < min_value) or (max_value is not None and v > max_value):
                    continue
            out.append((ds, s))
    if sk:
        out = [x for x in out if sk in x[1]["values"]]
        out.sort(key=lambda x: x[1]["values"][sk], reverse=descending)
    return out


def table(rows: list[tuple[dict, dict]], include_reps: bool = False, limit: int = 25) -> str:
    traits = [k for k in TRAITS if any(k in s["values"] for _, s in rows)]
    head = ["dataset", "sample", "plot", "genotype", "location"] + [f"{TRAITS[k][0]} ({TRAITS[k][1]})" for k in traits] + ["avg MD", "QC", "flags"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for ds, s in rows[:limit]:
        cells = [ds["id"], s["sample_id"], s["plot"], s["genotype"], s["location"]]
        cells += [_fmt(k, s["values"][k]) if k in s["values"] else "" for k in traits]
        cells += [f"{s['md_avg']:.2f}" if s.get("md_avg") is not None else "", s["qc"], "; ".join(s["flags"][:3])]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
        if include_reps:
            for r in s["reps"]:
                rc = ["", f"  scan {r['rep']}", "", "", ""] + [_fmt(k, r["values"][k]) if k in r["values"] else "" for k in traits] + ["", "", r["comment"]]
                lines.append("| " + " | ".join(str(c) for c in rc) + " |")
    if len(rows) > limit:
        lines.append(f"... {len(rows) - limit} more rows (raise limit or narrow the filter)")
    return "\n".join(lines)


def csv_rows(rows: list[tuple[dict, dict]], limit: int = 2000) -> str:
    """Compact CSV of samples (all traits), for plotting or analysis by the client."""
    traits = [k for k in TRAITS if any(k in s["values"] for _, s in rows)]
    out = ["dataset,sample,plot,genotype,location,qc,avg_md," + ",".join(traits)]
    for ds, s in rows[:limit]:
        cells = [ds["id"], s["sample_id"], s["plot"], s["genotype"], s["location"], s["qc"],
                 f"{s['md_avg']:.2f}" if s.get("md_avg") is not None else ""]
        cells += [f"{s['values'][k]:.4g}" if k in s["values"] else "" for k in traits]
        out.append(",".join('"' + c.replace('"', "'") + '"' if "," in c else c for c in map(str, cells)))
    if len(rows) > limit:
        out.append(f"# {len(rows) - limit} more rows not shown")
    return "\n".join(out)


def correlate(x: str, y: str, dataset: str = "", qc: str = "usable", by_location: bool = False) -> dict:
    xk, yk = trait_key(x), trait_key(y)
    if not xk or not yk:
        return {"ok": False, "error": f"unknown trait; use one of {', '.join(TRAITS)}"}
    allowed = qc_allowed(qc)
    out = {"ok": True, "x": xk, "y": yk, "qc": sorted(allowed), "results": {}}
    for ds, samples in resolve_dataset(dataset):
        keep = [s for s in samples if s["qc"] in allowed]
        groups = {"all": keep}
        if by_location:
            for s in keep:
                groups.setdefault(s["location"] or "(not recorded)", []).append(s)
        out["results"][ds["id"]] = {g: pearson([s["values"].get(xk) for s in ss], [s["values"].get(yk) for s in ss])
                                    for g, ss in groups.items()}
    return out


def compare_datasets(trait: str, dataset_a: str = "", dataset_b: str = "", qc: str = "usable", top: int = 10) -> dict:
    tk = trait_key(trait)
    if not tk:
        return {"ok": False, "error": f"unknown trait; use one of {', '.join(TRAITS)}"}
    allds = load_all()
    a = resolve_dataset(dataset_a) if dataset_a else allds[:1]
    b = resolve_dataset(dataset_b) if dataset_b else allds[1:2]
    if not a or not b:
        return {"ok": False, "error": "need two datasets; see list_lab_datasets"}
    (da, sa), (db, sb) = a[0], b[0]
    allowed = qc_allowed(qc)

    def per_geno(samples):
        g = defaultdict(list)
        for s in samples:
            if s["qc"] in allowed and s["genotype"] and tk in s["values"]:
                g[s["genotype"]].append(s["values"][tk])
        return {k: st.mean(v) for k, v in g.items()}

    ga, gb = per_geno(sa), per_geno(sb)
    both = sorted(set(ga) & set(gb))
    diffs = sorted(((gb[g] - ga[g], g) for g in both), reverse=True)
    rank = lambda d: {g: i for i, (g, _) in enumerate(sorted(d.items(), key=lambda x: -x[1]))}
    ra, rb = rank({g: ga[g] for g in both}), rank({g: gb[g] for g in both})
    return {"ok": True, "trait": tk, "unit": TRAITS[tk][1], "dataset_a": da["id"], "dataset_b": db["id"], "qc": sorted(allowed),
            "n_genotypes_a": len(ga), "n_genotypes_b": len(gb), "n_shared": len(both),
            "correlation": pearson([ga[g] for g in both], [gb[g] for g in both]),
            "rank_correlation": pearson([ra[g] for g in both], [rb[g] for g in both]),
            "mean_a": round(st.mean(ga[g] for g in both), 4) if both else None,
            "mean_b": round(st.mean(gb[g] for g in both), 4) if both else None,
            "largest_increase_b_minus_a": [(g, round(d, 3)) for d, g in diffs[:top]],
            "largest_decrease_b_minus_a": [(g, round(d, 3)) for d, g in diffs[-top:][::-1]]}



# ---------------------------------------------------------------------------
# published per-accession values in KB supplements, for checking lab values against the literature
# ---------------------------------------------------------------------------
PUBLISHED = OUT_DIR / "published_values.json"
PUB_TRAITS = [("lysine_grain", r"^lys\b|\blysine\b"), ("protein", r"(^|%\s*|crude\s+|total\s+)protein\b"),
              ("starch", r"\bstarch\b"), ("fat", r"\bfat\b|\boil\b")]


def _pub_trait(header: str) -> str | None:
    h = (header or "").lower()
    if re.search(r"digest|\bid\b|ids\b|blast|gene|domain|amylose|/\s*protein|per protein|\bsd\b|std|error|\bse\b", h):
        return None
    return next((t for t, pat in PUB_TRAITS if re.search(pat, h)), None)


def published_values() -> list[dict]:
    """Per-accession trait values in the KB's supplementary tables (accession column, or sample numbers
    joined to accessions through another sheet), e.g. NIRS protein of the SAP in Rhodes et al. 2017."""
    import supplements as sp

    out = []
    for pj in sorted((ROOT / "knowledge_base" / "structured").glob("*/paper.json")):
        rec = json.loads(pj.read_text())
        entries = [e for e in rec.get("supplements") or [] if Path(e.get("path", "")).suffix.lower() in (".xlsx", ".xlsm", ".csv")]
        tables = []
        for e in entries:
            try:
                parts = sp._parse(ROOT / e["path"])
            except Exception:
                continue
            tables += [{"where": f"{e['file']} / {p['part']}", "rows": p["rows"], "part": p, "file": e["file"]}
                       for p in parts if "rows" in p]
        if not tables:
            continue
        maps = sp._id_maps(tables)
        for t in tables:
            view = sp._table_view(t["rows"])
            if view is None:
                continue
            _, header, body = view
            cand = [j for j in range(len(header))
                    if sum(bool(sp.ACC_RE.match(r[j])) for r in body if j < len(r) and r[j]) >= max(5, 0.5 * len(body))]
            # several ID-like columns (e.g. 'SC1019' names and PI numbers): prefer the one holding PI numbers
            acc_col = max(cand, key=lambda j: sum(bool(re.match(r"(?i)pi\s*\d", r[j])) for r in body if j < len(r) and r[j])) if cand else None
            if acc_col is not None:
                accs = [canon_genotype(r[acc_col]) if acc_col < len(r) else "" for r in body]
                how = f"accession column {header[acc_col]!r}"
            else:
                links = sp._links_for(t, maps)
                if not links:
                    continue
                accs = []
                for i in range(len(body)):
                    m = re.search(r"=\s*([A-Za-z]{1,6}[\s\-_]?\d{2,}[A-Za-z]?)", links.get(i, ""))
                    accs.append(canon_genotype(m.group(1)) if m else "")
                how = "sample numbers joined to accessions through another sheet"
            for j, h in enumerate(header):
                trait = _pub_trait(h)
                if not trait:
                    continue
                vals = defaultdict(list)
                for i, r in enumerate(body):
                    x = _num(r[j])[0] if j < len(r) else None
                    if accs[i] and x is not None:
                        vals[accs[i]].append(x)
                if len(vals) >= 10:
                    out.append({"paper_id": rec["paper_id"], "title": rec.get("title"), "doi": rec.get("doi"),
                                "file": t["file"], "sheet": t["part"]["part"], "header": h, "trait": trait, "linked_by": how,
                                "values": {a: round(st.mean(v), 6) for a, v in vals.items()}})
    return out


def compare_with_literature(trait: str = "protein", dataset: str = "", qc: str = "usable") -> dict:
    tk = trait_key(trait)
    if not tk:
        return {"ok": False, "error": f"unknown trait; use one of {', '.join(TRAITS)}"}
    pubs = json.loads(PUBLISHED.read_text()) if PUBLISHED.exists() else []
    pubs = [p for p in pubs if p["trait"] == tk]
    if not pubs:
        return {"ok": False, "error": f"no published per-accession {tk} values in the KB supplements"}
    allowed, results = qc_allowed(qc), []
    for ds, samples in resolve_dataset(dataset):
        g = defaultdict(list)
        for s in samples:
            if s["qc"] in allowed and s["genotype"] and tk in s["values"]:
                g[s["genotype"]].append(s["values"][tk])
        lab = {k: st.mean(v) for k, v in g.items()}
        for p in pubs:
            both = sorted(set(lab) & set(p["values"]))
            ra = {a: i for i, a in enumerate(sorted(both, key=lambda a: lab[a]))}
            rb = {a: i for i, a in enumerate(sorted(both, key=lambda a: p["values"][a]))}
            results.append({"dataset": ds["id"], "paper": p["title"], "doi": p["doi"], "source": f"{p['file']} / {p['sheet']}",
                            "published_column": p["header"], "linked_by": p["linked_by"], "n_shared": len(both),
                            "correlation": pearson([lab[a] for a in both], [p["values"][a] for a in both]),
                            "rank_correlation": pearson([ra[a] for a in both], [rb[a] for a in both]),
                            "lab_mean": round(st.mean(lab[a] for a in both), 4) if both else None,
                            "published_mean": round(st.mean(p["values"][a] for a in both), 4) if both else None})
    return {"ok": True, "trait": tk, "unit": TRAITS[tk][1], "qc": sorted(allowed), "results": results}


def review_text(ds: dict) -> str:
    lines = [f"## {ds['title']} ({ds['id']})", f"{ds['n_samples']} samples, {ds['n_scans']} scans, {ds['n_genotypes']} genotypes; "
             f"QC " + ", ".join(f"{k} {v}" for k, v in ds["qc_counts"].items())]
    lines += [f"- [{i['kind']}] {i['text']}" for i in ds["issues"]] or ["- no issues found"]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "build":
        print(json.dumps(build(update_indexes="--no-index" not in sys.argv), indent=2))
    elif cmd == "review":
        for ds, _ in load_all():
            print(review_text(ds), "\n")
    elif cmd == "query" and len(sys.argv) > 2:
        print(table(find_samples(genotype=" ".join(sys.argv[2:]), qc="all"), include_reps=True))
    else:
        print(__doc__)
