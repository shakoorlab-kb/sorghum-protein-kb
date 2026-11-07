"""Neo4j: the knowledge graph and the passage index in one store.

    (:Document)<-[:IN_DOCUMENT]-(:Chunk {page, text, embedding})
    (:Chunk)-[:MENTIONS]->(:Entity {type, name})
    (:Entity)-[:SUBJECT_OF]->(:Claim {relation, statement, evidence, page})-[:OBJECT]->(:Entity)
    (:Claim)-[:FROM_CHUNK]->(:Chunk)           every claim points at the text it came from
    (:Entity)-[:RELATES {type, n, docs}]->(:Entity)   claims aggregated, for traversal and maps
    (:Document)-[:DISCUSSES {n}]->(:Entity)
    (:Document)-[:AUTHORED_BY]->(:Author), -[:PUBLISHED_IN]->(:Journal), -[:CITES]->(:Document)

Claims are the provenance layer (one per statement, each with its verbatim quote,
page and chunk); RELATES edges are the navigation layer derived from them.
Queries are fixed templates with parameters; nothing from a user or a model is
ever interpolated into Cypher except whitelisted labels.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Iterable

from neo4j import GraphDatabase, Driver

from .config import settings

ENTITY_TYPES = [
    "GENE", "PROTEIN", "TRAIT", "PROCESS", "COMPOUND", "STRUCTURE", "PHENOTYPE",
    "GERMPLASM", "ORGANISM", "METHOD", "MARKER", "LOCATION", "FOOD",
]
RELATION_TYPES = [
    "INCREASES", "DECREASES", "IMPROVES", "IMPAIRS", "AFFECTS", "ASSOCIATED_WITH",
    "CORRELATES_WITH", "ENCODES", "REGULATES", "INTERACTS_WITH", "PART_OF", "CONTAINS",
    "HAS_TRAIT", "MEASURED_BY", "TARGETS", "DERIVED_FROM", "CAUSES", "NO_EFFECT_ON", "IS_A",
]

_driver: Driver | None = None
_lock = threading.Lock()


def driver() -> Driver:
    global _driver
    with _lock:
        if _driver is None:
            _driver = GraphDatabase.driver(
                settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password),
                max_connection_pool_size=64, notifications_min_severity="OFF",
            )
    return _driver


def run(query: str, **params) -> list[dict[str, Any]]:
    with driver().session() as s:
        return [r.data() for r in s.run(query, **params)]


def run_write(query: str, **params) -> None:
    with driver().session() as s:
        s.execute_write(lambda tx: tx.run(query, **params).consume())


def ping() -> bool:
    try:
        run("RETURN 1 AS ok")
        return True
    except Exception:
        return False


def ensure_schema() -> None:
    stmts = [
        "CREATE CONSTRAINT doc_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE",
        "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
        "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
        "CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (c:Claim) REQUIRE c.id IS UNIQUE",
        "CREATE CONSTRAINT author_name IF NOT EXISTS FOR (a:Author) REQUIRE a.name IS UNIQUE",
        "CREATE CONSTRAINT journal_name IF NOT EXISTS FOR (j:Journal) REQUIRE j.name IS UNIQUE",
        "CREATE INDEX chunk_doc IF NOT EXISTS FOR (c:Chunk) ON (c.doc_id)",
        "CREATE INDEX claim_doc IF NOT EXISTS FOR (c:Claim) ON (c.doc_id)",
        "CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)",
        "CREATE INDEX doc_doi IF NOT EXISTS FOR (d:Document) ON (d.doi)",
        (
            "CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS FOR (c:Chunk) ON (c.embedding) "
            "OPTIONS {indexConfig: {`vector.dimensions`: $dim, `vector.similarity_function`: 'cosine'}}"
        ),
        "CREATE FULLTEXT INDEX chunk_text IF NOT EXISTS FOR (c:Chunk) ON EACH [c.text] "
        "OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}",
        "CREATE FULLTEXT INDEX entity_names IF NOT EXISTS FOR (e:Entity) ON EACH [e.name, e.alias_text]",
        "CREATE FULLTEXT INDEX document_text IF NOT EXISTS FOR (d:Document) ON EACH [d.title, d.abstract, d.doi]",
    ]
    for q in stmts:
        run_write(q, dim=settings.embed_dim)


# ---- helpers -------------------------------------------------------------------

_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')
_STOP = {
    "the", "a", "an", "of", "and", "or", "in", "on", "for", "to", "is", "are", "what", "which",
    "how", "does", "do", "with", "by", "from", "that", "this", "be", "as", "at", "it", "its",
    "why", "when", "who", "whom", "there", "their", "about", "into", "than", "can", "has", "have",
}


def lucene_query(text: str, fuzzy: bool = False) -> str:
    words = [w for w in re.findall(r"[\w.\-]+", text) if w.lower() not in _STOP and len(w) > 1]
    if not words:
        return ""
    escaped = [_LUCENE_SPECIAL.sub(r"\\\1", w) for w in words[:24]]
    if fuzzy:
        escaped = [f"{w}~" if len(w) > 4 and w.isalpha() else w for w in escaped]
    return " OR ".join(escaped)


def norm_key(name: str) -> str:
    s = name.strip().lower()
    for a, b in (("α", "alpha"), ("β", "beta"), ("γ", "gamma"), ("δ", "delta"), ("κ", "kappa")):
        s = s.replace(a, b)
    s = re.sub(r"[‐-―]", "-", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"(?<=[a-z0-9])\s*-\s*(?=[a-z])", " ", s)
    # Symbol-font PDFs turn the Greek prefix into a Latin letter: "g-kafirin" is gamma-kafirin.
    s = re.sub(r"\b([abgd]) (kafirins?|zeins?|prolamins?|gliadins?)\b",
               lambda m: {"a": "alpha", "b": "beta", "g": "gamma", "d": "delta"}[m.group(1)] + " " + m.group(2), s)
    s = s.strip(" .,;:")
    return s


def _singular(key: str) -> str:
    head, _, last = key.rpartition(" ")
    if len(last) > 4 and last.isalpha():
        if last.endswith("ies"):
            last = last[:-3] + "y"
        elif last.endswith(("ches", "shes", "xes", "sses")):
            last = last[:-2]
        elif last.endswith("s") and not last.endswith(("ss", "us", "is", "os", "as")):
            last = last[:-1]
    return f"{head} {last}".strip()


def entity_id(etype: str, name: str) -> str:
    return f"{etype}:{_singular(norm_key(name))}"


# ---- writes ----------------------------------------------------------------------

def upsert_document(meta: dict) -> None:
    run_write(
        """
        MERGE (d:Document {id: $m.id})
        SET d.title = $m.title, d.origin = $m.origin, d.file_path = $m.file_path,
            d.file_type = $m.file_type, d.year = $m.year, d.doi = $m.doi, d.journal = $m.journal,
            d.authors = $m.authors, d.abstract = $m.abstract, d.doc_type = $m.doc_type,
            d.keywords = $m.keywords, d.url = $m.url, d.n_pages = $m.n_pages,
            d.content_hash = $m.content_hash, d.updated_at = datetime(),
            d.parent_id = $m.parent_id, d.text_path = $m.text_path
        WITH d
        OPTIONAL MATCH (d)-[r:AUTHORED_BY|PUBLISHED_IN|SUPPLEMENT_OF]->() DELETE r
        WITH d
        FOREACH (_ IN CASE WHEN $m.parent_id <> '' THEN [1] ELSE [] END |
            MERGE (p:Document {id: $m.parent_id})
            MERGE (d)-[:SUPPLEMENT_OF]->(p))
        WITH d
        FOREACH (i IN range(0, CASE WHEN $m.doc_type = 'supplement' THEN -1 ELSE size($m.authors) - 1 END) |
            MERGE (a:Author {name: $m.authors[i]})
            MERGE (d)-[:AUTHORED_BY {position: i + 1}]->(a))
        FOREACH (_ IN CASE WHEN $m.journal <> '' AND $m.doc_type <> 'supplement' THEN [1] ELSE [] END |
            MERGE (j:Journal {name: $m.journal})
            MERGE (d)-[:PUBLISHED_IN]->(j))
        """,
        m=meta,
    )


def set_document_state(doc_id: str, **props) -> None:
    run_write("MATCH (d:Document {id: $id}) SET d += $p", id=doc_id, p=props)


def replace_chunks(doc_id: str, chunks: list[dict]) -> list[str]:
    """Drop the document's chunks (and the claims hanging off them) and write new ones.
    Returns the entity ids whose relations need refreshing."""
    affected = clear_document_claims(doc_id)
    _delete_chunks(doc_id)
    for i in range(0, len(chunks), 200):
        run_write(
            """
            MATCH (d:Document {id: $doc})
            UNWIND $rows AS r
            CREATE (c:Chunk {id: r.id, doc_id: $doc, page: r.page, idx: r.idx, text: r.text})
            WITH c, r, d
            CALL db.create.setNodeVectorProperty(c, 'embedding', r.embedding)
            CREATE (c)-[:IN_DOCUMENT]->(d)
            """,
            doc=doc_id, rows=chunks[i:i + 200],
        )
    return affected


def _delete_chunks(doc_id: str) -> None:
    while True:
        rows = run(
            "MATCH (c:Chunk {doc_id: $id}) WITH c LIMIT 1000 DETACH DELETE c RETURN count(*) AS n",
            id=doc_id,
        )
        if not rows or rows[0]["n"] == 0:
            break


def clear_document_claims(doc_id: str) -> list[str]:
    """Delete a document's claims; return the entity ids whose relations need refreshing."""
    rows = run(
        """
        MATCH (c:Claim {doc_id: $id})
        OPTIONAL MATCH (s:Entity)-[:SUBJECT_OF]->(c)-[:OBJECT]->(o:Entity)
        WITH collect(DISTINCT s.id) + collect(DISTINCT o.id) AS ids, collect(c) AS cs
        FOREACH (c IN cs | DETACH DELETE c)
        RETURN ids
        """,
        id=doc_id,
    )
    run_write("MATCH (d:Document {id: $id})-[r:DISCUSSES]->() DELETE r", id=doc_id)
    return [i for i in (rows[0]["ids"] if rows else []) if i]


