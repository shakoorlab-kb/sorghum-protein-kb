"""LLM graph extraction, one page at a time, with every claim checked against its page.

The model reads one page and returns entities and claims; each claim must carry a
verbatim quote. The quote is then searched for in the page text: found = verified
(the viewer can highlight it on the PDF), not found = kept but marked unverified with
lowered confidence. Entity names are canonicalised through the KB vocabulary so
"γ-kafirin", "gamma kafirin" and "gamma-kafirins" become one node.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from . import llm
from .config import settings
from .documents import _norm, quote_in_text
from .graphdb import ENTITY_TYPES, RELATION_TYPES, entity_id, norm_key

MAX_PAGE_CHARS = 7000

SYSTEM = f"""You build a scientific knowledge graph about sorghum grain protein quality,
kafirins, digestibility, grain composition, processing, genetics and breeding.

From ONE page of a document, extract entities and claims as JSON:
{{
  "entities": [{{"name": str, "type": one of {ENTITY_TYPES}, "aliases": [str], "description": str}}],
  "claims": [{{
     "subject": str, "subject_type": str, "relation": one of {RELATION_TYPES},
     "object": str, "object_type": str,
     "statement": "one plain sentence stating the claim",
     "context": "conditions/qualifiers: assay, cooking state, population, magnitude (may be empty)",
     "evidence": "a VERBATIM quote copied character-for-character from the page (max ~300 chars)",
     "confidence": 0.0-1.0
  }}]
}}

Types:
GENE genes, loci, transcription factors, gene IDs (e.g. Sobic.005G189000, Opaque2, k1C)
PROTEIN proteins and protein fractions, enzymes (kafirin, gamma-kafirin, pepsin)
TRAIT measurable traits (protein digestibility, protein content, lysine content, grain hardness)
PROCESS food/grain processing or biological processes (wet cooking, fermentation, malting, CRISPR editing)
COMPOUND chemicals and nutrients (tannin, phytic acid, starch, lysine, disulfide bond)
STRUCTURE grain/cell structures (protein body, endosperm, pericarp, testa)
PHENOTYPE named phenotypes/mutant classes (highly digestible phenotype, floury endosperm)
GERMPLASM accessions, cultivars, lines, mutants, populations, panels (P721Q, BTx623, SAP, PI 533919)
ORGANISM species (Sorghum bicolor, maize)
METHOD assays, analytical or genetic methods (pepsin digestion assay, SDS-PAGE, GWAS, NIR)
MARKER QTL, SNPs, markers, chromosomal regions
LOCATION countries/regions/environments when they matter to a finding
FOOD food products (porridge, injera, ugali, flour, biscuits)

Rules:
- Claims are findings or established facts the page states, not plans or questions. Prefer
  results with direction (INCREASES/DECREASES/IMPROVES/IMPAIRS/NO_EFFECT_ON) when the text gives one.
- Subject and object of every claim must appear in "entities".
- Names: short canonical scientific names (at most 5 words, a noun phrase, never a clause), singular, lower case except proper names and gene
  symbols ("protein digestibility", not "the IVPD of cooked flour"). Put qualifiers in "context".
- "evidence" must be copied exactly from the page text, never paraphrased.
- Do not extract from reference lists, author affiliations, acknowledgements or journal boilerplate;
  for such a page return {{"entities": [], "claims": []}}.
