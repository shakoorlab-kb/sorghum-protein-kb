#!/usr/bin/env python3
"""
Build a local RAG knowledge base for sorghum protein research.

Creates:
  knowledge_base/chroma/     - ChromaDB persistent vector store
  knowledge_base/chunks.jsonl - all text chunks with metadata
  knowledge_base/PROJECT_KNOWLEDGE.md - curated project knowledge
  scripts/query_kb.py        - CLI query interface (same file has query helpers)

Uses sentence-transformers (all-MiniLM-L6-v2) + ChromaDB.
Falls back to a pure numpy TF-IDF index if chroma/torch unavailable.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPERS = ROOT / "papers"
DOCS = ROOT / "documents"
META = ROOT / "metadata"
KB = ROOT / "knowledge_base"
KB.mkdir(exist_ok=True)

CHUNK_SIZE = 900
CHUNK_OVERLAP = 150


PROJECT_KNOWLEDGE = """
# Sorghum Protein Knowledge Base — Project Core Knowledge

## Overall Goal
Develop sorghum grain traits, genotypes, gene targets, and predictive tools that improve
protein bioavailability from sorghum-based foods, focused on African food-security systems.
Identify materials with improved protein digestibility after food-relevant processing
(raw, wet-cooked, fermented/cooked) while maintaining acceptable grain quality, total protein,
amino acid profile, and agronomic relevance.

## Why Sorghum Protein Digestibility Matters
Sorghum (Sorghum bicolor L. Moench) is a climate-resilient staple cereal critical for food
security in Africa and Asia. Nearly 40% of world sorghum production is used for direct human
consumption. However, sorghum proteins — especially kafirins — have lower digestibility than
proteins of other major cereals, particularly after wet cooking. Poor protein bioavailability
contributes to malnutrition risk in sorghum-dependent populations, especially children.

## Sorghum Proteins (Kafirins)
- Sorghum storage proteins are prolamins called kafirins, located in protein bodies in the endosperm.
- Major classes: alpha-kafirins (~80%), beta-kafirins, gamma-kafirins; delta-kafirins also described.
- Kafirins are rich in proline and glutamine, low in lysine (limiting amino acid for protein quality).
- Gamma- and beta-kafirins are cysteine-rich and form disulfide cross-links.
- Upon cooking (wet heat), kafirins form disulfide-bonded polymers / cross-links that restrict
  protease access, lowering protein digestibility compared with raw grain and other cereals.
- Protein–starch interactions further reduce enzymatic access to both protein and starch.
- Protein bodies with kafirin shells can encapsulate starch granules, reducing digestibility.

## Key Factors Reducing Protein Digestibility
1. Disulfide bonding / kafirin polymerization during wet cooking
2. Protein–starch matrix interactions and cross-linking
3. Condensed tannins / polyphenols (especially in tannin sorghums with pigmented testa)
4. Enzyme inhibitors present in sorghum
5. Kernel structure / vitreous endosperm / hardness limiting enzyme penetration
6. Drought stress: may increase total protein but decrease starch and protein digestibility

## Factors / Interventions That Can Improve Digestibility
- Fermentation (traditional African processing) — can improve protein digestibility
- Germination / malting
- Decortication / pearling (removes tannin-rich outer layers)
- Certain extrusion / processing methods
- Reducing agents that break disulfide bonds (research context)
- Genetic approaches: highly digestible (HD) sorghum mutants with altered protein body structure
  (e.g., floury/highly digestible phenotypes with irregular protein bodies, improved protease access)
- Breeding / gene-editing targets related to kafirin composition, protein-body morphology,
  tannin status, and endosperm texture
- Selecting non-tannin food-grade germplasm with favorable kafirin profiles

## Dual Nutritional Nature
- Lower digestibility is a problem for malnourished / staple-diet populations needing bioavailable protein.
- Lower starch/protein digestibility can benefit glycemic control (diabetes) and may influence gut microbiome.
- Project priority: improve protein bioavailability for African food-security use cases while
  monitoring grain quality and metabolic implications.