def write_extraction(doc_id: str, entities: list[dict], mentions: list[dict], claims: list[dict]) -> None:
    """entities: {id,type,name,aliases,description}; mentions: {chunk_id, entity_id};
    claims: {id, subject_id, object_id, relation, statement, evidence, page, chunk_id,
             confidence, verified, method, context}"""
    if entities:
        run_write(
            """
            UNWIND $rows AS r
            MERGE (e:Entity {id: r.id})
            ON CREATE SET e.type = r.type, e.name = r.name, e.aliases = [], e.description = '',
                          e.created_at = datetime()
            WITH e, r
            SET e.aliases = [a IN e.aliases WHERE NOT a IN r.aliases] + r.aliases,
                e.description = CASE WHEN e.description = '' AND r.description <> '' THEN r.description
                                     ELSE e.description END
            SET e.alias_text = reduce(s = '', a IN e.aliases | s + ' ' + a)
            """,
            rows=entities,
        )
    if mentions:
        run_write(
            """
            UNWIND $rows AS r
            MATCH (c:Chunk {id: r.chunk_id}), (e:Entity {id: r.entity_id})
            MERGE (c)-[:MENTIONS]->(e)
            """,
            rows=mentions,
        )
    if claims:
        run_write(
            """
            UNWIND $rows AS r
            MATCH (s:Entity {id: r.subject_id}), (o:Entity {id: r.object_id}), (d:Document {id: $doc})
            MERGE (c:Claim {id: r.id})
            SET c.relation = r.relation, c.statement = r.statement, c.evidence = r.evidence,
                c.page = r.page, c.doc_id = $doc, c.confidence = r.confidence,
                c.verified = r.verified, c.method = r.method, c.context = r.context,
                c.kind = coalesce(r.kind, 'finding'), c.value = coalesce(r.value, '')
            MERGE (s)-[:SUBJECT_OF]->(c)
            MERGE (c)-[:OBJECT]->(o)
            MERGE (c)-[:IN_DOCUMENT]->(d)
            WITH c, r
            OPTIONAL MATCH (ch:Chunk {id: r.chunk_id})
            FOREACH (_ IN CASE WHEN ch IS NULL THEN [] ELSE [1] END | MERGE (c)-[:FROM_CHUNK]->(ch))
            """,
            doc=doc_id, rows=claims,
        )


