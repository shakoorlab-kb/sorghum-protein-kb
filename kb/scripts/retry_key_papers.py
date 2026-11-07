#!/usr/bin/env python3
"""Retry high-value sorghum protein PDFs via Unpaywall, PMC, MDPI, EuropePMC."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
PAPERS = ROOT / "papers"
META = ROOT / "metadata"
EMAIL = os.environ.get("KB_CONTACT_EMAIL", "")  # used by OpenAlex, Crossref and Unpaywall
HEADERS = {"User-Agent": f"SorghumProteinKB/1.0 (mailto:{EMAIL})", "Accept": "application/pdf,*/*"}
S = requests.Session()
S.headers.update(HEADERS)

# Canonical high-value sorghum protein papers (even if not in first catalog)
SEED_DOIS = [
    "10.1104/pp.18.00200",  # CRISPR alpha-kafirin
    "10.1073/pnas.81.1.1",  # pepsin digestibility cereals
    "10.1073/pnas.78.3.1333",  # digestibility of sorghum proteins
    "10.1073/pnas.1201700109",  # Tannin1
    "10.1104/pp.110.154690",  # RNAi protein body morphology
    "10.3390/nu12041111",  # African fermented sorghum foods
    "10.3390/nu2010016",
    "10.3390/foods12102046",
    "10.3390/molecules24183321",
    "10.3390/nu12041020",
    "10.3390/healthcare3020172",
    "10.1016/s0733-5210(03)00016-x",  # Duodu factors affecting sorghum protein digestibility
    "10.1094/cchem.2000.77.2.232",  # Oria highly digestible mutant
    "10.1094/CCHEM.1998.75.5.525",
    "10.1094/CCHEM.2001.78.4.412",
    "10.1016/j.jcs.2005.01.004",
    "10.1016/j.jcs.2013.09.001",
    "10.1021/jf00046a019",
    "10.1021/jf00056a006",
    "10.1111/j.1365-2621.1987.tb14071.x",
    "10.1002/jsfa.2740650217",
    "10.1016/j.foodchem.2013.07.059",
    "10.1016/j.foodchem.2006.12.056",
    "10.3389/fpls.2018.00331",
    "10.3389/fpls.2019.00621",
    "10.3389/fnut.2022.882020",
    "10.3390/foods10081831",
    "10.3390/foods10020310",
    "10.3390/foods9050569",
    "10.3390/ijms21176223",
    "10.3390/nu11081706",
    "10.3390/nu11071521",
    "10.3390/nu11010176",
    "10.1186/s12870-017-1064-9",
    "10.1186/s12864-020-6538-8",
    "10.1155/2018/3964392",
    "10.1155/2020/8243617",
    "10.1038/s41598-019-45190-3",
    "10.1038/s41598-020-59155-4",
    "10.1371/journal.pone.0189106",
    "10.1371/journal.pone.0197874",
    "10.1371/journal.pone.0082120",
    "10.1371/journal.pone.0113212",
    "10.1104/pp.104.057307",
    "10.1104/pp.15.01173",
    "10.1111/pbi.12833",
    "10.1111/nph.12434",
    "10.1016/j.jafr.2022.100300",
    "10.1080/10408398.2021.1960793",
    "10.1002/fsn3.2",
    "10.1016/j.foodres.2020.108996",
]


def safe_name(title: str, doi: str) -> str:
    base = re.sub(r"[^\w\s\-]+", "", (title or doi)[:80])
    base = re.sub(r"\s+", "_", base).strip("_") or "paper"
    return f"{base}__{re.sub(r'[^\w.\\-]+', '_', doi)[-40:]}"[:180] + ".pdf"


def looks_pdf(content: bytes, ctype: str | None) -> bool:
    return content[:5] == b"%PDF-" or (bool(ctype) and "pdf" in ctype.lower() and content[:5] == b"%PDF-")


def unpaywall(doi: str) -> list[str]:
    try:
        r = S.get(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}", params={"email": EMAIL}, timeout=30)
        if r.status_code != 200:
            return []
        data = r.json()
        urls = []
        for loc in [data.get("best_oa_location"), *(data.get("oa_locations") or [])]:
            if not loc:
                continue
            if loc.get("url_for_pdf"):
                urls.append(loc["url_for_pdf"])
            if loc.get("url"):
                urls.append(loc["url"])
        return list(dict.fromkeys(urls))
    except Exception:
        return []


def europepmc(doi: str) -> tuple[list[str], dict]:
    urls, meta = [], {}
    try:
        r = S.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": f"DOI:{doi}", "format": "json", "pageSize": 5, "resultType": "core"},
            timeout=30,
        )
        r.raise_for_status()
        hits = ((r.json().get("resultList") or {}).get("result")) or []
        if not hits:
            return urls, meta
        w = hits[0]
        meta = {
            "title": w.get("title"),
            "year": int(w["pubYear"]) if str(w.get("pubYear", "")).isdigit() else None,
            "pmcid": w.get("pmcid"),
            "pmid": w.get("pmid"),
            "abstract": w.get("abstractText"),
            "authors": [w.get("authorString")] if w.get("authorString") else [],
        }
        pmcid = w.get("pmcid")
        if pmcid:
            urls += [
                f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/",
                f"https://europepmc.org/articles/{pmcid}?pdf=render",
            ]
    except Exception:
        pass
    return urls, meta


def mdpi_pdf(doi: str) -> list[str]:
    if not doi.startswith("10.3390/"):
        return []
    # Resolve landing page then look for pdf
    urls = [f"https://www.mdpi.com/doi/pdf/{doi}", f"https://doi.org/{doi}"]
    return urls


def download(url: str) -> bytes | None:
    try:
        r = S.get(url, timeout=90, allow_redirects=True, headers=HEADERS)
        if r.status_code != 200:
            return None
        ctype = r.headers.get("Content-Type", "")
        if r.content[:5] == b"%PDF-":
            return r.content
        # html -> find pdf
        text = r.text[:400000] if "html" in ctype.lower() or r.content[:20].lstrip().startswith(b"<") else ""
        if not text:
            return None
        # PMC citation_pdf_url
        m = re.search(r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']', text, re.I)
        if not m:
            m = re.search(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', text, re.I)
        if not m:
            return None
        pdf_url = urllib.parse.urljoin(url, m.group(1))
        r2 = S.get(pdf_url, timeout=90, allow_redirects=True, headers=HEADERS)
        if r2.status_code == 200 and r2.content[:5] == b"%PDF-":
            return r2.content
    except Exception:
        return None
    return None


def already_has(doi: str) -> bool:
    tag = re.sub(r"[^\w.\-]+", "_", doi)[-40:]
    for p in PAPERS.glob("*.pdf"):
        if tag in p.name or doi.replace("/", "_") in p.name:
            return True
    return False


def main() -> None:
    fail = json.loads((META / "failed_downloads.json").read_text())
    catalog = json.loads((META / "paper_catalog.json").read_text())
    by_doi = { (p.get("doi") or "").lower(): p for p in catalog if p.get("doi") }

    # Priority: seed DOIs + OA failed with sorghum+protein-ish titles
    wanted = list(dict.fromkeys([d.lower() for d in SEED_DOIS] + [
        p["doi"].lower() for p in fail
        if p.get("doi") and (p.get("is_oa") or p.get("oa_urls") or (p.get("cited_by_count") or 0) >= 80)
    ]))

    new_ok = []
    still_fail = []
    print(f"Retrying {len(wanted)} DOIs", flush=True)

    for i, doi in enumerate(wanted, 1):
        if already_has(doi):
            print(f"[{i}/{len(wanted)}] already have {doi}", flush=True)
            continue
        rec = by_doi.get(doi, {"doi": doi, "title": doi})
        title = rec.get("title") or doi
        print(f"[{i}/{len(wanted)}] {title[:70]}", flush=True)
        urls = []
        urls += unpaywall(doi)
        eurls, emeta = europepmc(doi)
        urls += eurls
        if emeta.get("title") and not rec.get("title"):
            rec.update({k: v for k, v in emeta.items() if v})
        urls += mdpi_pdf(doi)
        urls.append(f"https://doi.org/{doi}")
        # unique
        urls = list(dict.fromkeys([u for u in urls if u]))
        got = None
        src = None
        for u in urls:
            got = download(u)
            if got and len(got) > 4000:
                src = u
                break
            time.sleep(0.08)
        if got:
            dest = PAPERS / safe_name(rec.get("title") or doi, doi)
            dest.write_bytes(got)
            rec["local_path"] = str(dest.relative_to(ROOT))
            rec["download_status"] = "downloaded"
            rec["downloaded_from"] = src
            rec["doi"] = doi
            new_ok.append(rec)
            print(f"  OK ({len(got)} bytes) from {src}", flush=True)
        else:
            rec["download_status"] = "failed"
            rec["link"] = f"https://doi.org/{doi}"
            rec["doi"] = doi
            still_fail.append(rec)
            print("  FAIL", flush=True)
        time.sleep(0.1)

    # Merge into downloaded/failed
    old_ok = json.loads((META / "downloaded.json").read_text())
    have = { (p.get("doi") or "").lower() for p in old_ok if p.get("doi") }
    for p in new_ok:
        if (p.get("doi") or "").lower() not in have:
            old_ok.append(p)
    (META / "downloaded.json").write_text(json.dumps(old_ok, indent=2))

    # Rebuild unavailable list from remaining fails + still_fail
    remaining = []
    still_dois = {p["doi"].lower() for p in still_fail if p.get("doi")}
    recovered = {(p.get("doi") or "").lower() for p in new_ok}
    for p in fail:
        d = (p.get("doi") or "").lower()
        if d in recovered or already_has(d):
            continue
        remaining.append(p)
    # add still_fail not already in remaining
    rem_dois = {(p.get("doi") or "").lower() for p in remaining}
    for p in still_fail:
        if p["doi"].lower() not in rem_dois:
            remaining.append(p)

    (META / "failed_downloads.json").write_text(json.dumps(remaining, indent=2))
    lines = [
        "# Papers not downloaded — access links\n",
        "Found in literature search but PDF could not be saved (paywall, broken OA link, or HTML-only).\n",
        "Sorted by citation count when available.\n",
    ]
    remaining.sort(key=lambda x: (-(x.get("cited_by_count") or 0), x.get("title") or ""))
    for p in remaining:
        title = p.get("title") or "Untitled"
        year = p.get("year") or "?"
        link = p.get("link") or (f"https://doi.org/{p['doi']}" if p.get("doi") else "N/A")
        cites = p.get("cited_by_count") or 0
        lines.append(f"- **{title}** ({year}, cited {cites})\n  - {link}\n")
    (META / "UNAVAILABLE_PAPERS.md").write_text("\n".join(lines))
    print(f"\nNewly downloaded: {len(new_ok)}")
    print(f"Still unavailable: {len(remaining)}")
    print(f"PDF count now: {len(list(PAPERS.glob('*.pdf')))}")


if __name__ == "__main__":
    main()