## Assay & Phenotyping Concepts
- Distinguish true bioavailability gains from artifacts (low protein, poor grain quality, sample issues)
- Compare raw vs wet-cooked digestibility; cooking typically decreases sorghum protein digestibility
- Include tannin and non-tannin checks; high- and low-digestibility references
- Targeted amino acid / kafirin profiling on extreme lines
- Maintain total protein and amino acid quality alongside digestibility gains

## Grain Structure Relevant to Protein
- Caryopsis: pericarp, endosperm (aleurone, vitreous, floury), germ; optional testa (tannins)
- Vitreous endosperm: dense protein-starch matrix, often harder, can reduce digestibility
- Floury endosperm: looser structure, often associated with higher digestibility phenotypes
- Highly digestible mutants often show altered (invaginated/irregular) protein bodies

## AI / Knowledge-Base Use Cases
- Retrieve mechanisms of kafirin digestibility loss on cooking
- Compare processing methods for improving bioavailability
- Summarize genetic / mutant literature for editing target ideation
- Link traits (tannin, hardness, kafirin subclass) to digestibility outcomes
- Support prioritization of germplasm and gene targets for African food sorghum
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks = []
    i = 0
    while i < len(text):
        chunk = text[i : i + chunk_size]
        if chunk.strip():
            chunks.append(chunk.strip())
        if i + chunk_size >= len(text):
            break
        i += chunk_size - overlap
    return chunks


def extract_pdf_text(path: Path) -> str:
    # Prefer pdftotext
    import subprocess

    try:
        r = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    except Exception:
        pass
    try:
        import pypdf

        reader = pypdf.PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        pass
    return ""


def extract_docx_text(path: Path) -> str:
    import zipfile
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    lines = []
    for p in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        texts = [
            t.text or ""
            for t in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
        ]
        line = "".join(texts).strip()
        if line:
            lines.append(line)
    # Drop timeline-heavy quarterly milestone sections for cleaner KB (keep objectives/budget concepts)
    cleaned = []
    skip = False
    for line in lines:
        if re.match(r"^Year\s+\d+:", line) or line.strip() in {
            "Quarter",
            "Milestones",
            "Deliverables",
        }:
            skip = True
            continue
        if line.startswith("Technical Objective") or line.startswith("Overall Goal"):
            skip = False
        if line.startswith("Year 1 Equipment") or line.startswith("Equipment and Service"):
            skip = False
        if skip and (
            re.match(r"^Q\d+", line)
            or "Go/No-Go" in line
            or line.startswith("End of Year")
            or line.startswith("Decision:")
        ):
            continue
        if not skip:
            cleaned.append(line)
    # If cleaning removed too much, fall back
    if len(cleaned) < 20:
        return "\n".join(lines)
    return "\n".join(cleaned)


def load_catalog() -> dict[str, dict]:
    path = META / "paper_catalog.json"
    if not path.exists():
        return {}
    papers = json.loads(path.read_text())
    by_name = {}
    for p in papers:
        lp = p.get("local_path")
        if lp:
            by_name[Path(lp).name] = p
        if p.get("doi"):
            by_name[p["doi"]] = p
    return by_name


