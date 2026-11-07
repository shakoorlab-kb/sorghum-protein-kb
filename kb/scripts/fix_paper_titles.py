#!/usr/bin/env python3
"""Fix papers whose title was stored as the DOI; refresh structured index."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
STRUCTURED = ROOT / "knowledge_base" / "structured"
META = ROOT / "metadata"
EMAIL = os.environ.get("KB_CONTACT_EMAIL", "")  # used by OpenAlex, Crossref and Unpaywall
S = requests.Session()
S.headers.update({"User-Agent": f"SorghumProteinKB/1.0 (mailto:{EMAIL})"})

DOI_AS_TITLE = re.compile(r"^10\.\d{4,9}/.+$", re.I)


def fetch_title(doi: str) -> dict:
    out = {}
    try:
        r = S.get(f"https://api.crossref.org/works/{doi}", timeout=40)
        if r.status_code == 200:
            msg = r.json().get("message") or {}
            titles = msg.get("title") or []
            if titles:
                out["title"] = titles[0]
            authors = []
            for a in msg.get("author") or []:
                name = " ".join(x for x in [a.get("given"), a.get("family")] if x)
                if name:
                    authors.append(name)
            if authors:
                out["authors"] = authors
            for key in ("published-print", "published-online", "created"):
                parts = ((msg.get(key) or {}).get("date-parts") or [[None]])[0]
                if parts and parts[0]:
                    out["year"] = int(parts[0])
                    break
            journal = " ".join(msg.get("container-title") or [])
            if journal:
                out["journal"] = journal
            return out
    except Exception:
        pass
    try:
        r = S.get(
            f"https://api.unpaywall.org/v2/{doi}",
            params={"email": EMAIL},
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("title"):
                out["title"] = data["title"]
            if data.get("year"):
                out["year"] = data["year"]
            if data.get("journal_name"):
                out["journal"] = data["journal_name"]
    except Exception:
        pass
    return out


def needs_fix(title: str | None, doi: str | None) -> bool:
    t = (title or "").strip()
    if not t:
        return True
    if DOI_AS_TITLE.match(t):
        return True
    if doi and t.lower() == doi.lower():
        return True
    if t.count("_") >= 6 and " " not in t:
        return True
    return False


def main() -> None:
    fixed = []
    for d in sorted(STRUCTURED.iterdir()):
        paper_path = d / "paper.json"
        if not paper_path.exists():
            continue
        paper = json.loads(paper_path.read_text())
        doi = (paper.get("doi") or "").lower()
        if not needs_fix(paper.get("title"), doi):
            continue
        if not doi:
            print(f"SKIP no doi: {d.name}")
            continue
        print(f"FIX {doi} ...")
        meta = fetch_title(doi)
        time.sleep(0.15)
        if not meta.get("title"):
            print(f"  FAIL no title from Crossref/Unpaywall")
            continue
        paper["title"] = meta["title"]
        if meta.get("authors") and (
            not paper.get("authors")
            or (len(paper.get("authors") or []) == 1 and DOI_AS_TITLE.match(str((paper.get("authors") or [""])[0])))
        ):
            paper["authors"] = meta["authors"]
        if meta.get("year") and not paper.get("year"):
            paper["year"] = meta["year"]
        if meta.get("journal") and not paper.get("journal"):
            paper["journal"] = meta["journal"]
        paper_path.write_text(json.dumps(paper, indent=2))
        card_path = d / "card.json"
        if card_path.exists():
            card = json.loads(card_path.read_text())
            card["title"] = paper["title"]
            if paper.get("authors"):
                card["authors"] = paper["authors"][:12]
            if paper.get("year"):
                card["year"] = paper["year"]
            if paper.get("journal"):
                card["journal"] = paper["journal"]
            card_path.write_text(json.dumps(card, indent=2))
        # update catalogs
        for cat_name in ("downloaded.json", "paper_catalog.json"):
            cat_path = META / cat_name
            if not cat_path.exists():
                continue
            items = json.loads(cat_path.read_text())
            changed = False
            for rec in items:
                if (rec.get("doi") or "").lower() == doi:
                    rec["title"] = paper["title"]
                    if paper.get("authors"):
                        rec["authors"] = paper["authors"]
                    if paper.get("year"):
                        rec["year"] = paper["year"]
                    changed = True
            if changed:
                cat_path.write_text(json.dumps(items, indent=2))
        fixed.append({"doi": doi, "title": paper["title"]})
        print(f"  -> {paper['title'][:80]}")

    # rebuild index
    from extract_paper_structure import build_index

    all_recs = []
    for d in sorted(STRUCTURED.iterdir()):
        if d.is_dir() and (d / "paper.json").exists():
            all_recs.append(json.loads((d / "paper.json").read_text()))
    build_index(all_recs)
    print(f"\nFixed {len(fixed)} titles; index now {len(all_recs)} papers")
    (STRUCTURED / "title_fixes.json").write_text(json.dumps(fixed, indent=2))


if __name__ == "__main__":
    main()
