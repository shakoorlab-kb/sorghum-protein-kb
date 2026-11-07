#!/usr/bin/env python3
"""Search & download open-access sorghum protein literature."""
from __future__ import annotations

import hashlib
import os
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
PAPERS = ROOT / "papers"
META = ROOT / "metadata"
PAPERS.mkdir(exist_ok=True)
META.mkdir(exist_ok=True)

EMAIL = os.environ.get("KB_CONTACT_EMAIL", "")  # used by OpenAlex, Crossref and Unpaywall
HEADERS = {"User-Agent": f"SorghumProteinKB/1.0 (mailto:{EMAIL})"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

QUERIES = [
    "sorghum protein digestibility",
    "sorghum kafirin digestibility",
    "sorghum kafirin structure",
    "sorghum protein bioavailability",
    "highly digestible sorghum mutant protein",
    "sorghum tannin protein digestibility",
    "sorghum cooking fermentation protein digestibility",
    "sorghum amino acid protein quality lysine",
    "sorghum prolamin endosperm protein body",
    "improving sorghum protein digestibility food",
]

PROTEIN_TERMS = (
    "protein",
    "kafirin",
    "digestib",
    "prolamin",
    "amino acid",
    "bioavailab",
    "nutrit",
    "endosperm",
)

catalog: dict[str, dict] = {}


def log(msg: str) -> None:
    print(msg, flush=True)


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi.strip(), flags=re.I)
    return doi.strip().rstrip(".").lower() or None


def paper_key(doi: str | None, title: str | None) -> str:
    if doi:
        return f"doi:{normalize_doi(doi)}"
    t = re.sub(r"\s+", " ", (title or "").strip().lower())
    return "title:" + hashlib.sha1(t.encode()).hexdigest()[:16]


def safe_filename(title: str, doi: str | None) -> str:
    base = re.sub(r"[^\w\s\-]+", "", (title or doi or "paper")[:80])
    base = re.sub(r"\s+", "_", base).strip("_") or "paper"
    if doi:
        base = f"{base}__{re.sub(r'[^\w.\-]+', '_', doi)[-40:]}"
    return base[:180] + ".pdf"


def reconstruct_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pairs = [(i, w) for w, idxs in inv.items() for i in idxs]
    pairs.sort()
    return " ".join(w for _, w in pairs)


def relevant(title: str, abstract: str = "") -> bool:
    blob = f"{title} {abstract}".lower()
    if "sorghum" not in blob:
        return False
    return any(t in blob for t in PROTEIN_TERMS)


def merge_record(rec: dict) -> None:
    key = paper_key(rec.get("doi"), rec.get("title"))
    if key in catalog:
        ex = catalog[key]
        for k, v in rec.items():
            if k == "oa_urls" and v:
                ex.setdefault("oa_urls", [])
                for u in v:
                    if u and u not in ex["oa_urls"]:
                        ex["oa_urls"].append(u)
            elif v and not ex.get(k):
                ex[k] = v
            elif k in ("cited_by_count", "year") and v and (ex.get(k) or 0) < v:
                ex[k] = v
        return
    catalog[key] = rec


def search_openalex(query: str, want_oa: bool | None = None, pages: int = 3, per_page: int = 50) -> int:
    added = 0
    cursor = "*"
    for _ in range(pages):
        filt = ["type:article|review"]
        if want_oa is True:
            filt.append("is_oa:true")
        params = {
            "search": query,
            "filter": ",".join(filt),
            "per-page": per_page,
            "cursor": cursor,
            "mailto": EMAIL,
        }
        try:
            r = SESSION.get("https://api.openalex.org/works", params=params, timeout=90)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log(f"  OpenAlex error: {e}")
            break
        results = data.get("results") or []
        if not results:
            break
        for w in results:
            title = w.get("title") or ""
            abstract = reconstruct_abstract(w.get("abstract_inverted_index"))
            if not relevant(title, abstract):
                continue
            doi = normalize_doi(w.get("doi"))
            oa = w.get("open_access") or {}
            urls = []
            for loc in [w.get("best_oa_location"), w.get("primary_location"), *(w.get("locations") or [])]:
                if not loc:
                    continue
                if loc.get("pdf_url"):
                    urls.append(loc["pdf_url"])
            if oa.get("oa_url"):
                urls.append(oa["oa_url"])
            # unique preserve order
            seen, uniq = set(), []
            for u in urls:
                if u and u not in seen:
                    seen.add(u)
                    uniq.append(u)
            authors = [
                (a.get("author") or {}).get("display_name")
                for a in (w.get("authorships") or [])[:15]
            ]
            authors = [a for a in authors if a]
            merge_record(
                {
                    "title": title,
                    "doi": doi,
                    "year": w.get("publication_year"),
                    "authors": authors,
                    "openalex_id": w.get("id"),
                    "cited_by_count": w.get("cited_by_count") or 0,
                    "is_oa": bool(oa.get("is_oa")),
                    "oa_urls": uniq,
                    "abstract": abstract,
                    "source": "openalex",
                    "landing_url": (w.get("primary_location") or {}).get("landing_page_url"),
                }
            )
            added += 1
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(0.15)
    return added