def gather_documents() -> list[dict]:
    docs = []
    # Project knowledge
    docs.append(
        {
            "doc_id": "project_core_knowledge",
            "title": "Sorghum Protein Project Core Knowledge",
            "source_type": "project_knowledge",
            "path": "knowledge_base/PROJECT_KNOWLEDGE.md",
            "text": PROJECT_KNOWLEDGE,
            "year": None,
            "doi": None,
        }
    )
    (KB / "PROJECT_KNOWLEDGE.md").write_text(PROJECT_KNOWLEDGE)

    catalog = load_catalog()

    # Outline docx
    for path in DOCS.glob("*.docx"):
        text = extract_docx_text(path)
        docs.append(
            {
                "doc_id": f"docx:{path.name}",
                "title": path.stem,
                "source_type": "project_document",
                "path": str(path.relative_to(ROOT)),
                "text": text,
                "year": None,
                "doi": None,
            }
        )

    # Review fulltext if present
    review_txt = META / "review_fulltext.txt"
    if review_txt.exists():
        docs.append(
            {
                "doc_id": "review_fulltext",
                "title": "Sorghum Starch and Protein Digestibility Review (full text)",
                "source_type": "review_paper",
                "path": str(review_txt.relative_to(ROOT)),
                "text": review_txt.read_text(errors="ignore"),
                "year": 2026,
                "doi": "10.3390/foods15101681",
            }
        )

    # All PDFs in papers/. Prefer the structured record (sections + every table row +
    # OCR of scanned pages and image regions), the same text ingest_paper indexes, so a
    # full rebuild and an incremental ingest never disagree.
    import structured_store as store

    structured_by_pdf: dict[str, dict] = {}
    for rec_path in sorted((KB / "structured").glob("*/paper.json")):
        try:
            rec = json.loads(rec_path.read_text())
        except Exception:
            continue
        src = Path(rec.get("source_pdf") or "").name
        if src:
            structured_by_pdf[src] = rec

    for path in sorted(PAPERS.glob("*.pdf")):
        # skip if we already loaded as review_fulltext and this is the same review
        if "foods15101681" in path.name and review_txt.exists():
            continue
        meta = catalog.get(path.name, {})
        rec = structured_by_pdf.get(path.name)
        if rec:
            text = store.record_index_text(rec)
            docs.append(
                {
                    "doc_id": f"structured:{rec['paper_id']}",
                    "title": rec.get("title") or meta.get("title") or path.stem,
                    "source_type": "structured_paper",
                    "path": f"knowledge_base/structured/{rec['paper_id']}/paper.json",
                    "text": text,
                    "year": rec.get("year") or meta.get("year"),
                    "doi": rec.get("doi") or meta.get("doi"),
                    "authors": rec.get("authors") or meta.get("authors"),
                    "abstract": rec.get("abstract") or meta.get("abstract"),
                }
            )
            continue
        log(f"Extracting (no structured record yet): {path.name}")
        text = extract_pdf_text(path)
        if len(text.strip()) < 200:
            from page_text import pdf_full_text

            log("  little/no text layer: running OCR")
            text = pdf_full_text(path)
        if len(text.strip()) < 200:
            log("  still no text after OCR; skipped")
            continue
        docs.append(
            {
                "doc_id": f"pdf:{path.name}",
                "title": meta.get("title") or path.stem,
                "source_type": "paper",
                "path": str(path.relative_to(ROOT)),
                "text": text,
                "year": meta.get("year"),
                "doi": meta.get("doi"),
                "authors": meta.get("authors"),
                "abstract": meta.get("abstract"),
            }
        )

    # Abstracts from catalog for papers without PDF (still useful for retrieval)
    catalog_path = META / "paper_catalog.json"
    if catalog_path.exists():
        for p in json.loads(catalog_path.read_text()):
            if p.get("download_status") in ("downloaded", "already_present"):
                continue
            abstract = (p.get("abstract") or "").strip()
            if len(abstract) < 80:
                continue
            title = p.get("title") or "Untitled"
            docs.append(
                {
                    "doc_id": f"abstract:{p.get('doi') or paper_hash(title)}",
                    "title": title,
                    "source_type": "abstract_only",
                    "path": p.get("link") or (f"https://doi.org/{p['doi']}" if p.get("doi") else None),
                    "text": f"Title: {title}\n\nAbstract: {abstract}",
                    "year": p.get("year"),
                    "doi": p.get("doi"),
                    "authors": p.get("authors"),
                }
            )
    return docs


def paper_hash(title: str) -> str:
    return hashlib.sha1(title.encode()).hexdigest()[:12]


def make_chunks(docs: list[dict]) -> list[dict]:
    chunks = []
    for doc in docs:
        parts = chunk_text(doc["text"])
        for i, part in enumerate(parts):
            cid = hashlib.sha1(f"{doc['doc_id']}:{i}:{part[:40]}".encode()).hexdigest()[:16]
            chunks.append(
                {
                    "id": cid,
                    "text": part,
                    "doc_id": doc["doc_id"],
                    "title": doc.get("title"),
                    "source_type": doc.get("source_type"),
                    "path": doc.get("path"),
                    "year": doc.get("year"),
                    "doi": doc.get("doi"),
                    "chunk_index": i,
                    "n_chunks": len(parts),
                }
            )
    return chunks


