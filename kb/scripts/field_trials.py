#!/usr/bin/env python3
"""Field-trial workbooks (per-plot agronomic traits) for the lab-data layer of the KB.

A field-trial workbook has a plot map (plot ID -> genotype, rep, maturity group) and trait sheets
keyed by plot ID or by a genotype+rep ID: anthesis, end-of-season measurements (height, lodging,
tillers), stem diameter, biomass, weed cover, stand counts, seed and panicle traits, and notes.
Each plot becomes one record with standard trait names; notes become plot flags; problems found
while parsing become dataset issues. scripts/lab_data.py calls this module from its build.

Output: knowledge_base/lab_data/<dataset_id>/dataset.json (kind 'field_trial') and plots.json
"""
from __future__ import annotations

import datetime as dt
import json
import re
import statistics as st
import time
from collections import Counter, defaultdict
from pathlib import Path

import lab_data as L

ROOT = L.ROOT
OUT_DIR = L.OUT_DIR

# standard numeric traits: key -> (label, unit, header pattern)
TRAITS = {
    "days_to_anthesis": ("Days to anthesis", "days after planting", r"(dap|days after planting)"),
    "plant_height": ("Plant height", "cm", r"(avg\.? ?height|average height( \(cm\))?)"),
    "lodging_pct": ("Lodging", "% of plants", r"% ?lodged"),
    "tillering_pct": ("Plants with tillers", "% of plants", r"% with tillers"),
    "tillers_per_plant": ("Tillers per plant", "count", r"avg\.? #? ?tillers(/ ?plant)?"),
    "panicle_tillers": ("Tillers with panicles per plant", "count", r"avg\.? (# )?tillers w/ ?panicles"),
    "tiller_height": ("Tiller height", "cm", r"avg\.? (height (of )?tillers|tiller height)"),
    "stem_diameter_3rd": ("Stem diameter, 3rd internode", "mm", r"av(g\.?|erage)( of)? 3rd internode axis 1"),
    "stem_diameter_6th": ("Stem diameter, 6th internode", "mm", r"av(g\.?|erage)( of)? 6th internode axis 1"),
    "biomass": ("Biomass, 2 plants, dried", "g", None),
    "leaf_biomass": ("Leaf biomass", "g", r"leaf biomass( \(g\))?"),
    "stalk_panicle_biomass": ("Stalk + panicle biomass", "g", r"stalk ?\+ ?panicle biomass( \(g\))?"),
    "weed_cover_pct": ("Weed cover", "%", None),
    "stand_count": ("Stand count", "plants per plot", r"# of plants in plot"),
}
TEXT = {"anthesis_date": r"date of anthesis", "maturity": r"maturity( group for planting)?",
        "lodging_type": r"(lodging type.*|root \(0\) or shoot.*)", "seed_color": r"seed colou?r",
        "panicle_openness": r"panicle open.*", "panicle_shape": r"panicle round.*", "notes": r"notes?"}
TEXT_FIX = {"comact": "Compact", "com": "Compact", "oen": "Open"}  # misspellings seen in the workbooks
LODGING_CODES = {"0": "root", "1": "shoot", "2": "root and shoot"}
MISSING = {"", "na", "n/a", "nan", "none", ".", "#value!", "#ref!", "#n/a", "#div/0!", "x"}
FLAG_WORDS = [("mixed plot or phenotype", r"mixed|more than one variety|unclear phenotype|wrong variety"),
              ("empty or no plants", r"no plants|empty|not planted"), ("weeds", r"weed"),
              ("fall armyworm", r"army ?worm"), ("missing rows", r"rows? (are )?missing|missing rows|a few rows missing"),
              ("leafy", r"\bleafy\b")]


def _norm(h) -> str:
    return re.sub(r"\s+", " ", L._text(h)).strip().lower()


def classify(header) -> tuple[str, str] | None:
    h = _norm(header)
    if not h:
        return None
    if re.fullmatch(r"plot( id)?( \(skip black rows\))?", h):
        return ("key", "plot")
    if re.search(r"pi ?\+ ?rep|geno ?\+ ?rep|unique id|id \+ rep", h):
        return ("key", "uid")
    if h in ("id", "genotype id", "genotype"):
        return ("key", "genotype")
    if h == "rep":
        return ("key", "rep")
    if h in ("groups", "group", "type"):
        return ("key", "group")
    if re.search(r"minus .*bag", h):
        return ("trait", "biomass_net")
    if re.match(r"(weight \(g\) dried|total (biomass|weight) \(g\))", h):
        return ("trait", "biomass")
    for k, (_, _, pat) in TRAITS.items():
        if pat and re.fullmatch(pat, h):
            return ("trait", k)
    for k, pat in TEXT.items():
        if re.fullmatch(pat, h):
            return ("text", k)
    return None