def search_europe_pmc(query: str, pages: int = 4, page_size: int = 100) -> int:
    added = 0
    for page in range(1, pages + 1):
        try:
            r = SESSION.get(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                params={
                    "query": query,
                    "format": "json",
                    "pageSize": page_size,
                    "resultType": "core",
                    "page": page,
                },
                timeout=90,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log(f"  EuropePMC error: {e}")
            break
        results = ((data.get("resultList") or {}).get("result")) or []
        if not results:
            break
        for w in results:
            title = w.get("title") or ""
            abstract = w.get("abstractText") or ""
            if not relevant(title, abstract):
                continue
            doi = normalize_doi(w.get("doi"))
            pmcid = w.get("pmcid")
            urls = []
            if pmcid:
                urls.append(f"https://europepmc.org/articles/{pmcid}?pdf=render")
                urls.append(f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/")
            merge_record(
                {
                    "title": title,
                    "doi": doi,
                    "year": int(w["pubYear"]) if str(w.get("pubYear", "")).isdigit() else None,
                    "authors": [w["authorString"]] if w.get("authorString") else [],
                    "pmcid": pmcid,
                    "pmid": w.get("pmid"),
                    "is_oa": w.get("isOpenAccess") == "Y",
                    "oa_urls": urls,
                    "abstract": abstract,
                    "source": "europepmc",
                    "landing_url": (
                        f"https://europepmc.org/article/MED/{w['pmid']}" if w.get("pmid") else None
                    ),
                    "cited_by_count": int(w.get("citedByCount") or 0),
                }
            )
            added += 1
        time.sleep(0.15)
    return added


def unpaywall_urls(doi: str) -> list[str]:
    try:
        r = SESSION.get(
            f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}",
            params={"email": EMAIL},
            timeout=30,
        )
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
        out, seen = [], set()
        for u in urls:
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out
    except Exception:
        return []


def looks_like_pdf(content: bytes, content_type: str | None) -> bool:
    return content[:5] == b"%PDF-" or (bool(content_type) and "pdf" in content_type.lower())


def download_one(rec: dict, seen_urls: set[str]) -> bool:
    title = rec.get("title") or "untitled"
    doi = rec.get("doi")
    dest = PAPERS / safe_filename(title, doi)
    if dest.exists() and dest.stat().st_size > 5000:
        rec["local_path"] = str(dest.relative_to(ROOT))
        rec["download_status"] = "already_present"
        return True

    urls = list(rec.get("oa_urls") or [])
    if doi:
        for u in unpaywall_urls(doi):
            if u not in urls:
                urls.append(u)
        # MDPI pattern
        if doi.startswith("10.3390/"):
            journal_article = doi.split("/", 1)[1]
            # e.g. foods15101681 -> foods/15/10/1681 is hard; try doi resolver later
            urls.append(f"https://www.mdpi.com/{doi.replace('10.3390/', '')}/pdf")

    for url in urls:
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            r = SESSION.get(
                url,
                timeout=90,
                allow_redirects=True,
                headers={**HEADERS, "Accept": "application/pdf,*/*"},
            )
            if r.status_code != 200:
                continue
            ctype = r.headers.get("Content-Type", "")
            content = r.content
            if not looks_like_pdf(content, ctype):
                # scrape pdf link from html
                text = ""
                try:
                    text = r.text[:300000]
                except Exception:
                    pass
                m = re.search(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', text, re.I)
                if not m:
                    continue
                pdf_url = urllib.parse.urljoin(url, m.group(1))
                if pdf_url in seen_urls:
                    continue
                seen_urls.add(pdf_url)
                r = SESSION.get(pdf_url, timeout=90, allow_redirects=True)
                if r.status_code != 200 or not looks_like_pdf(r.content, r.headers.get("Content-Type")):
                    continue
                content = r.content
                url = pdf_url
            if len(content) < 4000:
                continue
            dest.write_bytes(content)
            rec["local_path"] = str(dest.relative_to(ROOT))
            rec["download_status"] = "downloaded"
            rec["downloaded_from"] = url
            return True
        except Exception:
            continue
        finally:
            time.sleep(0.1)

    rec["download_status"] = "failed"
    rec["link"] = (
        f"https://doi.org/{doi}"
        if doi
        else rec.get("landing_url")
        or (urls[0] if urls else None)
    )
    return False


def main() -> None:
    log("=== Searching literature databases ===")
    for q in QUERIES:
        log(f"\nQuery: {q}")
        n_oa = search_openalex(q, want_oa=True, pages=3)
        log(f"  OpenAlex OA: +{n_oa} (catalog now {len(catalog)})")
        n_all = search_openalex(q, want_oa=None, pages=2)
        log(f"  OpenAlex all: +{n_all} (catalog now {len(catalog)})")
        n_pmc = search_europe_pmc(f'("{q}") AND OPEN_ACCESS:Y', pages=3)
        log(f"  EuropePMC OA: +{n_pmc} (catalog now {len(catalog)})")
        n_pmc2 = search_europe_pmc(f'("{q}")', pages=2)
        log(f"  EuropePMC all: +{n_pmc2} (catalog now {len(catalog)})")

    # Seed known project review
    merge_record(
        {
            "title": "Sorghum Starch and Protein Digestibility: Mechanisms, Modifications, and Health Implications",
            "doi": "10.3390/foods15101681",
            "year": 2026,
            "is_oa": True,
            "oa_urls": ["https://www.mdpi.com/2304-8158/15/10/1681/pdf"],
            "source": "seed",
            "landing_url": "https://doi.org/10.3390/foods15101681",
            "cited_by_count": 0,
            "abstract": "Review of sorghum digestibility focusing on protein-starch interactions, kafirins, processing, and health.",
        }
    )

    papers = list(catalog.values())
    papers.sort(
        key=lambda p: (
            0 if (p.get("is_oa") or p.get("oa_urls")) else 1,
            -(p.get("cited_by_count") or 0),
            -(p.get("year") or 0),
        )
    )
    (META / "paper_catalog.json").write_text(json.dumps(papers, indent=2))
    log(f"\n=== Catalog size: {len(papers)} unique papers ===")

    # Copy existing review PDF
    src = ROOT / "documents" / "Sorghum starch and protein digestibility_Review.pdf"
    if src.exists():
        dest = PAPERS / "Sorghum_starch_and_protein_digestibility_Review__10.3390_foods15101681.pdf"
        if not dest.exists():
            dest.write_bytes(src.read_bytes())
            log(f"Copied project review -> {dest.name}")

    log("\n=== Downloading PDFs (prioritizing open access) ===")
    # Try all OA first, then others up to a cap
    oa_first = [p for p in papers if p.get("is_oa") or p.get("oa_urls")]
    rest = [p for p in papers if p not in oa_first]
    to_try = oa_first[:200] + rest[:80]
    seen_urls: set[str] = set()
    ok_n = fail_n = 0
    for i, rec in enumerate(to_try, 1):
        log(f"[{i}/{len(to_try)}] {(rec.get('title') or '')[:65]}")
        if download_one(rec, seen_urls):
            ok_n += 1
            log(f"  -> OK ({rec.get('download_status')})")
        else:
            fail_n += 1
            log(f"  -> FAIL {rec.get('link')}")

    # Refresh statuses from catalog
    papers = list(catalog.values())
    failed = [p for p in papers if p.get("download_status") == "failed"]
    succeeded = [p for p in papers if p.get("download_status") in ("downloaded", "already_present")]

    (META / "downloaded.json").write_text(json.dumps(succeeded, indent=2))
    (META / "failed_downloads.json").write_text(json.dumps(failed, indent=2))

    lines = [
        "# Papers not downloaded — access links\n",
        "These papers were found in literature search but could not be downloaded as PDF",
        "(paywalled, broken OA link, or HTML-only). Use the links below.\n",
    ]
    for p in sorted(failed, key=lambda x: (-(x.get("cited_by_count") or 0), x.get("title") or "")):
        title = p.get("title") or "Untitled"
        year = p.get("year") or "?"
        link = p.get("link") or "N/A"
        cites = p.get("cited_by_count") or 0
        lines.append(f"- **{title}** ({year}, cited {cites})\n  - {link}\n")
    (META / "UNAVAILABLE_PAPERS.md").write_text("\n".join(lines))

    pdfs = list(PAPERS.glob("*.pdf"))
    log(f"\n=== DONE ===")
    log(f"PDFs in papers/: {len(pdfs)}")
    log(f"Download attempts OK: {ok_n}  FAIL: {fail_n}")
    log(f"Catalog: {META / 'paper_catalog.json'}")
    log(f"Unavailable links: {META / 'UNAVAILABLE_PAPERS.md'}")


if __name__ == "__main__":
    main()