def build_chroma(chunks: list[dict]) -> str:
    import chromadb
    from chromadb.utils import embedding_functions

    persist = str(KB / "chroma")
    try:
        ef = embedding_functions.DefaultEmbeddingFunction()
    except Exception:
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
    client = chromadb.PersistentClient(path=persist)
    # recreate collection
    try:
        client.delete_collection("sorghum_protein")
    except Exception:
        pass
    col = client.create_collection(
        name="sorghum_protein",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine", "description": "Sorghum protein digestibility KB"},
    )
    batch = 64
    for i in range(0, len(chunks), batch):
        batch_chunks = chunks[i : i + batch]
        col.add(
            ids=[c["id"] for c in batch_chunks],
            documents=[c["text"] for c in batch_chunks],
            metadatas=[
                {
                    "title": (c.get("title") or "")[:300],
                    "source_type": c.get("source_type") or "",
                    "path": str(c.get("path") or "")[:300],
                    "year": str(c.get("year") or ""),
                    "doi": str(c.get("doi") or ""),
                    "doc_id": c.get("doc_id") or "",
                    "chunk_index": c.get("chunk_index") or 0,
                }
                for c in batch_chunks
            ],
        )
        log(f"  Indexed {min(i + batch, len(chunks))}/{len(chunks)} chunks")
    return persist


def build_tfidf_fallback(chunks: list[dict]) -> str:
    """Lightweight fallback index without torch/chroma."""
    from collections import Counter
    import math

    def tokenize(t: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", t.lower())

    docs_tokens = [tokenize(c["text"]) for c in chunks]
    df = Counter()
    for toks in docs_tokens:
        df.update(set(toks))
    n = len(chunks)
    idf = {t: math.log((n + 1) / (df[t] + 1)) + 1.0 for t in df}

    vectors = []
    for toks in docs_tokens:
        tf = Counter(toks)
        length = len(toks) or 1
        vec = {t: (tf[t] / length) * idf[t] for t in tf if t in idf}
        vectors.append(vec)

    index = {
        "idf": idf,
        "vectors": vectors,
        "chunks": [
            {
                "id": c["id"],
                "text": c["text"],
                "title": c.get("title"),
                "source_type": c.get("source_type"),
                "path": c.get("path"),
                "year": c.get("year"),
                "doi": c.get("doi"),
            }
            for c in chunks
        ],
    }
    out = KB / "tfidf_index.json"
    # vectors as lists of items for JSON
    serial = {
        "idf": index["idf"],
        "vectors": [list(v.items()) for v in vectors],
        "chunks": index["chunks"],
    }
    out.write_text(json.dumps(serial))
    return str(out)


def main() -> None:
    log("=== Gathering documents ===")
    docs = gather_documents()
    log(f"Documents: {len(docs)}")
    (KB / "documents_index.json").write_text(
        json.dumps(
            [
                {
                    "doc_id": d["doc_id"],
                    "title": d.get("title"),
                    "source_type": d.get("source_type"),
                    "path": d.get("path"),
                    "year": d.get("year"),
                    "doi": d.get("doi"),
                    "chars": len(d.get("text") or ""),
                }
                for d in docs
            ],
            indent=2,
        )
    )

    log("=== Chunking ===")
    chunks = make_chunks(docs)
    log(f"Chunks: {len(chunks)}")
    with (KB / "chunks.jsonl").open("w") as f:
        for c in chunks:
            f.write(json.dumps(c) + "\n")

    backend = None
    try:
        log("=== Building ChromaDB vector store (sentence-transformers) ===")
        persist = build_chroma(chunks)
        backend = f"chroma:{persist}"
        log(f"ChromaDB ready at {persist}")
    except Exception as e:
        log(f"ChromaDB unavailable ({e}); building TF-IDF fallback index")
        path = build_tfidf_fallback(chunks)
        backend = f"tfidf:{path}"
        log(f"TF-IDF index ready at {path}")

    info = {
        "backend": backend,
        "n_documents": len(docs),
        "n_chunks": len(chunks),
        "embedding_model": "chroma-default-minilm" if backend.startswith("chroma") else "tfidf",
        "query_script": "scripts/query_kb.py",
    }
    (KB / "kb_info.json").write_text(json.dumps(info, indent=2))
    log(f"\n=== Knowledge base ready ===")
    log(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