def _missing(v) -> bool:
    s = _norm(v)
    return s in MISSING or bool(re.fullmatch(r"[-#\s]+", s))


def _year(path: Path, meta: dict):
    if meta.get("year"):
        return int(meta["year"])
    m = re.search(r"(20\d\d)", path.stem)
    return int(m.group(1)) if m else None


def _site(path: Path, meta: dict) -> str:
    if meta.get("site"):
        return meta["site"]
    s = path.stem.lower()
    if "south carolina" in s or re.search(r"\bsc\b", s):
        return "South Carolina"
    if "hpi" in s:
        return "HPI field (Missouri)"
    return "not recorded"


def is_field_workbook(path: Path) -> bool:
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        return False
    for _, rows in L._sheets(path):
        if L._find_header(rows):
            return False  # NIR composition workbook
    for _, rows in L._sheets(path):
        for _, r in rows[:6]:
            kinds = {classify(c) for c in r}
            if ("key", "plot") in kinds and (("key", "genotype") in kinds or ("key", "uid") in kinds):
                return True
    return False


def _header(rows):
    best = None
    for n, r in rows[:6]:
        cols = {j: classify(c) for j, c in enumerate(r)}
        cols = {j: v for j, v in cols.items() if v}
        keys = {v[1] for v in cols.values() if v[0] == "key"}
        if ("plot" in keys or "uid" in keys) and (best is None or len(cols) > len(best[1])):
            best = (n, cols, [L._text(c) for c in r])
    return best