def write_document_profile(doc_id: str, profile: dict, studies: list[dict]) -> None:
    """Document-level knowledge: summary, study type, key findings, and what it studies."""
    run_write(
        """
        MATCH (d:Document {id: $id})
        SET d.summary = $p.summary, d.study_type = $p.study_type, d.key_findings = $p.key_findings
        WITH d
        OPTIONAL MATCH (d)-[r:STUDIES]->() DELETE r
        WITH DISTINCT d
        UNWIND $studies AS s
        MATCH (e:Entity {id: s.entity_id})
        MERGE (d)-[r:STUDIES]->(e) SET r.role = s.role
        """,
        id=doc_id, p=profile, studies=studies,
    )


def refresh_document_links(doc_id: str) -> None:
    """DISCUSSES edges and entity mention counts for one document."""
    run_write(
        """
        MATCH (d:Document {id: $id})<-[:IN_DOCUMENT]-(:Chunk)-[:MENTIONS]->(e:Entity)
        WITH d, e, count(*) AS n
        MERGE (d)-[r:DISCUSSES]->(e) SET r.n = n
        """,
        id=doc_id,
    )


def refresh_relations(entity_ids: Iterable[str] | None = None) -> None:
    """Rebuild RELATES aggregates from claims (for the given entities, or all)."""
    ids = list(entity_ids) if entity_ids is not None else None
    if ids is not None and not ids:
        return
    run_write(
        """
        MATCH (s:Entity)-[r:RELATES]->(o:Entity)
        WHERE $ids IS NULL OR s.id IN $ids OR o.id IN $ids
        DELETE r
        """,
        ids=ids,
    )
    run_write(
        """
        MATCH (s:Entity)-[:SUBJECT_OF]->(c:Claim)-[:OBJECT]->(o:Entity)
        WHERE $ids IS NULL OR s.id IN $ids OR o.id IN $ids
        WITH s, o, c.relation AS t, count(c) AS n, collect(DISTINCT c.doc_id) AS docs,
             max(c.confidence) AS conf, sum(CASE WHEN c.verified THEN 1 ELSE 0 END) AS verified
        MERGE (s)-[r:RELATES {type: t}]->(o)
        SET r.n = n, r.docs = docs, r.n_docs = size(docs), r.confidence = conf, r.verified = verified
        """,
        ids=ids,
    )
    run_write(
        """
        MATCH (e:Entity) WHERE $ids IS NULL OR e.id IN $ids
        OPTIONAL MATCH (e)<-[:MENTIONS]-(c:Chunk)
        WITH e, count(DISTINCT c) AS mentions, count(DISTINCT c.doc_id) AS docs
        OPTIONAL MATCH (e)-[r:RELATES]-()
        WITH e, mentions, docs, count(r) AS degree
        SET e.mentions = mentions, e.n_docs = docs, e.degree = degree
        """,
        ids=ids,
    )


def prune_orphan_entities() -> None:
    run_write("MATCH (e:Entity) WHERE NOT (e)--() DELETE e")


def write_citations(pairs: list[dict]) -> None:
    run_write(
        """
        UNWIND $rows AS r
        MATCH (a:Document {id: r.src}), (b:Document {id: r.dst})
        WHERE a <> b AND coalesce(a.doc_type, '') <> 'supplement' AND coalesce(b.doc_type, '') <> 'supplement'
        MERGE (a)-[:CITES]->(b)
        """,
        rows=pairs,
    )


def delete_document(doc_id: str) -> None:
    ids = clear_document_claims(doc_id)
    _delete_chunks(doc_id)
    run_write("MATCH (d:Document {id: $id}) DETACH DELETE d", id=doc_id)
    refresh_relations(ids)
    prune_orphan_entities()


# ---- reads -----------------------------------------------------------------------