- Do not make claims whose only content is that something is in or from sorghum.
- Use the most standard name for each concept and the same name every time it recurs.
- Keep "description" under 15 words and "context" under 25 words.
- At most 25 entities and 15 claims; quality over quantity. Return only JSON."""


@lru_cache(maxsize=1)
def _vocab() -> dict[str, tuple[str, str]]:
    """normalised alias -> (canonical name, type)"""
    here = Path(__file__).parent
    out: dict[str, tuple[str, str]] = {}
    # canonical_map.json (cross-document entity resolution) overrides the seed vocabulary.
    for fname in ("seed_vocabulary.json", "canonical_map.json"):
        f = here / fname
        if not f.exists():
            continue
        for etype, entries in json.loads(f.read_text()).items():
            for canonical, aliases in entries.items():
                for a in [canonical, *aliases]:
                    out[norm_key(a)] = (canonical, etype)
    return out


_REL_SYNONYMS = {
    "REDUCES": "DECREASES", "LOWERS": "DECREASES", "INHIBITS": "DECREASES", "DECREASE": "DECREASES",
    "SUPPRESSES": "DECREASES", "ENHANCES": "INCREASES", "PROMOTES": "INCREASES", "INCREASE": "INCREASES",
    "RAISES": "INCREASES", "UPREGULATES": "INCREASES", "DOWNREGULATES": "DECREASES",
    "CORRELATED_WITH": "CORRELATES_WITH", "CORRELATES": "CORRELATES_WITH", "LINKED_TO": "ASSOCIATED_WITH",
    "CONTRIBUTES_TO": "CAUSES", "LEADS_TO": "CAUSES", "RESULTS_IN": "CAUSES", "INFLUENCES": "AFFECTS",
    "MODULATES": "AFFECTS", "IS_PART_OF": "PART_OF", "COMPONENT_OF": "PART_OF", "HAS": "CONTAINS",
    "MEASURES": "MEASURED_BY", "EDITS": "TARGETS", "MUTATES": "TARGETS", "BINDS": "INTERACTS_WITH",
    "WORSENS": "IMPAIRS", "REDUCES_QUALITY": "IMPAIRS", "BENEFITS": "IMPROVES",
    "DOES_NOT_AFFECT": "NO_EFFECT_ON", "NOT_AFFECTS": "NO_EFFECT_ON",
}
_TYPE_SYNONYMS = {
    "ENZYME": "PROTEIN", "NUTRIENT": "COMPOUND", "CHEMICAL": "COMPOUND", "TISSUE": "STRUCTURE",
    "CULTIVAR": "GERMPLASM", "VARIETY": "GERMPLASM", "ACCESSION": "GERMPLASM", "LINE": "GERMPLASM",
    "POPULATION": "GERMPLASM", "MUTANT": "GERMPLASM", "QTL": "MARKER", "SNP": "MARKER",
    "ASSAY": "METHOD", "TECHNIQUE": "METHOD", "SPECIES": "ORGANISM", "PRODUCT": "FOOD",
    "COUNTRY": "LOCATION", "REGION": "LOCATION", "PROPERTY": "TRAIT", "TREATMENT": "PROCESS",
}


def _etype(t: str) -> str:
    t = re.sub(r"[^A-Z_]", "", (t or "").upper())
    t = _TYPE_SYNONYMS.get(t, t)
    return t if t in ENTITY_TYPES else "TRAIT"


def _relation(r: str) -> str | None:
    r = re.sub(r"[^A-Z_]", "_", (r or "").upper()).strip("_")
    r = _REL_SYNONYMS.get(r, r)
    return r if r in RELATION_TYPES else ("ASSOCIATED_WITH" if r else None)


# A gene and its product often share a symbol (COMT, CAD, PAL); folding one into the other's
# canonical name would erase the distinction, so these never cross over.
_NO_CROSS = {frozenset({"GENE", "PROTEIN"})}


def canonical(name: str, etype: str) -> tuple[str, str]:
    name = re.sub(r"\s+", " ", (name or "").strip(" .,;:'\""))
    key = norm_key(name)
    hit = _vocab().get(key)
    if not hit and key.endswith("s") and len(name) > 5:
        hit = _vocab().get(key[:-1])
    if hit and frozenset({hit[1], etype}) in _NO_CROSS:
        return name, etype
    return hit or (name, etype)


def is_reference_page(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(text.strip()) < 250:
        return True
    head = "\n".join(lines[:4]).lower()
    refish = sum(
        1 for ln in lines
        if re.search(r"\(\d{4}[a-z]?\)|\b(19|20)\d{2}[a-z]?[.;,]\s|doi\.org|https?://|\bvol\.|\bpp\.", ln, re.I)
    )
    starts = head.startswith(("references", "literature cited", "bibliography"))
    return starts and refish > len(lines) * 0.25 or refish > len(lines) * 0.55


def _chunk_for_quote(quote: str, page_chunks: list[dict]) -> str | None:
    if not page_chunks:
        return None
    for c in page_chunks:
        if quote_in_text(quote, c["text"]):
            return c["id"]
    qwords = set(re.findall(r"[a-z0-9]{4,}", _norm(quote)))
    best = max(page_chunks, key=lambda c: len(qwords & set(re.findall(r"[a-z0-9]{4,}", _norm(c["text"])))))
    return best["id"]


def extract_page(doc_id: str, title: str, page: int, pages: list[str],
                 chunks_by_page: dict[int, list[dict]]) -> dict:
    """OpenRouter path: one LLM call for one page, then the shared checks."""
    text = pages[page - 1].strip()
    if is_reference_page(text):
        return {"entities": [], "mentions": [], "claims": [], "skipped": True}
    body = text[:MAX_PAGE_CHARS]
    msgs = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Document: {title}\nPage {page}\n\n<page>\n{body}\n</page>"},
    ]
    try:
        data = llm.complete_json(msgs, purpose="extract", model=settings.extract_model, max_tokens=4000)
    except json.JSONDecodeError:
        # A dense page can overrun the output budget; ask for the strongest findings only.
        msgs[1]["content"] += "\n\nThis page is dense: return at most 12 entities and 8 claims."
        data = llm.complete_json(msgs, purpose="extract", model=settings.extract_model, max_tokens=4000)
    for c in data.get("claims") or []:
        if isinstance(c, dict):
            c.setdefault("page", page)
    out = build_records(doc_id, pages, {page: chunks_by_page.get(page, [])}, data,
                        f"llm:{settings.extract_model}", only_page=page)
    out["skipped"] = False
    return out


CLAIM_KINDS = {"finding", "background", "method", "hypothesis"}


def build_records(doc_id: str, pages: list[str], chunks_by_page: dict[int, list[dict]], data: dict,
                  method: str, only_page: int | None = None) -> dict:
    """Turn an extraction (entities + claims, each claim with a page and a verbatim quote) into
    graph records. Every quote is searched for on its page; a quote found on another page is
    moved there; a quote found nowhere is kept as unverified with lowered confidence.

    Returns {entities, mentions, claims, issues}.
    """
    entities: dict[str, dict] = {}
    by_surface: dict[str, str] = {}
    issues: list[str] = []

    def add_entity(name: str, etype: str, aliases=(), description: str = "") -> str | None:
        name = re.sub(r"\s+", " ", (name or "").strip())
        if not name or len(name) > 80 or len(name.split()) > 7:
            issues.append(f"entity name rejected (empty or too long): {name[:60]!r}")
            return None
        cname, ctype = canonical(name, _etype(etype))
        if not norm_key(cname):
            return None
        eid = entity_id(ctype, cname)
        e = entities.setdefault(eid, {"id": eid, "type": ctype, "name": cname, "aliases": [], "description": ""})
        for a in [name, *aliases]:
            a = (a or "").strip()
            if a and a.lower() != cname.lower() and a not in e["aliases"] and len(a) < 90:
                e["aliases"].append(a)
        if description and not e["description"]:
            e["description"] = description[:300]
        by_surface[norm_key(name)] = eid
        by_surface[norm_key(cname)] = eid
        return eid

    for e in data.get("entities") or []:
        if isinstance(e, dict):
            add_entity(str(e.get("name") or ""), str(e.get("type") or ""),
                       [str(a) for a in (e.get("aliases") or []) if isinstance(a, str)],
                       str(e.get("description") or ""))

    claims: list[dict] = []
    seen: set[str] = set()
    n_pages = len(pages)
    for i, c in enumerate(data.get("claims") or []):
        if not isinstance(c, dict):
            continue
        rel = _relation(str(c.get("relation") or ""))
        s_name, o_name = str(c.get("subject") or ""), str(c.get("object") or "")
        if not rel or not s_name or not o_name:
            issues.append(f"claim {i}: missing subject, object or relation")
            continue
        sid = by_surface.get(norm_key(s_name)) or add_entity(s_name, str(c.get("subject_type") or ""))
        oid = by_surface.get(norm_key(o_name)) or add_entity(o_name, str(c.get("object_type") or ""))
        if not sid or not oid:
            continue
        if sid == oid:
            issues.append(f"claim {i}: subject and object are the same entity ({s_name})")
            continue
        try:
            page = int(c.get("page") or only_page or 0)
        except (TypeError, ValueError):
            page = 0
        evidence = re.sub(r"\s+", " ", str(c.get("evidence") or "")).strip()[:700]
        verified = False
        if evidence and 1 <= page <= n_pages and quote_in_text(evidence, pages[page - 1]):
            verified = True
        elif evidence and only_page is None:
            for p in range(1, n_pages + 1):
                if quote_in_text(evidence, pages[p - 1]):
                    if page:
                        issues.append(f"claim {i}: quote is on page {p}, not {page}; moved")
                    page, verified = p, True
                    break
        if not verified:
            issues.append(f"claim {i} (page {page}): quote not found in the text: {evidence[:120]!r}")
        if not 1 <= page <= max(n_pages, 1):
            page = only_page or 1
        try:
            conf = float(c.get("confidence", 0.8))
        except (TypeError, ValueError):
            conf = 0.8
        conf = max(0.05, min(1.0, conf if verified else conf * 0.5))
        kind = str(c.get("kind") or "finding").strip().lower()
        kind = kind if kind in CLAIM_KINDS else "finding"
        cid = hashlib.sha1(f"{doc_id}|{page}|{sid}|{rel}|{oid}|{evidence[:80]}".encode()).hexdigest()[:20]
        if cid in seen:
            continue
        seen.add(cid)
        page_chunks = chunks_by_page.get(page, [])
        claims.append({
            "id": cid, "subject_id": sid, "object_id": oid, "relation": rel,
            "statement": str(c.get("statement") or "").strip()[:500],
            "context": str(c.get("context") or "").strip()[:400],
            "value": str(c.get("value") or "").strip()[:200],
            "kind": kind,
            "evidence": evidence, "page": page, "confidence": round(conf, 3), "verified": verified,
            "chunk_id": _chunk_for_quote(evidence, page_chunks) if evidence and page_chunks else None,
            "method": method,
        })

    # Mentions: every passage whose text names the entity (or an alias).
    mentions: list[dict] = []
    all_chunks = [c for cs in chunks_by_page.values() for c in cs]
    normed = [(c["id"], _norm(c["text"])) for c in all_chunks]
    for eid, e in entities.items():
        names = {norm_key(e["name"]), *(norm_key(a) for a in e["aliases"])}
        pats = [re.compile(rf"(?<![a-z0-9]){re.escape(n)}") for n in names if len(n) >= 3]
        hit = False
        for chunk_id, t in normed:
            if any(p.search(t) for p in pats):
                mentions.append({"chunk_id": chunk_id, "entity_id": eid})
                hit = True
        if not hit:
            for cl in claims:
                if cl["chunk_id"] and eid in (cl["subject_id"], cl["object_id"]):
                    mentions.append({"chunk_id": cl["chunk_id"], "entity_id": eid})
    return {"entities": list(entities.values()), "mentions": mentions, "claims": claims, "issues": issues}
