#!/usr/bin/env python3
"""Embedded evidence-backed property graph for the sorghum protein KB.

The graph complements Chroma rather than replacing it:

* Chroma retrieves semantically similar passages.
* This SQLite property graph stores named entities and typed relationships.
* Every scientific relationship keeps the paper and evidence text that created it.

The extractor intentionally favors precision and provenance over trying to infer every
possible fact.  An LLM can add reviewed facts through the MCP ``add_graph_facts`` tool.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
GRAPH_DIR = ROOT / "knowledge_base" / "graph"
DEFAULT_DB = GRAPH_DIR / "knowledge_graph.sqlite3"


# Domain vocabulary. Aliases are normalized to the first (canonical) name.
ENTITY_VOCABULARY: dict[str, dict[str, Sequence[str]]] = {
    "PROTEIN": {
        "kafirin": ("kafirin", "kafirins"),
        "alpha-kafirin": ("alpha-kafirin", "α-kafirin", "alpha kafirin", "α kafirin"),
        "beta-kafirin": ("beta-kafirin", "β-kafirin", "beta kafirin", "β kafirin"),
        "gamma-kafirin": ("gamma-kafirin", "γ-kafirin", "gamma kafirin", "γ kafirin"),
        "delta-kafirin": ("delta-kafirin", "δ-kafirin", "delta kafirin", "δ kafirin"),
        "prolamin": ("prolamin", "prolamins"),
        "glutelin": ("glutelin", "glutelins"),
        "albumin": ("albumin", "albumins"),
        "globulin": ("globulin", "globulins"),
        "zein": ("zein", "zeins"),
        "pepsin": ("pepsin",),
        "trypsin": ("trypsin",),
    },
    "GENE": {
        "Opaque2": ("opaque2", "opaque-2", "o2 gene"),
        "Floury2": ("floury2", "floury-2", "fl2 gene"),
        "PBF": ("prolamin-box binding factor", "pbf gene", "pbf"),
        "RNAi": ("rnai", "rna interference", "rna interference silencing"),
    },
    "TRAIT": {
        "protein digestibility": (
            "protein digestibility",
            "protein digestion",
            "in vitro protein digestibility",
            "ivpd",
        ),
        "protein bioavailability": ("protein bioavailability", "protein availability"),
        "protein content": ("protein content", "total protein"),
        "lysine content": ("lysine content", "high lysine", "lysine concentration"),
        "amino acid quality": ("amino acid quality", "protein quality"),
        "grain hardness": ("grain hardness", "kernel hardness"),
        "endosperm vitreousness": ("endosperm vitreousness", "vitreous endosperm", "vitreousness"),
        "drought tolerance": ("drought tolerance", "drought resistance"),
        "starch digestibility": ("starch digestibility", "starch digestion"),
    },
    "PROCESS": {
        "wet cooking": ("wet cooking", "wet-cooking", "moist cooking"),
        "boiling": ("boiling", "boiled"),
        "fermentation": ("fermentation", "fermented"),
        "germination": ("germination", "germinated", "sprouting"),
        "malting": ("malting", "malted"),
        "extrusion": ("extrusion", "extruded"),
        "decortication": ("decortication", "decorticated", "dehulling"),
        "milling": ("milling", "milled"),
        "soaking": ("soaking", "soaked"),
        "nixtamalization": ("nixtamalization", "alkaline cooking"),
        "dry heat": ("dry heat", "dry heating", "roasting"),
        "enzymatic hydrolysis": ("enzymatic hydrolysis", "enzyme hydrolysis"),
        "CRISPR": ("crispr", "crispr/cas9", "genome editing", "gene editing"),
    },
    "COMPOUND": {
        "tannin": ("tannin", "tannins", "condensed tannin", "condensed tannins"),
        "phenolic compound": ("phenolic compound", "phenolic compounds", "polyphenol", "polyphenols"),
        "phytic acid": ("phytic acid", "phytate"),
        "starch": ("starch",),
        "lysine": ("lysine",),
        "methionine": ("methionine",),
        "cysteine": ("cysteine",),
        "disulfide bond": ("disulfide bond", "disulfide bonds", "disulphide bond", "disulphide bonds"),
        "reducing agent": ("reducing agent", "reducing agents"),
    },
    "STRUCTURE": {
        "protein body": ("protein body", "protein bodies"),
        "protein-starch matrix": ("protein-starch matrix", "protein starch matrix"),
        "endosperm": ("endosperm",),
        "pericarp": ("pericarp",),
        "testa": ("testa",),
    },
    "PHENOTYPE": {
        "highly digestible phenotype": (
            "highly digestible phenotype",
            "high digestibility mutant",
            "highly digestible mutant",
            "highly digestible sorghum mutant",
            "highly digestible mutant cultivar",
            "hd mutant",
        ),
        "opaque endosperm": ("opaque endosperm", "opaque phenotype"),
        "floury endosperm": ("floury endosperm", "floury phenotype"),
    },
    "ORGANISM": {
        "Sorghum bicolor": ("sorghum bicolor", "s. bicolor", "sorghum"),
        "Zea mays": ("zea mays", "maize", "corn"),
        "Oryza sativa": ("oryza sativa", "rice"),
        "Triticum aestivum": ("triticum aestivum", "wheat"),
    },
    "METHOD": {
        "in vitro digestion": ("in vitro digestion", "in-vitro digestion"),
        "INFOGEST": ("infogest", "infogest protocol"),
        "SDS-PAGE": ("sds-page", "sds page"),
        "LC-MS/MS": ("lc-ms/ms", "lc–ms/ms", "tandem mass spectrometry"),
        "proteomics": ("proteomics", "proteomic analysis"),
        "QTL mapping": ("qtl mapping", "quantitative trait locus", "quantitative trait loci"),
    },
}

RELATION_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("IMPROVES", re.compile(r"\b(improv\w*|enhanc\w*|benefit\w*)\b", re.I)),
    ("INCREASES", re.compile(r"\b(increas\w*|promot\w*|up-?regulat\w*)\b", re.I)),
    ("REDUCES", re.compile(r"\b(reduc\w*|decreas\w*|lower\w*|inhibit\w*|suppress\w*)\b", re.I)),
    ("ENCODES", re.compile(r"\b(encod\w*)\b", re.I)),
    ("CONTRIBUTES_TO", re.compile(r"\b(contribut\w*|lead\w* to|result\w* in|responsible for)\b", re.I)),
    ("ASSOCIATED_WITH", re.compile(r"\b(associat\w* with|correlat\w* with|linked? to)\b", re.I)),
    ("AFFECTS", re.compile(r"\b(affect\w*|influenc\w*|modulat\w*)\b", re.I)),
)

ALLOWED_NODE_TYPES = {
    "PAPER",
    "AUTHOR",
    "JOURNAL",
    "GENE",
    "PROTEIN",
    "TRAIT",
    "PROCESS",
    "COMPOUND",
    "STRUCTURE",
    "PHENOTYPE",
    "ORGANISM",
    "METHOD",
    "TOPIC",
    "DATASET",
}

STOPWORDS = {
    "about", "after", "also", "among", "and", "are", "can", "could", "does", "do",
    "from", "have", "how", "into", "is", "mechanism", "of", "on", "or", "paper",
    "protein", "relationship", "sorghum", "that", "the", "their", "this", "through",
    "to", "using", "what", "when", "where", "which", "why", "with", "would",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_name(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.replace("α", "alpha").replace("β", "beta").replace("γ", "gamma").replace("δ", "delta")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_relation(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "_", (value or "").strip()).strip("_").upper()
    if not value or len(value) > 64:
        raise ValueError("relation must contain 1-64 letters/numbers")
    return value


def stable_id(prefix: str, *parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return f"{prefix}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:20]}"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _decode(value: str | None, fallback: object) -> object:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _compile_aliases() -> list[tuple[str, str, str, re.Pattern[str]]]:
    rows = []
    for label, entities in ENTITY_VOCABULARY.items():
        for canonical, aliases in entities.items():
            for alias in sorted(set((*aliases, canonical)), key=len, reverse=True):
                escaped = re.escape(alias).replace(r"\ ", r"[\s-]+")
                rows.append((label, canonical, alias, re.compile(rf"(?<!\w){escaped}(?!\w)", re.I)))
    rows.sort(key=lambda row: len(row[2]), reverse=True)
    return rows


COMPILED_ALIASES = _compile_aliases()


def split_sentences(text: str, limit: int = 350) -> list[str]:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return []
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9αβγδ])", clean)
    out = []
    for piece in pieces:
        piece = piece.strip()
        if len(piece) < 20:
            continue
        if len(piece) > limit:
            piece = piece[:limit].rsplit(" ", 1)[0] + "…"
        out.append(piece)
    return out


def detect_entities(text: str) -> list[dict]:
    """Return canonical entities and their first character position."""
    found: dict[tuple[str, str], dict] = {}
    occupied: list[tuple[int, int]] = []
    for label, canonical, alias, pattern in COMPILED_ALIASES:
        matches = list(pattern.finditer(text or ""))
        match = next(
            (
                candidate
                for candidate in matches
                if not any(
                    candidate.start() < end and candidate.end() > start
                    for start, end in occupied
                )
            ),
            None,
        )
        if match is None:
            continue
        key = (label, normalize_name(canonical))
        if key not in found:
            found[key] = {
                "type": label,
                "name": canonical,
                "alias": match.group(0),
                "start": match.start(),
            }
            occupied.append((match.start(), match.end()))

    # High-confidence Sorghum gene identifiers not covered by the vocabulary.
    gene_patterns = (
        re.compile(r"\bSobic\.\d{3}G\d{5,7}\b", re.I),
        re.compile(r"\bSORBI[_-]\d{6,14}\b", re.I),
        re.compile(r"\bSb[A-Z][A-Za-z0-9_-]{2,14}\b"),
    )
    for pattern in gene_patterns:
        for match in pattern.finditer(text or ""):
            name = match.group(0)
            key = ("GENE", normalize_name(name))
            found.setdefault(
                key,
                {"type": "GENE", "name": name, "alias": name, "start": match.start()},
            )
    return sorted(found.values(), key=lambda row: row["start"])


def infer_relation(sentence: str, left: dict, right: dict) -> tuple[str, float]:
    between_start = min(left["start"], right["start"])
    between_end = max(left["start"], right["start"])
    between = sentence[between_start:between_end]
    candidates: list[tuple[int, str]] = []
    for relation, pattern in RELATION_CUES:
        for match in pattern.finditer(between):
            candidates.append((match.start(), relation))
    if candidates:
        # The cue nearest the right-hand entity is usually the predicate joining the pair.
        relation = max(candidates, key=lambda item: item[0])[1]
        causal = {"IMPROVES", "INCREASES", "REDUCES", "ENCODES", "CONTRIBUTES_TO", "AFFECTS"}
        plausible_sources = {"PROCESS", "COMPOUND", "GENE", "PROTEIN", "STRUCTURE", "PHENOTYPE", "METHOD"}
        plausible_targets = {"TRAIT", "PROTEIN", "COMPOUND", "STRUCTURE", "PHENOTYPE", "GENE", "PROCESS"}
        if relation in causal and (
            left.get("type") not in plausible_sources or right.get("type") not in plausible_targets
        ):
            return "CO_OCCURS_WITH", 0.52
        return relation, 0.72
    return "CO_OCCURS_WITH", 0.52


def paper_text_units(paper: dict) -> Iterable[tuple[str, str]]:
    """Yield compact, evidence-bearing text without indexing a whole PDF twice."""
    if paper.get("title"):
        yield ("title", str(paper["title"]))
    if paper.get("abstract"):
        yield ("abstract", str(paper["abstract"]))
    for value in paper.get("key_claims") or []:
        if value:
            yield ("claim", str(value))
    for value in paper.get("quantitative_facts") or []:
        if isinstance(value, dict):
            value = value.get("text") or value.get("evidence") or _json(value)
        if value:
            yield ("fact", str(value))
    for fig in paper.get("figures") or []:
        value = " ".join(
            str(fig.get(k) or "") for k in ("caption_guess", "nearby_text", "ocr_text")
        ).strip()
        if value:
            yield ("figure_caption", value)
    for table in paper.get("tables") or []:
        value = " ".join(
            [str(table.get("caption_guess") or ""), " ".join(map(str, table.get("headers") or []))]
        ).strip()
        if value:
            yield ("table_caption", value)


class ClosingConnection(sqlite3.Connection):
    """SQLite connection whose context manager also closes the file handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class KnowledgeGraph:
    def __init__(self, db_path: Path | str = DEFAULT_DB):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        self.initialize(conn)
        return conn

    @staticmethod
    def initialize(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                identity_key TEXT NOT NULL,
                search_text TEXT NOT NULL,
                aliases_json TEXT NOT NULL DEFAULT '[]',
                properties_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(type, identity_key)
            );
            CREATE TABLE IF NOT EXISTS edges (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
                relation TEXT NOT NULL,
                target_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
                paper_id TEXT NOT NULL DEFAULT '',
                evidence TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.5,
                extraction_method TEXT NOT NULL,
                properties_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_map (
                paper_id TEXT PRIMARY KEY,
                node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
                doi TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_normalized ON nodes(normalized_name);
            CREATE INDEX IF NOT EXISTS idx_nodes_identity ON nodes(identity_key);
            CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
            CREATE INDEX IF NOT EXISTS idx_edges_paper ON edges(paper_id);
            CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation);
            """
        )

    @staticmethod
    def _upsert_node(
        conn: sqlite3.Connection,
        node_type: str,
        name: str,
        aliases: Sequence[str] | None = None,
        properties: dict | None = None,
        identity: str | None = None,
    ) -> str:
        node_type = (node_type or "TOPIC").strip().upper()
        if node_type not in ALLOWED_NODE_TYPES:
            raise ValueError(f"unsupported node type {node_type!r}")
        name = re.sub(r"\s+", " ", (name or "").strip())[:300]
        normalized = normalize_name(name)
        if not normalized:
            raise ValueError("node name is empty")
        identity_key = normalize_name(identity or name)
        node_id = stable_id("node", node_type, identity_key)
        now = utc_now()
        old = conn.execute("SELECT aliases_json, properties_json FROM nodes WHERE id=?", (node_id,)).fetchone()
        old_aliases = set(_decode(old["aliases_json"], []) if old else [])
        old_props = dict(_decode(old["properties_json"], {}) if old else {})
        old_aliases.update(a.strip() for a in (aliases or []) if a and a.strip())
        old_props.update({k: v for k, v in (properties or {}).items() if v not in (None, "", [], {})})
        search_text = normalize_name(" ".join([name, *sorted(old_aliases)]))
        conn.execute(
            """
            INSERT INTO nodes(
              id,type,name,normalized_name,identity_key,search_text,
              aliases_json,properties_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name,
              normalized_name=excluded.normalized_name,
              search_text=excluded.search_text,
              aliases_json=excluded.aliases_json,
              properties_json=excluded.properties_json,
              updated_at=excluded.updated_at
            """,
            (
                node_id, node_type, name, normalized, identity_key, search_text,
                _json(sorted(old_aliases)), _json(old_props), now, now,
            ),
        )
        return node_id

    @staticmethod
    def _upsert_edge(
        conn: sqlite3.Connection,
        source_id: str,
        relation: str,
        target_id: str,
        paper_id: str = "",
        evidence: str = "",
        confidence: float = 0.5,
        extraction_method: str = "deterministic",
        properties: dict | None = None,
    ) -> str:
        relation = normalize_relation(relation)
        evidence = re.sub(r"\s+", " ", evidence or "").strip()[:1200]
        confidence = max(0.0, min(float(confidence), 1.0))
        edge_id = stable_id("edge", source_id, relation, target_id, paper_id, evidence)
        now = utc_now()
        conn.execute(
            """
            INSERT INTO edges(
              id,source_id,relation,target_id,paper_id,evidence,confidence,
              extraction_method,properties_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              confidence=MAX(edges.confidence, excluded.confidence),
              extraction_method=excluded.extraction_method,
              properties_json=excluded.properties_json,
              updated_at=excluded.updated_at
            """,
            (
                edge_id, source_id, relation, target_id, paper_id, evidence, confidence,
                extraction_method, _json(properties or {}), now, now,
            ),
        )
        return edge_id

    def index_paper(self, paper: dict) -> dict:
        paper_id = str(paper.get("paper_id") or "").strip()
        if not paper_id:
            return {"ok": False, "error": "paper_id is missing"}
        title = str(paper.get("title") or paper_id)
        doi = str(paper.get("doi") or "").lower()
        paper_key = doi or paper_id
        counts = {"entities": 0, "relationships": 0, "authors": 0, "citations": 0}

        with self.connect() as conn:
            # Refresh automatic facts, but retain facts explicitly added/reviewed by an LLM.
            conn.execute(
                "DELETE FROM edges WHERE paper_id=? AND extraction_method IN ('metadata','deterministic')",
                (paper_id,),
            )
            paper_node = self._upsert_node(
                conn,
                "PAPER",
                title,
                aliases=[paper_id, doi],
                properties={
                    "paper_id": paper_id,
                    "doi": doi,
                    "year": paper.get("year"),
                    "journal": paper.get("journal"),
                    "source_pdf": paper.get("source_pdf"),
                    "status": "structured",
                    "key": paper_key,
                },
                identity=paper_key,
            )
            conn.execute(
                "INSERT INTO paper_map(paper_id,node_id,doi) VALUES(?,?,?) "
                "ON CONFLICT(paper_id) DO UPDATE SET node_id=excluded.node_id, doi=excluded.doi",
                (paper_id, paper_node, doi),
            )

            for author in paper.get("authors") or []:
                if not author or len(str(author)) > 200:
                    continue
                author_node = self._upsert_node(conn, "AUTHOR", str(author))
                self._upsert_edge(
                    conn, paper_node, "AUTHORED_BY", author_node, paper_id,
                    confidence=1.0, extraction_method="metadata",
                )
                counts["authors"] += 1

            journal = str(paper.get("journal") or "").strip()
            if journal:
                journal_node = self._upsert_node(conn, "JOURNAL", journal)
                self._upsert_edge(
                    conn, paper_node, "PUBLISHED_IN", journal_node, paper_id,
                    confidence=1.0, extraction_method="metadata",
                )

            for keyword in paper.get("keywords") or []:
                keyword = re.sub(r"\s+", " ", str(keyword)).strip(" ;,.\t")
                keyword_norm = normalize_name(keyword)
                keyword_is_noise = (
                    bool(re.match(r"^\d", keyword))
                    or keyword_norm in {"however", "therefore", "introduction", "abstract", "keywords"}
                    or len(keyword.split()) > 10
                )
                if 2 < len(keyword) <= 100 and not keyword_is_noise:
                    detected = detect_entities(keyword)
                    exact = next(
                        (
                            entity for entity in detected
                            if normalize_name(entity.get("alias") or "") == normalize_name(keyword)
                        ),
                        None,
                    )
                    if exact:
                        topic = self._upsert_node(
                            conn, exact["type"], exact["name"], aliases=[keyword]
                        )
                    else:
                        topic = self._upsert_node(conn, "TOPIC", keyword)
                    self._upsert_edge(
                        conn, paper_node, "HAS_TOPIC", topic, paper_id,
                        confidence=0.95, extraction_method="metadata",
                    )

            seen_entities: set[str] = set()
            seen_sentences: set[str] = set()
            for unit_kind, unit in paper_text_units(paper):
                for sentence in split_sentences(unit):
                    sentence_key = normalize_name(sentence)
                    if sentence_key in seen_sentences:
                        continue
                    seen_sentences.add(sentence_key)
                    entities = detect_entities(sentence)
                    entity_nodes: list[tuple[dict, str]] = []
                    for entity in entities:
                        node_id = self._upsert_node(
                            conn,
                            entity["type"],
                            entity["name"],
                            aliases=[entity.get("alias") or ""],
                        )
                        entity_nodes.append((entity, node_id))
                        if node_id not in seen_entities:
                            self._upsert_edge(
                                conn, paper_node, "MENTIONS", node_id, paper_id,
                                evidence=sentence, confidence=0.82,
                                extraction_method="deterministic",
                            )
                            seen_entities.add(node_id)

                    # Titles and captions are useful mention evidence but often compress
                    # phrases such as "reduction and digestibility" without asserting a
                    # directional relationship. Infer typed edges only from prose claims.
                    if unit_kind not in {"abstract", "claim", "fact"}:
                        continue
                    # Organism mentions provide context but rarely form the causal relation.
                    # Keep typed inference conservative in entity-heavy sentences; Claude can
                    # add deeper reviewed facts through add_graph_facts.
                    relational_nodes = [
                        pair for pair in entity_nodes if pair[0].get("type") != "ORGANISM"
                    ]
                    if 2 <= len(relational_nodes) <= 3:
                        for i, (left, left_id) in enumerate(relational_nodes):
                            for right, right_id in relational_nodes[i + 1 :]:
                                if left_id == right_id:
                                    continue
                                relation, confidence = infer_relation(sentence, left, right)
                                self._upsert_edge(
                                    conn, left_id, relation, right_id, paper_id,
                                    evidence=sentence, confidence=confidence,
                                    extraction_method="deterministic",
                                )
                                counts["relationships"] += 1

            counts["entities"] = len(seen_entities)

            doi_pattern = re.compile(r"10\.\d{4,9}/[^\s\]\)>\"'<>]+", re.I)
            cited_dois: set[str] = set()
            for reference in paper.get("references") or []:
                text = str(reference)
                for match in doi_pattern.findall(text):
                    cited = match.rstrip(".,;").lower()
                    if cited == doi or cited in cited_dois:
                        continue
                    cited_dois.add(cited)
                    cited_node = self._upsert_node(
                        conn, "PAPER", cited, aliases=[cited],
                        properties={"doi": cited, "status": "referenced"},
                        identity=cited,
                    )
                    self._upsert_edge(
                        conn, paper_node, "CITES", cited_node, paper_id,
                        evidence=text[:600], confidence=1.0, extraction_method="metadata",
                    )
                    counts["citations"] += 1

        return {"ok": True, "paper_id": paper_id, **counts}

    def add_reviewed_facts(self, paper_id: str, facts: Sequence[dict]) -> dict:
        if not paper_id:
            return {"ok": False, "error": "paper_id is required"}
        if len(facts) > 100:
            return {"ok": False, "error": "maximum 100 facts per call"}
        added, errors = 0, []
        with self.connect() as conn:
            paper_row = conn.execute("SELECT node_id FROM paper_map WHERE paper_id=?", (paper_id,)).fetchone()
            if not paper_row:
                return {"ok": False, "error": f"paper {paper_id!r} is not indexed in the graph"}
            paper_node = paper_row["node_id"]
            for index, fact in enumerate(facts):
                try:
                    source_name = str(fact.get("source") or "").strip()
                    target_name = str(fact.get("target") or "").strip()
                    source_type = str(fact.get("source_type") or "TOPIC").upper()
                    target_type = str(fact.get("target_type") or "TOPIC").upper()
                    relation = normalize_relation(str(fact.get("relation") or ""))
                    evidence = re.sub(r"\s+", " ", str(fact.get("evidence") or "")).strip()
                    if not source_name or not target_name:
                        raise ValueError("source and target are required")
                    if len(evidence) < 15:
                        raise ValueError("evidence must quote or closely paraphrase a paper passage")
                    source = self._upsert_node(conn, source_type, source_name)
                    target = self._upsert_node(conn, target_type, target_name)
                    confidence = float(fact.get("confidence", 0.8))
                    self._upsert_edge(
                        conn, source, relation, target, paper_id,
                        evidence=evidence, confidence=confidence,
                        extraction_method="llm-reviewed",
                        properties={"review_note": fact.get("review_note") or ""},
                    )
                    for node in (source, target):
                        self._upsert_edge(
                            conn, paper_node, "MENTIONS", node, paper_id,
                            evidence=evidence, confidence=confidence,
                            extraction_method="llm-reviewed",
                        )
                    added += 1
                except (TypeError, ValueError) as exc:
                    errors.append({"index": index, "error": str(exc)})
        return {"ok": not errors, "paper_id": paper_id, "added": added, "errors": errors}

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "type": row["type"],
            "name": row["name"],
            "aliases": _decode(row["aliases_json"], []),
            "properties": _decode(row["properties_json"], {}),
        }

    def stats(self) -> dict:
        if not self.db_path.exists():
            return {"backend": "sqlite-property-graph", "path": str(self.db_path), "nodes": 0, "edges": 0}
        with self.connect() as conn:
            nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            papers = conn.execute("SELECT COUNT(*) FROM paper_map").fetchone()[0]
            reviewed = conn.execute("SELECT COUNT(*) FROM edges WHERE extraction_method='llm-reviewed'").fetchone()[0]
            node_types = {
                row[0]: row[1]
                for row in conn.execute("SELECT type,COUNT(*) FROM nodes GROUP BY type ORDER BY COUNT(*) DESC")
            }
            relations = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT relation,COUNT(*) FROM edges GROUP BY relation ORDER BY COUNT(*) DESC LIMIT 20"
                )
            }
        return {
            "backend": "sqlite-property-graph",
            "path": str(self.db_path),
            "papers": papers,
            "nodes": nodes,
            "edges": edges,
            "llm_reviewed_edges": reviewed,
            "node_types": node_types,
            "relations": relations,
        }

    def prune_orphans(self) -> int:
        """Remove unreferenced nodes left behind by automatic re-indexing."""
        with self.connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM nodes
                WHERE id NOT IN (SELECT node_id FROM paper_map)
                  AND id NOT IN (SELECT source_id FROM edges)
                  AND id NOT IN (SELECT target_id FROM edges)
                """
            )
            return max(0, int(cursor.rowcount or 0))

    def search_nodes(self, query: str, limit: int = 12) -> list[dict]:
        normalized = normalize_name(query)
        tokens = [t for t in normalized.split() if len(t) >= 3 and t not in STOPWORDS]
        if not normalized:
            return []
        clauses = ["search_text LIKE ?"]
        params: list[object] = [f"%{normalized}%"]
        for token in tokens[:16]:
            clauses.append("search_text LIKE ?")
            params.append(f"%{token}%")
        sql = "SELECT * FROM nodes WHERE " + " OR ".join(clauses) + " LIMIT 250"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        scored = []
        for row in rows:
            name = row["normalized_name"]
            search_text = row["search_text"]
            score = 0.0
            if name == normalized:
                score += 20
            elif normalized in search_text or name in normalized:
                score += 8
            score += sum(2.0 if token == name else 1.0 for token in tokens if token in search_text)
            if row["type"] == "PAPER":
                score -= 2.0
            elif row["type"] == "TOPIC":
                score -= 0.5
            else:
                score += 0.5
            item = self._row_to_node(row)
            item["match_score"] = score
            scored.append(item)
        scored.sort(key=lambda row: (-row["match_score"], row["name"]))
        return scored[: max(1, min(int(limit), 50))]

    def paper_node_ids(self, paper_ids: Sequence[str]) -> list[str]:
        paper_ids = [p for p in dict.fromkeys(paper_ids) if p]
        if not paper_ids:
            return []
        placeholders = ",".join("?" for _ in paper_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT node_id FROM paper_map WHERE paper_id IN ({placeholders})", paper_ids
            ).fetchall()
        return [row["node_id"] for row in rows]

    def explore(
        self,
        query: str = "",
        seed_ids: Sequence[str] | None = None,
        depth: int = 1,
        relation: str = "",
        limit: int = 60,
    ) -> dict:
        depth = max(1, min(int(depth), 3))
        limit = max(1, min(int(limit), 200))
        matched = self.search_nodes(query, limit=10) if query else []
        seeds = list(dict.fromkeys([*(seed_ids or []), *(n["id"] for n in matched)]))
        if not seeds:
            return {"query": query, "matched_nodes": matched, "nodes": [], "edges": []}
        relation = normalize_relation(relation) if relation else ""
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        seen_edges: set[str] = set()
        queue = deque((node_id, 0) for node_id in seeds)
        visited: set[str] = set()
        with self.connect() as conn:
            while queue and len(edges) < limit:
                node_id, level = queue.popleft()
                if node_id in visited or level > depth:
                    continue
                visited.add(node_id)
                node_row = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
                if node_row:
                    nodes[node_id] = self._row_to_node(node_row)
                if level == depth:
                    continue
                params: list[object] = [node_id, node_id]
                rel_sql = ""
                if relation:
                    rel_sql = " AND relation=?"
                    params.append(relation)
                rows = conn.execute(
                    "SELECT * FROM edges WHERE (source_id=? OR target_id=?)" + rel_sql +
                    " ORDER BY confidence DESC, updated_at DESC LIMIT ?",
                    [*params, limit],
                ).fetchall()
                for row in rows:
                    if row["id"] in seen_edges:
                        continue
                    seen_edges.add(row["id"])
                    edge = {
                        "id": row["id"],
                        "source_id": row["source_id"],
                        "relation": row["relation"],
                        "target_id": row["target_id"],
                        "paper_id": row["paper_id"],
                        "evidence": row["evidence"],
                        "confidence": row["confidence"],
                        "extraction_method": row["extraction_method"],
                    }
                    edges.append(edge)
                    other = row["target_id"] if row["source_id"] == node_id else row["source_id"]
                    if other not in visited:
                        queue.append((other, level + 1))
                    if len(edges) >= limit:
                        break
            if nodes:
                missing_ids = {
                    value for edge in edges for value in (edge["source_id"], edge["target_id"])
                    if value not in nodes
                }
                for node_id in missing_ids:
                    row = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
                    if row:
                        nodes[node_id] = self._row_to_node(row)
        return {"query": query, "matched_nodes": matched, "nodes": list(nodes.values()), "edges": edges}

    def paper_graph(self, paper_id: str, limit: int = 100) -> dict:
        seed_ids = self.paper_node_ids([paper_id])
        if not seed_ids:
            return {"paper_id": paper_id, "error": "paper is not indexed in the graph"}
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM edges WHERE paper_id=? "
                "ORDER BY confidence DESC, updated_at DESC LIMIT ?",
                (paper_id, max(1, min(int(limit), 500))),
            ).fetchall()
            node_ids = set(seed_ids)
            edges = []
            for row in rows:
                node_ids.update((row["source_id"], row["target_id"]))
                edges.append(
                    {
                        "id": row["id"],
                        "source_id": row["source_id"],
                        "relation": row["relation"],
                        "target_id": row["target_id"],
                        "paper_id": row["paper_id"],
                        "evidence": row["evidence"],
                        "confidence": row["confidence"],
                        "extraction_method": row["extraction_method"],
                    }
                )
            nodes = []
            for node_id in node_ids:
                row = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
                if row:
                    nodes.append(self._row_to_node(row))
        return {"paper_id": paper_id, "nodes": nodes, "edges": edges}

    def format_connections(self, graph: dict, max_lines: int = 24) -> str:
        nodes = {node["id"]: node for node in graph.get("nodes") or []}
        lines = []
        relation_priority = {
            "IMPROVES": 5,
            "INCREASES": 5,
            "REDUCES": 5,
            "ENCODES": 5,
            "CONTRIBUTES_TO": 5,
            "ASSOCIATED_WITH": 5,
            "AFFECTS": 5,
            "CO_OCCURS_WITH": 3,
            "MENTIONS": 2,
            "HAS_TOPIC": 1,
            "CITES": 1,
            "AUTHORED_BY": 0,
            "PUBLISHED_IN": 0,
        }
        ranked_edges = sorted(
            graph.get("edges") or [],
            key=lambda edge: (
                -relation_priority.get(edge.get("relation"), 4),
                -float(edge.get("confidence") or 0),
                edge.get("relation") or "",
            ),
        )
        for edge in ranked_edges:
            source = nodes.get(edge["source_id"], {"name": edge["source_id"], "type": "?"})
            target = nodes.get(edge["target_id"], {"name": edge["target_id"], "type": "?"})
            evidence = edge.get("evidence") or ""
            evidence_part = f' Evidence: "{evidence}"' if evidence else ""
            paper_part = f" [paper_id: {edge['paper_id']}]" if edge.get("paper_id") else ""
            lines.append(
                f"- {source['name']} ({source['type']}) --{edge['relation']}--> "
                f"{target['name']} ({target['type']}){paper_part}.{evidence_part}"
            )
            if len(lines) >= max_lines:
                break
        return "\n".join(lines) if lines else "No graph relationships found."


def paper_ids_from_vector_hits(hits: Sequence[dict]) -> list[str]:
    out = []
    for hit in hits:
        metadata = hit.get("metadata") or {}
        doc_id = str(metadata.get("doc_id") or "")
        path = str(metadata.get("path") or "")
        if doc_id.startswith("structured:"):
            out.append(doc_id.split(":", 1)[1])
        else:
            match = re.search(r"knowledge_base/structured/([^/]+)/paper\.json", path)
            if match:
                out.append(match.group(1))
    return list(dict.fromkeys(out))


def hybrid_graph_context(query_text: str, vector_hits: Sequence[dict], depth: int = 1) -> dict:
    graph = KnowledgeGraph()
    paper_ids = paper_ids_from_vector_hits(vector_hits)
    paper_seeds = graph.paper_node_ids(paper_ids[:8])
    return graph.explore(query=query_text, seed_ids=paper_seeds, depth=depth, limit=70)


if __name__ == "__main__":
    print(json.dumps(KnowledgeGraph().stats(), indent=2))