def stats() -> dict:
    counts = run(
        """
        CALL { MATCH (d:Document) RETURN count(d) AS documents }
        CALL { MATCH (c:Chunk) RETURN count(c) AS chunks }
        CALL { MATCH (e:Entity) RETURN count(e) AS entities }
        CALL { MATCH (c:Claim) RETURN count(c) AS claims }
        CALL { MATCH ()-[r:RELATES]->() RETURN count(r) AS relations }
        CALL { MATCH (a:Author) RETURN count(a) AS authors }
        CALL { MATCH ()-[r:CITES]->() RETURN count(r) AS citations }
        CALL { MATCH (c:Claim {verified: true}) RETURN count(c) AS verified_claims }
        CALL { MATCH (d:Document) WHERE d.extracted_at IS NOT NULL RETURN count(d) AS extracted_documents }
        CALL { MATCH (d:Document) RETURN sum(coalesce(d.n_pages, 0)) AS pages }
        RETURN documents, chunks, entities, claims, relations, authors, citations,
               verified_claims, extracted_documents, pages
        """
    )[0]
    by_type = run("MATCH (e:Entity) RETURN e.type AS type, count(*) AS n ORDER BY n DESC")
    by_rel = run("MATCH (c:Claim) RETURN c.relation AS type, count(*) AS n ORDER BY n DESC")
    by_origin = run("MATCH (d:Document) RETURN d.origin AS origin, d.doc_type AS doc_type, count(*) AS n")
    years = run(
        "MATCH (d:Document) WHERE d.year IS NOT NULL RETURN d.year AS year, count(*) AS n ORDER BY year"
    )
    return {**counts, "entity_types": by_type, "relation_types": by_rel, "origins": by_origin, "years": years}


def entity_type_counts() -> dict[str, int]:
    return {r["t"]: r["n"] for r in run("MATCH (e:Entity) RETURN e.type AS t, count(*) AS n")}


def top_entities(limit: int = 30, etype: str | None = None) -> list[dict]:
    return run(
        """
        MATCH (e:Entity) WHERE $t IS NULL OR e.type = $t
        RETURN e.id AS id, e.name AS name, e.type AS type, coalesce(e.degree, 0) AS degree,
               coalesce(e.n_docs, 0) AS n_docs, coalesce(e.mentions, 0) AS mentions
        ORDER BY n_docs DESC, degree DESC LIMIT $limit
        """,
        t=etype, limit=limit,
    )


def search_entities(q: str, limit: int = 20, types: list[str] | None = None) -> list[dict]:
    q = q.strip()
    if not q:
        return []
    lq = lucene_query(q, fuzzy=True)
    rows: list[dict] = []
    if lq:
        rows = run(
            """
            CALL db.index.fulltext.queryNodes('entity_names', $lq, {limit: 80}) YIELD node, score
            WHERE $types IS NULL OR node.type IN $types
            RETURN node.id AS id, node.name AS name, node.type AS type, node.aliases AS aliases,
                   coalesce(node.n_docs, 0) AS n_docs, coalesce(node.degree, 0) AS degree, score
            """,
            lq=lq, types=types,
        )
    key = norm_key(q)
    exact = run(
        """
        MATCH (e:Entity) WHERE (e.id ENDS WITH (':' + $k) OR toLower(e.name) CONTAINS $k)
          AND ($types IS NULL OR e.type IN $types)
        RETURN e.id AS id, e.name AS name, e.type AS type, e.aliases AS aliases,
               coalesce(e.n_docs, 0) AS n_docs, coalesce(e.degree, 0) AS degree,
               CASE WHEN e.id ENDS WITH (':' + $k) THEN 100.0 ELSE 5.0 END AS score
        ORDER BY score DESC, n_docs DESC LIMIT 30
        """,
        k=key, types=types,
    )
    best: dict[str, dict] = {}
    for r in exact + rows:
        cur = best.get(r["id"])
        r["score"] = float(r["score"]) + 0.02 * min(r["n_docs"], 50)
        if cur is None or r["score"] > cur["score"]:
            best[r["id"]] = r
    return sorted(best.values(), key=lambda r: -r["score"])[:limit]


def get_entity(eid: str) -> dict | None:
    rows = run(
        """
        MATCH (e:Entity {id: $id})
        OPTIONAL MATCH (d:Document)-[r:DISCUSSES]->(e)
        WITH e, d, r ORDER BY r.n DESC
        WITH e, collect({id: d.id, title: d.title, year: d.year, n: r.n})[..25] AS docs
        RETURN e {.id, .name, .type, .aliases, .description, .mentions, .n_docs, .degree} AS entity, docs
        """,
        id=eid,
    )
    if not rows:
        return None
    out = rows[0]["entity"]
    out["documents"] = [d for d in rows[0]["docs"] if d["id"]]
    out["claims"] = claims_for_entities([eid], limit=40)
    return out


def claims_for_entities(ids: list[str], limit: int = 30, doc_ids: list[str] | None = None,
                        relation: str | None = None) -> list[dict]:
    return run(
        """
        MATCH (s:Entity)-[:SUBJECT_OF]->(c:Claim)-[:OBJECT]->(o:Entity)
        WHERE (s.id IN $ids OR o.id IN $ids)
          AND ($docs IS NULL OR c.doc_id IN $docs)
          AND ($rel IS NULL OR c.relation = $rel)
        MATCH (c)-[:IN_DOCUMENT]->(d:Document)
        OPTIONAL MATCH (c)-[:FROM_CHUNK]->(ch:Chunk)
        WITH c, s, o, d, ch,
             (CASE WHEN s.id IN $ids AND o.id IN $ids THEN 2.0 ELSE 0.0 END)
             + c.confidence + (CASE WHEN c.verified THEN 0.5 ELSE 0.0 END)
             + (CASE coalesce(c.kind, 'finding') WHEN 'finding' THEN 0.4 WHEN 'background' THEN 0.0 ELSE 0.1 END) AS rank
        RETURN c.id AS id, c.relation AS relation, c.statement AS statement, c.evidence AS evidence,
               c.page AS page, c.confidence AS confidence, c.verified AS verified, c.context AS context,
               c.method AS method, ch.id AS chunk_id, c.kind AS kind, c.value AS value,
               s {.id, .name, .type} AS subject, o {.id, .name, .type} AS object,
               d {.id, .title, .year, .doi, .file_type} AS document
        ORDER BY rank DESC, d.year DESC LIMIT $limit
        """,
        ids=ids, limit=limit, docs=doc_ids, rel=relation,
    )