def parse_workbook(path: Path) -> dict:
    meta_path = path.with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    year, site = _year(path, meta), _site(path, meta)
    title = meta.get("title") or L._clean_title(path.stem)
    ds_id = meta.get("id") or L._slug(L._clean_title(path.stem))
    sheets = L._sheets(path)
    issues: list[dict] = []

    def issue(kind, text):
        issues.append({"kind": kind, "text": text})

    # 1. plot map
    plots: dict[str, dict] = {}
    uid_to_plot: dict[str, str] = {}
    map_sheet = None
    cands = []
    for name, rows in sheets:
        h = _header(rows)
        if h:
            keys = {v[1] for v in h[1].values() if v[0] == "key"}
            if "plot" in keys and "genotype" in keys:
                cands.append((0 if "genotype" in name.lower() else 1, len(h[1]), name, rows, h))
    for _, _, name, rows, h in sorted(cands, key=lambda c: c[:2])[:1]:
        n0, cols, _ = h
        keys = {v[1]: j for j, v in cols.items() if v[0] == "key"}
        map_sheet = name
        mat_col = next((j for j, v in cols.items() if v == ("text", "maturity")), None)
        for n, r in rows:
            if n <= n0:
                continue
            get = lambda k: r[keys[k]] if k in keys and keys[k] < len(r) else None  # noqa: E731
            p = L._text(get("plot"))
            g = L.canon_genotype(get("genotype"))
            if not p or not g or _missing(g) or classify(get("plot")) or classify(get("genotype")):
                continue
            uid = L._text(get("uid"))
            rep = L._text(get("rep"))
            m = re.search(r"(\d+)\s*$", rep) or re.search(r"_(\d+)$", uid)
            plots[p] = {"plot": p, "genotype": g, "rep": int(m.group(1)) if m else None, "uid": uid,
                        "maturity": L._text(r[mat_col]) if mat_col is not None and mat_col < len(r) else "",
                        "group": L._text(get("group")), "traits": {}, "text": {}, "notes": [], "flags": [],
                        "source_row": n}
            if uid:
                uid_to_plot[uid.upper()] = p
    if not plots:
        raise ValueError(f"{path.name}: no plot map (plot ID + genotype) found")

    # 2. trait sheets
    nonnum = defaultdict(list)
    typos = defaultdict(list)
    unparsed, notes = [], []
    biomass_src = {}
    used_sheets = []
    for name, rows in sheets:
        if name == map_sheet or not rows:
            continue
        if re.search(r"print ?out", name, re.I):
            continue
        h = _header(rows)
        if "note" in name.lower() and not h:
            # notes sheet, or a plot-notes sheet with category columns holding plot IDs
            head = rows[0][1]
            cats = [L._text(c) for c in head]
            body = rows[1:]
            plot_cells = sum(1 for _, r in body for c in r if L._text(c) in plots)
            if plot_cells >= 3:
                for _, r in body:
                    for j, c in enumerate(r):
                        p = L._text(c)
                        if p in plots and j < len(cats) and cats[j]:
                            lab = next((lb for lb, pat in FLAG_WORDS if re.search(pat, cats[j].lower())), cats[j].lower())
                            if lab not in plots[p]["flags"]:
                                plots[p]["flags"].append(lab)
                used_sheets.append(f"{name} (plot flags)")
            else:
                notes += [" ".join(L._text(c) for c in r if L._text(c)) for _, r in rows]
            continue
        if not h:
            lines = [" ".join(L._text(c) for c in r if L._text(c)) for _, r in rows]
            if len(rows) <= 30 and max(len([c for c in r if L._text(c)]) for _, r in rows) <= 2:
                notes += [f"[{name}] {ln}" for ln in lines]
            else:
                unparsed.append({"sheet": name, "text": "\n".join(lines)})
            continue
        n0, cols, hdr = h
        keys = {v[1]: j for j, v in cols.items() if v[0] == "key"}
        tcols = {j: v for j, v in cols.items() if v[0] in ("trait", "text")}
        if "weed" in name.lower():  # '% weed cover' sheet: the value column header is a date
            for j, c in enumerate(hdr):
                if j not in cols and re.search(r"\d+/\d+/\d+|cover", c.lower()):
                    tcols[j] = ("trait", "weed_cover_pct")
        if not tcols:
            unparsed.append({"sheet": name, "text": "\n".join(" | ".join(L._text(c) for c in r if L._text(c)) for _, r in rows)})
            continue
        used_sheets.append(name)
        for n, r in rows:
            if n <= n0:
                continue
            p = L._text(r[keys["plot"]]) if "plot" in keys and keys["plot"] < len(r) else ""
            if p not in plots and "uid" in keys and keys["uid"] < len(r):
                p = uid_to_plot.get(L._text(r[keys["uid"]]).upper(), p)
            if p not in plots:
                continue
            rec = plots[p]
            if rec["rep"] is None and "uid" in keys and keys["uid"] < len(r):
                m = re.search(r"_(\d+)\s*$", L._text(r[keys["uid"]]))
                if m:
                    rec["rep"] = int(m.group(1))
            for j, (kind, k) in tcols.items():
                v = r[j] if j < len(r) else None
                if v is None or _missing(v):
                    continue
                if kind == "text":
                    if k == "notes":
                        txt = L._text(v)
                        if txt and txt not in rec["notes"]:
                            rec["notes"].append(txt)
                    elif k == "anthesis_date":
                        rec["text"][k] = v.date().isoformat() if isinstance(v, (dt.datetime, dt.date)) else L._text(v)
                    elif k == "lodging_type":
                        rec["text"][k] = LODGING_CODES.get(L._text(v), L._text(v))
                    else:
                        raw = L._text(v)
                        fixed = TEXT_FIX.get(raw.lower()) or raw[:1].upper() + raw[1:].lower()
                        if raw.lower() in TEXT_FIX:
                            typos[k].append(f"{raw} -> {fixed}")
                        rec["text"].setdefault(k, fixed)
                    continue
                x, txt = L._num(v)
                if x is None:
                    if txt and k != "biomass":
                        nonnum[k].append((p, txt[:40]))
                    if txt and len(txt) > 3:
                        rec["notes"].append(f"{name}: {txt}")
                    continue
                if k in ("biomass", "biomass_net"):
                    prio = (2 if k == "biomass_net" else 1) + (1 if "biomass" in name.lower() else 0)
                    if prio >= biomass_src.get(p, (0,))[0]:
                        biomass_src[p] = (prio, name, hdr[j])
                        rec["traits"]["biomass"] = x
                    continue
                rec["traits"].setdefault(k, x)

    # 3. flags from notes, sanity checks
    bad_dates = 0
    for rec in plots.values():
        txt = " ".join(rec["notes"]).lower()
        for label, pat in FLAG_WORDS:
            if re.search(pat, txt) and label not in rec["flags"]:
                rec["flags"].append(label)
        d = rec["text"].get("anthesis_date", "")
        if year and re.match(r"\d{4}-", d) and int(d[:4]) != year:
            bad_dates += 1
        if not rec["traits"] and not rec["text"]:
            rec["flags"].append("no data")
        for k in ("lodging_pct", "tillering_pct", "weed_cover_pct"):
            v = rec["traits"].get(k)
            if v is not None and not (0 <= v <= 100):
                rec["flags"].append(f"{k} {v:g} outside 0 to 100")
    # a plot that disagrees sharply with the other reps of its genotype (a likely entry error, not a tall line)
    for k in TRAITS:
        if k in ("lodging_pct", "tillering_pct", "weed_cover_pct", "stand_count", "tillers_per_plant", "panicle_tillers"):
            continue  # percentages and small counts vary too much between reps for this check
        by_g = defaultdict(list)
        for r in plots.values():
            if k in r["traits"]:
                by_g[r["genotype"]].append(r)
        resid = []
        for rs in by_g.values():
            if len(rs) >= 3:
                med_g = st.median(x["traits"][k] for x in rs)
                resid += [(x, x["traits"][k] - med_g, med_g) for x in rs]
        if len(resid) < 30:
            continue
        mad = st.median(abs(d) for _, d, _ in resid) or 1e-9
        out = [(x, d, m) for x, d, m in resid if abs(d) > 6 * 1.4826 * mad and abs(d) > 0.25 * abs(m)]
        for x, d, m in out:
            x["flags"].append(f"check {k}: {x['traits'][k]:g} vs {m:.3g} in the other reps")
        if out:
            issue("rep_disagreement", f"{TRAITS[k][0]}: {len(out)} plot(s) far from the other reps of the same genotype, e.g. "
                  + "; ".join(f"plot {x['plot']} {x['traits'][k]:g} (reps median {m:.3g})" for x, d, m in sorted(out, key=lambda t: -abs(t[1]))[:4]))
        # two flagged plots whose values each fit the other's genotype: entries probably swapped
        def pos(plot):  # (field, position) from '12_15' or a plain plot number
            m = re.fullmatch(r"(\d+)_(\d+)", plot)
            return (int(m.group(1)), int(m.group(2))) if m else (0, int(plot)) if plot.isdigit() else None

        cand = defaultdict(list)
        for i, (a, _, ma) in enumerate(out):
            for b, _, mb in out[i + 1:]:
                pa, pb = pos(a["plot"]), pos(b["plot"])
                if not pa or not pb or pa[0] != pb[0] or abs(pa[1] - pb[1]) > 10:
                    continue
                va, vb = a["traits"][k], b["traits"][k]
                if abs(va - mb) <= 0.2 * abs(mb) and abs(vb - ma) <= 0.2 * abs(ma):
                    cand[a["plot"]].append(b)
                    cand[b["plot"]].append(a)
        swaps = []
        for a, _, _ in out:
            if len(cand[a["plot"]]) == 1:
                b = cand[a["plot"]][0]
                if len(cand[b["plot"]]) == 1 and a["plot"] < b["plot"]:
                    swaps.append((a["plot"], b["plot"]))
                    for x, y in ((a, b), (b, a)):
                        x["flags"].append(f"{k} value may belong to plot {y['plot']} (swapped entry?)")
        if swaps:
            issue("swapped_entries", f"{TRAITS[k][0]}: {len(swaps)} pair(s) of plots whose values fit each other's genotype "
                  f"(likely swapped when entered): " + ", ".join(f"{a} and {b}" for a, b in swaps[:10]))
    for k, ex in typos.items():
        issue("typos", f"{k.replace('_', ' ')}: {len(ex)} misspelt value(s) corrected (" + ", ".join(sorted(set(ex))) + ")")
    if bad_dates:
        issue("date_year", f"{bad_dates} anthesis dates carry a year other than {year} (days to anthesis was used, not the date)")
    for k, ex in nonnum.items():
        label = TRAITS[k][0] if k in TRAITS else k
        issue("text_in_number", f"{label}: {len(ex)} cell(s) hold text instead of a number, e.g. "
              + "; ".join(f"plot {p} {t!r}" for p, t in ex[:3]))
    if biomass_src:
        srcs = Counter(f"{s[1]} / {s[2][:60]}" for s in biomass_src.values())
        if len(srcs) > 1:
            issue("biomass_source", "biomass taken from more than one column: " + "; ".join(f"{k} ({v} plots)" for k, v in srcs.items()))
    flagged = Counter(f for rec in plots.values() for f in rec["flags"])
    no_data = flagged.get("no data", 0)
    if no_data:
        issue("no_data", f"{no_data} mapped plots have no trait data")
    for u in unparsed:
        issue("unparsed_sheet", f"sheet {u['sheet']!r} has no plot-keyed trait columns; its text is kept for exact search only")

    recs = list(plots.values())
    present = [k for k in TRAITS if any(k in r["traits"] for r in recs)]
    summary = {}
    for k in present:
        vals = [r["traits"][k] for r in recs if k in r["traits"]]
        by_mat = defaultdict(list)
        for r in recs:
            if k in r["traits"]:
                by_mat[r["maturity"] or "(none)"].append(r["traits"][k])
        reps = defaultdict(dict)
        for r in recs:
            if k in r["traits"] and r["rep"]:
                reps[r["genotype"]][r["rep"]] = r["traits"][k]
        pairs = [(d[1], d[2]) for d in reps.values() if 1 in d and 2 in d]
        summary[k] = {"label": TRAITS[k][0], "unit": TRAITS[k][1], **L._stats(vals),
                      "by_maturity": {m: L._stats(v) for m, v in by_mat.items()},
                      "rep1_vs_rep2": L.pearson([a for a, _ in pairs], [b for _, b in pairs])}
    texts = {k: dict(Counter(r["text"][k] for r in recs if k in r["text"]).most_common(8))
             for k in TEXT if k not in ("notes", "anthesis_date") and any(k in r["text"] for r in recs)}
    dataset = {
        "id": ds_id, "kind": "field_trial", "title": title, "site": site, "year": year,
        "source_file": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "description": meta.get("description") or "", "meta": meta,
        "n_plots": len(recs), "n_genotypes": len({r["genotype"] for r in recs}),
        "n_plots_with_data": sum(1 for r in recs if r["traits"]),
        "maturity_groups": dict(Counter(r["maturity"] or "(none)" for r in recs)),
        "groups": dict(Counter(r["group"] for r in recs if r["group"])),
        "reps": dict(Counter(str(r["rep"]) for r in recs)),
        "sheets_used": used_sheets, "notes": notes, "unparsed_sheets": unparsed,
        "traits": {k: {"label": TRAITS[k][0], "unit": TRAITS[k][1], "n": summary[k]["n"]} for k in present},
        "text_traits": texts, "flags": dict(flagged), "issues": issues, "summary": summary,
        "built_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    return {"dataset": dataset, "plots": recs}


def cross_checks(results: list[dict]) -> None:
    """Issues that need more than one trial: the same plot holding different genotypes in different years."""
    by_site = defaultdict(list)
    for r in results:
        by_site[r["dataset"]["site"]].append(r)
    for site, rs in by_site.items():
        rs = sorted(rs, key=lambda r: r["dataset"]["year"] or 0)
        for i, a in enumerate(rs):
            for b in rs[i + 1:]:
                ma = {p["plot"]: p["genotype"] for p in a["plots"]}
                mb = {p["plot"]: p["genotype"] for p in b["plots"]}
                diff = sorted((p, ma[p], mb[p]) for p in set(ma) & set(mb) if ma[p] != mb[p])
                only_b = sorted(set(mb) - set(ma))
                if diff:
                    swaps = Counter((x, y) for _, x, y in diff)
                    text = (f"{len(diff)} plot(s) hold a different genotype in {b['dataset']['year']} than in "
                            f"{a['dataset']['year']}: " + "; ".join(f"{x} -> {y} ({n} plots)" for (x, y), n in swaps.items()))
                    for r in (a, b):
                        r["dataset"]["issues"].append({"kind": "plot_map_change", "text": text})
                if only_b and len(only_b) > 10:
                    b["dataset"]["issues"].append({"kind": "extra_plots", "text": (
                        f"{len(only_b)} plots ({only_b[0]} to {only_b[-1]}) are not in the {a['dataset']['year']} trial "
                        f"(maturity: {', '.join(sorted({p['maturity'] for p in b['plots'] if p['plot'] in set(only_b)}))})")})


# ---------------------------------------------------------------------------
# text, loading, queries
# ---------------------------------------------------------------------------
def _fmt(k, v):
    return f"{v:.0f}" if k in ("days_to_anthesis", "plant_height", "stand_count", "biomass", "leaf_biomass",
                                "stalk_panicle_biomass", "tiller_height") else f"{v:.3g}"


def plot_text(ds: dict, p: dict) -> str:
    vals = "; ".join(f"{TRAITS[k][0].lower()}={_fmt(k, p['traits'][k])} {TRAITS[k][1]}" for k in TRAITS if k in p["traits"])
    txt = "; ".join(f"{k.replace('_', ' ')}={v}" for k, v in p["text"].items())
    parts = [f"Field trial: {ds['title']} ({ds['site']}, {ds['year']})", f"plot {p['plot']}", f"genotype {p['genotype']}"]
    if p["rep"]:
        parts.append(f"rep {p['rep']}")
    if p["maturity"]:
        parts.append(f"maturity {p['maturity']}")
    if p["group"]:
        parts.append(f"group {p['group']}")
    parts += [x for x in (vals, txt) if x]
    if p["flags"]:
        parts.append("flags: " + ", ".join(p["flags"]))
    if p["notes"]:
        parts.append("notes: " + " / ".join(p["notes"])[:400])
    return " | ".join(parts)


def card_text(ds: dict) -> str:
    lines = [f"Field trial: {ds['title']} ({ds['id']})", f"Site: {ds['site']}; year: {ds['year']}; source file: {ds['source_file']}"]
    if ds["description"]:
        lines.append(ds["description"])
    lines.append(f"{ds['n_plots']} plots ({ds['n_plots_with_data']} with data), {ds['n_genotypes']} genotypes; reps: "
                 + ", ".join(f"{k} {v}" for k, v in ds["reps"].items()) + "; maturity groups: "
                 + ", ".join(f"{k} {v}" for k, v in ds["maturity_groups"].items()) + ".")
    if ds["groups"]:
        lines.append("Germplasm groups: " + ", ".join(f"{k} {v}" for k, v in ds["groups"].items()) + ".")
    lines.append("Sheets used: " + ", ".join(ds["sheets_used"]) + ".")
    lines.append("Trait summary (all plots with a value; rep agreement = correlation of rep 1 and rep 2 of the same genotype):")
    for k, s in ds["summary"].items():
        if s.get("n"):
            ra = s["rep1_vs_rep2"]
            lines.append(f"  {s['label']} ({s['unit']}): mean {s['mean']:.3g}, SD {s['sd'] or 0:.3g}, range {s['min']:.3g} to "
                         f"{s['max']:.3g}, n = {s['n']}" + (f"; rep agreement r = {ra['r']} (n = {ra['n']})" if ra.get("r") is not None else ""))
    for k, v in ds["text_traits"].items():
        lines.append(f"  {k.replace('_', ' ')}: " + ", ".join(f"{a} ({b})" for a, b in v.items()))
    if ds["flags"]:
        lines.append("Plot flags: " + ", ".join(f"{k} ({v})" for k, v in sorted(ds["flags"].items(), key=lambda x: -x[1])) + ".")
    for n in ds["notes"]:
        lines.append(f"Note: {n}")
    if ds["issues"]:
        lines.append("Data issues found when the workbook was parsed:")
        lines += [f"  - {i['text']}" for i in ds["issues"]]
    return "\n".join(lines)


_CACHE: dict = {}


def load_all() -> list[tuple[dict, list[dict]]]:
    out = []
    if not OUT_DIR.exists():
        return out
    for d in sorted(OUT_DIR.iterdir()):
        f, g = d / "dataset.json", d / "plots.json"
        if not (f.exists() and g.exists()):
            continue
        stamp = (f.stat().st_mtime, g.stat().st_mtime)
        if _CACHE.get(d.name, (None,))[0] != stamp:
            _CACHE[d.name] = (stamp, json.loads(f.read_text()), json.loads(g.read_text()))
        out.append(_CACHE[d.name][1:])
    return out


def resolve(query: str = "", site: str = "", year: int | None = None) -> list[tuple[dict, list[dict]]]:
    rows = load_all()
    q = (query or "").lower()
    if q:
        rows = [x for x in rows if q in x[0]["id"].lower() or q in x[0]["title"].lower()]
    if site:
        rows = [x for x in rows if site.lower() in x[0]["site"].lower()]
    if year:
        rows = [x for x in rows if x[0]["year"] == int(year)]
    return rows


def trait_key(name: str) -> str | None:
    t = (name or "").strip().lower().replace(" ", "_")
    if t in TRAITS:
        return t
    aliases = {"flowering": "days_to_anthesis", "flowering_time": "days_to_anthesis", "anthesis": "days_to_anthesis",
               "dap": "days_to_anthesis", "height": "plant_height", "lodging": "lodging_pct", "tillers": "tillers_per_plant",
               "stem_diameter": "stem_diameter_3rd", "weed_cover": "weed_cover_pct", "stand": "stand_count"}
    return aliases.get(t)


def find_plots(genotype="", plot="", dataset="", site="", year=None, trait="", min_value=None, max_value=None,
               sort_by="", descending=True, exclude_flagged=False) -> list[tuple[dict, dict]]:
    tk, sk = trait_key(trait), trait_key(sort_by) or trait_key(trait)
    out = []
    for ds, plots in resolve(dataset, site, year):
        for p in plots:
            if genotype and not L.genotype_match(genotype, p["genotype"]):
                continue
            if plot and L._text(plot) != p["plot"]:
                continue
            if exclude_flagged and any(f for f in p["flags"] if f not in ("leafy",)):
                continue
            if tk:
                v = p["traits"].get(tk)
                if v is None or (min_value is not None and v < min_value) or (max_value is not None and v > max_value):
                    continue
            out.append((ds, p))
    if sk:
        out = [x for x in out if sk in x[1]["traits"]]
        out.sort(key=lambda x: x[1]["traits"][sk], reverse=descending)
    return out


def table(rows: list[tuple[dict, dict]], limit: int = 25, csv_out: bool = False) -> str:
    traits = [k for k in TRAITS if any(k in p["traits"] for _, p in rows)]
    if csv_out:
        out = ["dataset,site,year,plot,genotype,rep,maturity," + ",".join(traits) + ",flags"]
        for ds, p in rows[:limit]:
            cells = [ds["id"], ds["site"], str(ds["year"]), p["plot"], p["genotype"], str(p["rep"] or ""), p["maturity"]]
            cells += [f"{p['traits'][k]:.4g}" if k in p["traits"] else "" for k in traits] + ["; ".join(p["flags"])]
            out.append(",".join('"' + c.replace('"', "'") + '"' if "," in c else c for c in cells))
        return "\n".join(out)
    head = ["dataset", "site", "year", "plot", "genotype", "rep", "maturity"] + [f"{TRAITS[k][0]} ({TRAITS[k][1]})" for k in traits] + ["flags"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for ds, p in rows[:limit]:
        cells = [ds["id"], ds["site"], ds["year"], p["plot"], p["genotype"], p["rep"] or "", p["maturity"]]
        cells += [_fmt(k, p["traits"][k]) if k in p["traits"] else "" for k in traits] + ["; ".join(p["flags"])]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    if len(rows) > limit:
        lines.append(f"... {len(rows) - limit} more rows")
    return "\n".join(lines)


def genotype_means(plots: list[dict], trait: str, exclude_flagged: bool = True) -> dict[str, float]:
    g = defaultdict(list)
    for p in plots:
        if trait in p["traits"] and not (exclude_flagged and any(f in p["flags"] for f in ("mixed plot or phenotype", "empty or no plants"))):
            g[p["genotype"]].append(p["traits"][trait])
    return {k: st.mean(v) for k, v in g.items()}


def nir_field_join(nir_trait: str, field_trait: str, nir_dataset: str = "", field_dataset: str = "", qc: str = "usable") -> dict:
    """Correlate an NIR grain trait with a field trait. Plot level when the NIR dataset names its field trial
    (meta 'field_trial'), otherwise genotype means (NIR samples at that site vs the field trials of that site)."""
    nk, fk = L.trait_key(nir_trait), trait_key(field_trait)
    if not nk or not fk:
        return {"ok": False, "error": f"unknown trait; NIR traits: {', '.join(L.TRAITS)}; field traits: {', '.join(TRAITS)}"}
    allowed, out = L.qc_allowed(qc), []
    fields = load_all()
    for ds, samples in L.resolve_dataset(nir_dataset):
        ok = [s for s in samples if s["qc"] in allowed and nk in s["values"]]
        link = (ds.get("meta") or {}).get("field_trial")
        targets = [f for f in fields if (not field_dataset or field_dataset.lower() in f[0]["id"]) and (not link or f[0]["id"] == link or field_dataset)]
        for fds, plots in targets:
            if link and fds["id"] == link:
                by_plot = {p["plot"]: p for p in plots}
                pairs = [(s["values"][nk], by_plot[s["plot"] or s["sample_id"]]["traits"][fk]) for s in ok
                         if (s["plot"] or s["sample_id"]) in by_plot and fk in by_plot[s["plot"] or s["sample_id"]]["traits"]]
                level = "same plot, same season"
            else:
                site_ok = lambda s: bool(s["location"]) and s["location"].lower().split()[0] in fds["site"].lower()  # noqa: E731
                nir_g = defaultdict(list)
                for s in ok:
                    if s["genotype"] and site_ok(s):
                        nir_g[s["genotype"]].append(s["values"][nk])
                fg = genotype_means(plots, fk)
                pairs = [(st.mean(v), fg[g]) for g, v in nir_g.items() if g in fg]
                level = "genotype means (NIR season not linked to this trial)"
            c = L.pearson([a for a, _ in pairs], [b for _, b in pairs])
            if c["n"] >= 10:
                out.append({"nir_dataset": ds["id"], "field_dataset": fds["id"], "site": fds["site"], "year": fds["year"],
                            "level": level, **c})
    return {"ok": True, "nir_trait": nk, "field_trait": fk, "qc": sorted(allowed), "results": out}


def _usable(p: dict) -> bool:
    return not any(f in p["flags"] for f in ("mixed plot or phenotype", "empty or no plants"))


def correlate(x: str, y: str, dataset: str = "", site: str = "", year=None) -> dict:
    xk, yk = trait_key(x), trait_key(y)
    if not xk or not yk:
        return {"ok": False, "error": f"unknown field trait; use one of {', '.join(TRAITS)}"}
    res = {}
    for ds, plots in resolve(dataset, site, year):
        ok = [p for p in plots if _usable(p)]
        c = pearson_plots(ok, xk, yk)
        if c["n"] >= 10:
            res[ds["id"]] = c
    return {"ok": True, "x": xk, "y": yk, "level": "plots (mixed and empty plots excluded)", "results": res}


def pearson_plots(plots, xk, yk):
    return L.pearson([p["traits"].get(xk) for p in plots], [p["traits"].get(yk) for p in plots])


def compare_trials(trait: str, dataset_a: str, dataset_b: str, top: int = 10) -> dict:
    tk = trait_key(trait)
    if not tk:
        return {"ok": False, "error": f"unknown field trait; use one of {', '.join(TRAITS)}"}
    a, b = resolve(dataset_a), resolve(dataset_b)
    if not a or not b:
        return {"ok": False, "error": "need two field trials; see list_lab_datasets"}
    (da, pa), (db, pb) = a[0], b[0]
    ga, gb = genotype_means(pa, tk), genotype_means(pb, tk)
    both = sorted(set(ga) & set(gb))
    diffs = sorted(((gb[g] - ga[g], g) for g in both), reverse=True)
    return {"ok": True, "trait": tk, "unit": TRAITS[tk][1], "dataset_a": da["id"], "dataset_b": db["id"], "n_shared": len(both),
            "correlation": L.pearson([ga[g] for g in both], [gb[g] for g in both]),
            "mean_a": round(st.mean(ga[g] for g in both), 3) if both else None,
            "mean_b": round(st.mean(gb[g] for g in both), 3) if both else None,
            "largest_increase_b_minus_a": [(g, round(d, 2)) for d, g in diffs[:top]],
            "largest_decrease_b_minus_a": [(g, round(d, 2)) for d, g in diffs[-top:][::-1]]}