def claims_for_documents(doc_ids: list[str], per_doc: int = 6) -> list[dict]:
    """The strongest claims of each given document (findings first), in the same shape as the
    other claim queries, for papers a question names directly."""
    out: list[dict] = []
    for did in doc_ids:
        out += run(
            """
            MATCH (s:Entity)-[:SUBJECT_OF]->(c:Claim {doc_id: $id})-[:OBJECT]->(o:Entity)
            MATCH (c)-[:IN_DOCUMENT]->(d:Document)
            OPTIONAL MATCH (c)-[:FROM_CHUNK]->(ch:Chunk)
            RETURN c.id AS id, c.relation AS relation, c.statement AS statement, c.evidence AS evidence,
                   c.page AS page, c.confidence AS confidence, c.verified AS verified, c.context AS context,
                   c.method AS method, ch.id AS chunk_id, c.kind AS kind, c.value AS value,
                   s {.id, .name, .type} AS subject, o {.id, .name, .type} AS object,
                   d {.id, .title, .year, .doi, .file_type, .authors} AS document
            ORDER BY (c.kind = 'finding') DESC, (c.value <> '') DESC, c.confidence DESC LIMIT $n
            """,
            id=did, n=per_doc,
        )
    return out


def claims_between(a: str, b: str, limit: int = 30) -> list[dict]:
    return run(
        """
        MATCH (s:Entity)-[:SUBJECT_OF]->(c:Claim)-[:OBJECT]->(o:Entity)
        WHERE (s.id = $a AND o.id = $b) OR (s.id = $b AND o.id = $a)
        MATCH (c)-[:IN_DOCUMENT]->(d:Document)
        OPTIONAL MATCH (c)-[:FROM_CHUNK]->(ch:Chunk)
        RETURN c.id AS id, c.relation AS relation, c.statement AS statement, c.evidence AS evidence,
               c.page AS page, c.confidence AS confidence, c.verified AS verified, c.context AS context,
               c.method AS method, ch.id AS chunk_id, c.kind AS kind, c.value AS value,
               s {.id, .name, .type} AS subject, o {.id, .name, .type} AS object,
               d {.id, .title, .year, .doi, .file_type} AS document
        ORDER BY c.verified DESC, c.confidence DESC LIMIT $limit
        """,
        a=a, b=b, limit=limit,
    )


def _graph_payload(nodes: dict[str, dict], edges: dict[str, dict]) -> dict:
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def neighborhood(eid: str, depth: int = 1, limit: int = 60, min_support: int = 1,
                 types: list[str] | None = None, include_documents: bool = False) -> dict:
    depth = 2 if depth >= 2 else 1
    rows = run(
        """
        MATCH (c:Entity {id: $id})
        OPTIONAL MATCH (c)-[r:RELATES]-(n:Entity)
        WHERE r.n >= $min AND ($types IS NULL OR n.type IN $types)
        WITH c, n, r ORDER BY r.n_docs DESC, r.n DESC, n.n_docs DESC LIMIT $limit
        RETURN c {.id, .name, .type, .n_docs, .degree} AS center,
               collect(DISTINCT n {.id, .name, .type, .n_docs, .degree}) AS ns,
               collect(DISTINCT {source: startNode(r).id, target: endNode(r).id, type: r.type,
                                 n: r.n, n_docs: r.n_docs, verified: r.verified}) AS rs
        """,
        id=eid, min=min_support, limit=limit, types=types,
    )
    if not rows or not rows[0]["center"]:
        return {"nodes": [], "edges": [], "center": eid}
    nodes = {rows[0]["center"]["id"]: {**rows[0]["center"], "kind": "entity", "ring": 0}}
    for n in rows[0]["ns"]:
        if n and n.get("id"):
            nodes.setdefault(n["id"], {**n, "kind": "entity", "ring": 1})
    edges: dict[str, dict] = {}
    for e in rows[0]["rs"]:
        if e.get("source") and e.get("target"):
            edges[f"{e['source']}|{e['type']}|{e['target']}"] = e
    if depth == 2 and len(nodes) > 1:
        ring1 = [k for k, v in nodes.items() if v["ring"] == 1][:25]
        more = run(
            """
            UNWIND $ids AS nid
            MATCH (a:Entity {id: nid})-[r:RELATES]-(b:Entity)
            WHERE b.id <> $center AND r.n >= $min AND ($types IS NULL OR b.type IN $types)
            WITH a, b, r ORDER BY r.n_docs DESC, r.n DESC
            WITH a, collect({b: b {.id, .name, .type, .n_docs, .degree},
                             e: {source: startNode(r).id, target: endNode(r).id, type: r.type,
                                 n: r.n, n_docs: r.n_docs, verified: r.verified}})[..4] AS picks
            UNWIND picks AS p RETURN p.b AS b, p.e AS e
            """,
            ids=ring1, center=eid, min=min_support, types=types,
        )
        for r in more:
            nodes.setdefault(r["b"]["id"], {**r["b"], "kind": "entity", "ring": 2})
            e = r["e"]
            edges[f"{e['source']}|{e['type']}|{e['target']}"] = e
        # Links among already-shown nodes make the map honest about how they connect.
        ids = list(nodes)
        for e in run(
            """
            MATCH (a:Entity)-[r:RELATES]->(b:Entity) WHERE a.id IN $ids AND b.id IN $ids AND r.n >= $min
            RETURN a.id AS source, b.id AS target, r.type AS type, r.n AS n, r.n_docs AS n_docs,
                   r.verified AS verified LIMIT 400
            """,
            ids=ids, min=min_support,
        ):
            edges[f"{e['source']}|{e['type']}|{e['target']}"] = e
    if include_documents:
        for r in run(
            """
            MATCH (d:Document)-[r:DISCUSSES]->(e:Entity {id: $id})
            RETURN d {.id, .title, .year, .doc_type} AS d, r.n AS n ORDER BY r.n DESC LIMIT 12
            """,
            id=eid,
        ):
            did = "doc:" + r["d"]["id"]
            nodes[did] = {**r["d"], "id": did, "doc_id": r["d"]["id"], "name": r["d"]["title"],
                          "type": "DOCUMENT", "kind": "document", "ring": 1}
            edges[f"{did}|DISCUSSES|{eid}"] = {"source": did, "target": eid, "type": "DISCUSSES", "n": r["n"]}
    return {**_graph_payload(nodes, edges), "center": eid}


def subgraph(entity_ids: list[str], doc_ids: list[str] | None = None, expand: int = 2) -> dict:
    """Map for an answer: the given entities, the relations among them, a few strongest
    neighbours of each, and the cited documents linked to what they discuss."""
    entity_ids = list(dict.fromkeys(entity_ids))[:30]
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    for r in run(
        "MATCH (e:Entity) WHERE e.id IN $ids RETURN e {.id, .name, .type, .n_docs, .degree} AS e",
        ids=entity_ids,
    ):
        nodes[r["e"]["id"]] = {**r["e"], "kind": "entity", "focus": True}
    if expand:
        for r in run(
            """
            UNWIND $ids AS nid
            MATCH (a:Entity {id: nid})-[r:RELATES]-(b:Entity)
            WITH a, b, r ORDER BY r.n_docs DESC, r.n DESC
            WITH a, collect({b: b {.id, .name, .type, .n_docs, .degree},
                             e: {source: startNode(r).id, target: endNode(r).id, type: r.type,
                                 n: r.n, n_docs: r.n_docs, verified: r.verified}})[..$k] AS picks
            UNWIND picks AS p RETURN p.b AS b, p.e AS e
            """,
            ids=entity_ids, k=expand,
        ):
            nodes.setdefault(r["b"]["id"], {**r["b"], "kind": "entity"})
            e = r["e"]
            edges[f"{e['source']}|{e['type']}|{e['target']}"] = e
    ids = list(nodes)
    for e in run(
        """
        MATCH (a:Entity)-[r:RELATES]->(b:Entity) WHERE a.id IN $ids AND b.id IN $ids
        RETURN a.id AS source, b.id AS target, r.type AS type, r.n AS n, r.n_docs AS n_docs,
               r.verified AS verified LIMIT 300
        """,
        ids=ids,
    ):
        edges[f"{e['source']}|{e['type']}|{e['target']}"] = e
    if doc_ids:
        for r in run(
            """
            MATCH (d:Document)-[r:DISCUSSES]->(e:Entity)
            WHERE d.id IN $docs AND e.id IN $ids
            RETURN d {.id, .title, .year, .doc_type} AS d, e.id AS eid, r.n AS n
            """,
            docs=list(dict.fromkeys(doc_ids))[:15], ids=ids,
        ):
            did = "doc:" + r["d"]["id"]
            nodes.setdefault(did, {**r["d"], "id": did, "doc_id": r["d"]["id"], "name": r["d"]["title"],
                                   "type": "DOCUMENT", "kind": "document"})
            edges[f"{did}|DISCUSSES|{r['eid']}"] = {"source": did, "target": r["eid"], "type": "DISCUSSES", "n": r["n"]}
    return _graph_payload(nodes, edges)


def paths(a: str, b: str, max_hops: int = 4, limit: int = 5) -> dict:
    max_hops = max(1, min(int(max_hops), 5))
    rows = run(
        f"""
        MATCH (x:Entity {{id: $a}}), (y:Entity {{id: $b}})
        MATCH p = allShortestPaths((x)-[:RELATES*..{max_hops}]-(y))
        RETURN [n IN nodes(p) | n {{.id, .name, .type, .n_docs, .degree}}] AS ns,
               [r IN relationships(p) | {{source: startNode(r).id, target: endNode(r).id, type: r.type,
                                          n: r.n, n_docs: r.n_docs, verified: r.verified}}] AS rs
        LIMIT $limit
        """,
        a=a, b=b, limit=limit,
    )
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    path_list = []
    for r in rows:
        ids = [n["id"] for n in r["ns"]]
        if ids in path_list:  # parallel relation types give the same node sequence
            continue
        path_list.append(ids)
        for n in r["ns"]:
            nodes.setdefault(n["id"], {**n, "kind": "entity", "focus": n["id"] in (a, b)})
        for e in r["rs"]:
            edges[f"{e['source']}|{e['type']}|{e['target']}"] = e
    return {**_graph_payload(nodes, edges), "paths": path_list}


def document_graph(doc_id: str, limit: int = 60) -> dict:
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    d = run("MATCH (d:Document {id: $id}) RETURN d {.id, .title, .year, .doc_type} AS d", id=doc_id)
    if not d:
        return {"nodes": [], "edges": []}
    did = "doc:" + doc_id
    nodes[did] = {**d[0]["d"], "id": did, "doc_id": doc_id, "name": d[0]["d"]["title"],
                  "type": "DOCUMENT", "kind": "document", "focus": True}
    claim_rows = run(
        """
        MATCH (s:Entity)-[:SUBJECT_OF]->(c:Claim {doc_id: $id})-[:OBJECT]->(o:Entity)
        RETURN s {.id, .name, .type, .n_docs} AS s, o {.id, .name, .type, .n_docs} AS o,
               c.relation AS type, count(c) AS n, max(toInteger(c.verified)) AS verified
        ORDER BY n DESC LIMIT $limit
        """,
        id=doc_id, limit=limit * 2,
    )
    for r in claim_rows:
        for n in (r["s"], r["o"]):
            nodes.setdefault(n["id"], {**n, "kind": "entity"})
        edges[f"{r['s']['id']}|{r['type']}|{r['o']['id']}"] = {
            "source": r["s"]["id"], "target": r["o"]["id"], "type": r["type"], "n": r["n"],
            "verified": r["verified"],
        }
    for r in run(
        """
        MATCH (:Document {id: $id})-[r:DISCUSSES]->(e:Entity)
        RETURN e {.id, .name, .type, .n_docs} AS e, r.n AS n ORDER BY r.n DESC LIMIT $limit
        """,
        id=doc_id, limit=limit,
    ):
        nodes.setdefault(r["e"]["id"], {**r["e"], "kind": "entity"})
        edges[f"{did}|DISCUSSES|{r['e']['id']}"] = {"source": did, "target": r["e"]["id"], "type": "DISCUSSES", "n": r["n"]}
    for r in run(
        """
        MATCH (a:Document {id: $id})-[:CITES]-(b:Document)
        RETURN b {.id, .title, .year, .doc_type} AS b,
               exists((a)-[:CITES]->(b)) AS outgoing LIMIT 25
        """,
        id=doc_id,
    ):
        bid = "doc:" + r["b"]["id"]
        nodes.setdefault(bid, {**r["b"], "id": bid, "doc_id": r["b"]["id"], "name": r["b"]["title"],
                               "type": "DOCUMENT", "kind": "document"})
        src, dst = (did, bid) if r["outgoing"] else (bid, did)
        edges[f"{src}|CITES|{dst}"] = {"source": src, "target": dst, "type": "CITES", "n": 1}
    return _graph_payload(nodes, edges)


def overview(limit: int = 70, etype: str | None = None) -> dict:
    """The most connected part of the graph, for the explorer's landing view. With a type:
    the most widely discussed entities of that type and their strongest links."""
    if etype:
        top = top_entities(min(limit, 30), etype)
        return subgraph([t["id"] for t in top], expand=2)
    top = top_entities(limit, etype)
    ids = [t["id"] for t in top]
    nodes = {t["id"]: {**t, "kind": "entity"} for t in top}
    edges = {}
    for e in run(
        """
        MATCH (a:Entity)-[r:RELATES]->(b:Entity) WHERE a.id IN $ids AND b.id IN $ids
        RETURN a.id AS source, b.id AS target, r.type AS type, r.n AS n, r.n_docs AS n_docs,
               r.verified AS verified ORDER BY r.n_docs DESC LIMIT 400
        """,
        ids=ids,
    ):
        edges[f"{e['source']}|{e['type']}|{e['target']}"] = e
    return _graph_payload(nodes, edges)


# ---- passages -----------------------------------------------------------------------

def vector_search(embedding: list[float], k: int = 20, doc_ids: list[str] | None = None) -> list[dict]:
    return run(
        """
        CALL db.index.vector.queryNodes('chunk_embedding', $k, $emb) YIELD node, score
        WHERE $docs IS NULL OR node.doc_id IN $docs
        RETURN node.id AS id, score
        """,
        k=k if not doc_ids else k * 5, emb=embedding, docs=doc_ids,
    )


def fulltext_search(text: str, k: int = 20, doc_ids: list[str] | None = None) -> list[dict]:
    lq = lucene_query(text)
    if not lq:
        return []
    return run(
        """
        CALL db.index.fulltext.queryNodes('chunk_text', $lq, {limit: $k}) YIELD node, score
        WHERE $docs IS NULL OR node.doc_id IN $docs
        RETURN node.id AS id, score
        """,
        lq=lq, k=k if not doc_ids else k * 5, docs=doc_ids,
    )


def chunks_by_ids(ids: list[str]) -> dict[str, dict]:
    rows = run(
        """
        MATCH (c:Chunk)-[:IN_DOCUMENT]->(d:Document) WHERE c.id IN $ids
        OPTIONAL MATCH (c)-[:MENTIONS]->(e:Entity)
        WITH c, d, collect(e {.id, .name, .type})[..12] AS ents
        RETURN c.id AS id, c.text AS text, c.page AS page, c.idx AS idx,
               d {.id, .title, .year, .doi, .journal, .authors, .file_type, .doc_type, .origin} AS document,
               ents AS entities
        """,
        ids=ids,
    )
    return {r["id"]: r for r in rows}


def documents_by_first_author(surname: str, year: int | None = None) -> list[str]:
    rows = run(
        """
        MATCH (d:Document)
        WHERE size(d.authors) > 0 AND toLower(d.authors[0]) ENDS WITH $s
          AND ($y IS NULL OR d.year = $y)
        RETURN d.id AS id ORDER BY d.year DESC LIMIT 6
        """,
        s=surname.lower(), y=year,
    )
    return [r["id"] for r in rows]


def chunks_mentioning(entity_ids: list[str], limit: int = 12) -> list[str]:
    rows = run(
        """
        MATCH (c:Chunk)-[:MENTIONS]->(e:Entity) WHERE e.id IN $ids
        WITH c, count(DISTINCT e) AS hits ORDER BY hits DESC LIMIT $limit
        RETURN c.id AS id, hits
        """,
        ids=entity_ids, limit=limit,
    )
    return [r["id"] for r in rows if r["hits"] >= min(2, len(entity_ids))]


def search_documents(q: str = "", limit: int = 50, offset: int = 0, origin: str | None = None,
                     doc_type: str | None = None) -> list[dict]:
    fields = """d.id AS id, d.title AS title, d.year AS year, d.doi AS doi, d.journal AS journal,
               d.authors[..6] AS authors, d.origin AS origin, d.doc_type AS doc_type,
               d.file_type AS file_type, d.n_pages AS n_pages, d.status AS status,
               d.extracted_at IS NOT NULL AS extracted,
               COUNT { (d)<-[:IN_DOCUMENT]-(:Claim) } AS n_claims,
               COUNT { (d)-[:DISCUSSES]->() } AS n_entities"""
    lq = lucene_query(q, fuzzy=False) if q.strip() else ""
    if lq:
        return run(
            f"""
            CALL db.index.fulltext.queryNodes('document_text', $lq, {{limit: 300}}) YIELD node AS d, score
            WHERE ($origin IS NULL OR d.origin = $origin)
              AND (($dt IS NULL AND d.doc_type <> 'supplement') OR d.doc_type = $dt)
            RETURN {fields}, score ORDER BY score DESC SKIP $offset LIMIT $limit
            """,
            lq=lq, origin=origin, dt=doc_type, offset=offset, limit=limit,
        )
    return run(
        f"""
        MATCH (d:Document)
        WHERE ($origin IS NULL OR d.origin = $origin)
          AND (($dt IS NULL AND d.doc_type <> 'supplement') OR d.doc_type = $dt)
        RETURN {fields} ORDER BY d.updated_at DESC, d.year DESC SKIP $offset LIMIT $limit
        """,
        origin=origin, dt=doc_type, offset=offset, limit=limit,
    )


def get_document(doc_id: str) -> dict | None:
    rows = run(
        """
        MATCH (d:Document {id: $id})
        RETURN d {.*, updated_at: toString(d.updated_at), extracted_at: toString(d.extracted_at)} AS d,
               COUNT { (d)<-[:IN_DOCUMENT]-(:Chunk) } AS n_chunks,
               COUNT { (d)<-[:IN_DOCUMENT]-(:Claim) } AS n_claims,
               COUNT { (d)-[:CITES]->() } AS n_cites, COUNT { (d)<-[:CITES]-() } AS n_cited_by
        """,
        id=doc_id,
    )
    if not rows:
        return None
    r = rows[0]
    out = {**r["d"], "n_chunks": r["n_chunks"], "n_claims": r["n_claims"],
           "n_cites": r["n_cites"], "n_cited_by": r["n_cited_by"]}
    out["supplements"] = run(
        """
        MATCH (s:Document)-[:SUPPLEMENT_OF]->(:Document {id: $id})
        RETURN s.id AS id, s.title AS title, s.n_pages AS n_pages ORDER BY s.title
        """,
        id=doc_id,
    )
    out["parent"] = (run(
        "MATCH (:Document {id: $id})-[:SUPPLEMENT_OF]->(p:Document) RETURN p.id AS id, p.title AS title",
        id=doc_id,
    ) or [None])[0]
    out["top_entities"] = run(
        """
        MATCH (:Document {id: $id})-[r:DISCUSSES]->(e:Entity)
        RETURN e.id AS id, e.name AS name, e.type AS type, r.n AS n ORDER BY r.n DESC LIMIT 30
        """,
        id=doc_id,
    )
    return out


def document_claims(doc_id: str, limit: int = 200) -> list[dict]:
    return run(
        """
        MATCH (s:Entity)-[:SUBJECT_OF]->(c:Claim {doc_id: $id})-[:OBJECT]->(o:Entity)
        OPTIONAL MATCH (c)-[:FROM_CHUNK]->(ch:Chunk)
        RETURN c.id AS id, c.relation AS relation, c.statement AS statement, c.evidence AS evidence,
               c.page AS page, c.confidence AS confidence, c.verified AS verified, c.context AS context,
               c.method AS method, ch.id AS chunk_id, c.kind AS kind, c.value AS value,
               s {.id, .name, .type} AS subject, o {.id, .name, .type} AS object
        ORDER BY c.page, c.confidence DESC LIMIT $limit
        """,
        id=doc_id, limit=limit,
    )


def get_claim(cid: str) -> dict | None:
    rows = run(
        """
        MATCH (s:Entity)-[:SUBJECT_OF]->(c:Claim {id: $id})-[:OBJECT]->(o:Entity)
        MATCH (c)-[:IN_DOCUMENT]->(d:Document)
        OPTIONAL MATCH (c)-[:FROM_CHUNK]->(ch:Chunk)
        RETURN c.id AS id, c.relation AS relation, c.statement AS statement, c.evidence AS evidence,
               c.page AS page, c.confidence AS confidence, c.verified AS verified, c.context AS context,
               c.method AS method, ch.id AS chunk_id, c.kind AS kind, c.value AS value,
               s {.id, .name, .type} AS subject, o {.id, .name, .type} AS object,
               d {.id, .title, .year, .doi, .file_type} AS document
        """,
        id=cid,
    )
    return rows[0] if rows else None


def documents_state() -> dict[str, dict]:
    return {
        r["id"]: r for r in run(
            "MATCH (d:Document) RETURN d.id AS id, d.content_hash AS content_hash, "
            "d.extracted_at IS NOT NULL AS extracted, d.extract_model AS extract_model, d.status AS status"
        )
    }


def chunks_for_document(doc_id: str) -> list[dict]:
    return run(
        "MATCH (c:Chunk {doc_id: $id}) RETURN c.id AS id, c.page AS page, c.idx AS idx, c.text AS text "
        "ORDER BY c.page, c.idx",
        id=doc_id,
    )
