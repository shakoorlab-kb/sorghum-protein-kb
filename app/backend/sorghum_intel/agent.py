"""The chat agent: a LangGraph state machine over the Neo4j knowledge graph.

    plan ──> link ──> retrieve ──(too little evidence?)──> broaden ──> retrieve
                                   └──────────────> answer ──> finalize

plan      the model rewrites the question (using the chat history) into search
          queries and the entities it is about
link      entities are resolved to graph nodes (full-text over names and aliases)
retrieve  passages: hybrid vector + full-text search per query, plus passages that
          mention the linked entities together; graph: claims about the linked
          entities and claims along the shortest paths between them
answer    streamed; every statement cites numbered evidence [n]
finalize  citations the model invented are dropped; each cited passage gets the
          sentences that support the answer, which the viewer highlights on the page

Every step emits an event (stream_mode="custom") the UI shows while it works.
"""
from __future__ import annotations

import re
import time
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from . import charts, graphdb, llm, retrieval
from .config import settings


class State(TypedDict, total=False):
    question: str
    history: list[dict]
    standalone: str
    queries: list[str]
    entity_names: list[str]
    linked: list[dict]
    passages: list[dict]
    claims: list[dict]
    paths: list[list[str]]
    rounds: int
    answer: str
    result: dict
    t0: float
    steps: list[dict]
    wants_chart: bool
    evidence_text: str
    items: list[dict]
    chart: dict | None


def _emit(kind: str, **data: Any) -> None:
    try:
        get_stream_writer()({"type": kind, **data})
    except Exception:
        pass


def _step(state: State, name: str, detail: str, **extra) -> list[dict]:
    step = {"step": name, "detail": detail, "ms": int((time.time() - state["t0"]) * 1000), **extra}
    _emit("step", **step)
    return [*state.get("steps", []), step]


# ---- nodes ---------------------------------------------------------------------------

PLAN_SYSTEM = """You plan literature searches for a sorghum grain protein research assistant
(kafirins, digestibility, grain quality, processing, genetics, breeding, nutrition).
Given the chat so far and the new question, return JSON:
{"standalone": "the question rewritten to stand alone (resolve 'it', 'those', 'that gene')",
 "queries": ["2-5 short search queries covering different angles, with synonyms/abbreviations. If the question has several parts or names specific studies, authors or genotypes, give each part its own query"],
 "entities": ["the key scientific entities (genes, proteins, traits, processes, compounds, germplasm) named or implied"],
 "chart": true if the user asks for a plot, chart, graph of values or other visualisation, else false}
When a chart is wanted, include a query aimed at the data table that holds the numbers
(e.g. "supplementary table accession protein digestibility values")."""


def plan(state: State) -> dict:
    q = state["question"]
    hist = state.get("history") or []
    convo = "\n".join(f"{m['role']}: {m['content'][:600]}" for m in hist[-6:])
    try:
        data = llm.complete_json(
            [{"role": "system", "content": PLAN_SYSTEM},
             {"role": "user", "content": f"Chat so far:\n{convo or '(none)'}\n\nNew question: {q}"}],
            purpose="plan", max_tokens=600,
        )
    except Exception as exc:
        data = {"standalone": q, "queries": [q], "entities": [], "error": str(exc)}
    standalone = str(data.get("standalone") or q).strip()
    queries = [str(x).strip() for x in (data.get("queries") or []) if str(x).strip()][:5]
    if standalone not in queries:
        queries.insert(0, standalone)
    names = [str(x).strip() for x in (data.get("entities") or []) if str(x).strip()][:8]
    steps = _step(state, "plan", f"{len(queries)} searches; entities: {', '.join(names) or 'none'}",
                  queries=queries)
    wants = bool(data.get("chart")) or charts.wants_chart(q)
    return {"standalone": standalone, "queries": queries[:6], "entity_names": names, "steps": steps,
            "wants_chart": wants}


def link(state: State) -> dict:
    linked: dict[str, dict] = {}
    for name in state.get("entity_names", []):
        hits = graphdb.search_entities(name, limit=3)
        if not hits:
            continue
        top = hits[0]
        key = graphdb.norm_key(name)
        exactish = top["id"].endswith(":" + key) or key in graphdb.norm_key(top["name"]) \
            or graphdb.norm_key(top["name"]) in key
        if exactish or top["score"] > 3.0:
            linked.setdefault(top["id"], {**top, "query": name})
    # Entities named verbatim in the question that the planner did not list.
    qn = graphdb.norm_key(state["standalone"])
    for hit in graphdb.search_entities(state["standalone"], limit=12):
        n = graphdb.norm_key(hit["name"])
        if len(n) >= 4 and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", qn) and hit["n_docs"] > 0:
            linked.setdefault(hit["id"], {**hit, "query": hit["name"]})
    ents = sorted(linked.values(), key=lambda e: -e.get("n_docs", 0))[:8]
    steps = _step(state, "link", f"{len(ents)} graph entities: " + ", ".join(e["name"] for e in ents),
                  entities=[{k: e[k] for k in ("id", "name", "type")} for e in ents])
    return {"linked": ents, "steps": steps}


_AUTHOR_RE = re.compile(r"\b([A-Z][\w'\u2019\-]{2,})(?:\s+and\s+[A-Z][\w'\u2019\-]+)?\s+et\s+al\.?(?:[\s,]*\(?((?:19|20)\d{2})\)?)?")


def author_documents(question: str) -> list[str]:
    """Documents whose first author is named in the question ("Moraes et al. 2012")."""
    ids: list[str] = []
    for surname, year in _AUTHOR_RE.findall(question):
        ids += graphdb.documents_by_first_author(surname, int(year) if year else None)
    return list(dict.fromkeys(ids))[:6]


def retrieve(state: State) -> dict:
    # Passages: every query, fused.
    fused: dict[str, float] = {}
    for i, q in enumerate(state["queries"]):
        for cid, score in retrieval.hybrid_ids(q, k=16):
            fused[cid] = fused.get(cid, 0.0) + score * (1.15 if i == 0 else 1.0)
    # Papers the question names by author ("Moraes et al.") are guaranteed a place in the evidence.
    named = author_documents(state["question"] + " " + state["standalone"])
    forced: list[str] = []
    if named:
        per: dict[str, int] = {}
        for cid, score in retrieval.hybrid_ids(state["standalone"], k=12 * len(named), doc_ids=named):
            did = cid.rsplit(":p", 1)[0]
            if per.get(did, 0) < 3:
                per[did] = per.get(did, 0) + 1
                forced.append(cid)
            fused[cid] = fused.get(cid, 0.0) + score * 1.5
    ids = [e["id"] for e in state.get("linked", [])]
    if len(ids) >= 2:
        for cid in graphdb.chunks_mentioning(ids, limit=6):
            fused[cid] = fused.get(cid, 0.0) + 0.012
    ranked = sorted(fused.items(), key=lambda kv: -kv[1])
    order = [(cid, fused[cid]) for cid in forced] + [(cid, sc) for cid, sc in ranked[:40] if cid not in forced]
    rows = graphdb.chunks_by_ids([cid for cid, _ in order])
    passages, per_doc = [], {}
    for cid, score in order:
        r = rows.get(cid)
        if not r:
            continue
        did = r["document"]["id"]
        if per_doc.get(did, 0) >= 3:
            continue
        per_doc[did] = per_doc.get(did, 0) + 1
        passages.append({**r, "score": score})
        if len(passages) >= 12 + len(forced):
            break

    # Graph: claims about the linked entities, and along paths between them.
    claims: dict[str, dict] = {}
    path_ids: list[list[str]] = []
    if ids:
        for c in graphdb.claims_for_entities(ids, limit=24):
            claims[c["id"]] = c
        pairs = [(ids[i], ids[j]) for i in range(len(ids)) for j in range(i + 1, len(ids))][:4]
        for a, b in pairs:
            p = graphdb.paths(a, b, max_hops=3, limit=2)
            for path in p.get("paths", []):
                path_ids.append(path)
                for x, y in zip(path, path[1:]):
                    for c in graphdb.claims_between(x, y, limit=2):
                        claims.setdefault(c["id"], c)
    claim_list = sorted(claims.values(), key=lambda c: (-int(bool(c["verified"])), -c["confidence"]))[:18]
    if named:
        have = {c["id"] for c in claim_list}
        claim_list += [c for c in graphdb.claims_for_documents(named, per_doc=6) if c["id"] not in have]
    steps = _step(
        state, "retrieve",
        f"{len(passages)} passages from {len(per_doc)} documents; {len(claim_list)} graph claims"
        + (f"; {len(path_ids)} connecting paths" if path_ids else ""),
    )
    return {"passages": passages, "claims": claim_list, "paths": path_ids,
            "rounds": state.get("rounds", 0) + 1, "steps": steps}


def needs_more(state: State) -> str:
    if state.get("rounds", 0) < 2 and len(state.get("passages", [])) < 4 and not state.get("claims"):
        return "broaden"
    return "chart" if state.get("wants_chart") else "answer"


def chart(state: State) -> dict:
    """Build the requested chart from the numbered evidence (or a supplementary table in it)."""
    text, items = _evidence(state)
    try:
        out = charts.make_chart(state["standalone"], items)
    except Exception as exc:
        out = {"none": f"chart failed: {exc}"}
    spec = out.get("chart")
    if spec:
        _emit("chart", chart=spec)
        steps = _step(state, "chart", f"{spec['type']} chart, {len(spec['points'])} points from "
                      + ("a supplementary table" if spec["source"] == "table" else "the cited evidence"))
    else:
        steps = _step(state, "chart", f"no chart: {out.get('none')}")
    return {"evidence_text": text, "items": items, "chart": spec, "steps": steps,
            "result": {"chart_note": None if spec else out.get("none")}}


def broaden(state: State) -> dict:
    try:
        data = llm.complete_json(
            [{"role": "system", "content": "Return JSON {\"queries\": [...]} with 3 broader, simpler "
                                           "search queries (synonyms, parent concepts) for the question."},
             {"role": "user", "content": state["standalone"]}],
            purpose="plan", max_tokens=300,
        )
        queries = [str(q) for q in data.get("queries", []) if str(q).strip()][:3]
    except Exception:
        queries = []
    queries = queries or [" ".join(state.get("entity_names", [])) or state["standalone"]]
    steps = _step(state, "broaden", "little evidence found; searching more broadly", queries=queries)
    return {"queries": queries, "steps": steps}


ANSWER_SYSTEM = """You are a research assistant for the Shakoor Lab (Danforth Center) on sorghum
grain protein quality and digestibility. Answer ONLY from the numbered evidence below.

- Cite every factual statement with its evidence numbers in square brackets, e.g. "[3]" or "[2][7]".
  Use only numbers that exist in the evidence list.
- Passage evidence [P] is text from documents; graph evidence [G] is a claim extracted from a
  document with its verbatim quote. Prefer citing the passage or claim whose text says it directly.
  A [G] claim marked "finding" is that document's own result; "background" restates other work,
  so prefer findings and say when something rests only on background statements.
- Keep conditions straight (raw vs cooked, assay, genotype, population, units) and give numbers
  with their units and the study they come from.
- When studies disagree, say so and cite both sides. When the evidence does not answer part of
  the question, say what is missing instead of guessing.
- Format: a direct 1-3 sentence answer first, then short sections or bullets if useful.
  Markdown, no reference list at the end (the app shows sources), no preamble."""


def _label(d: dict) -> str:
    """"Moraes et al. 2012, In vivo protein quality of ..." so authors named in a question can be matched."""
    authors = d.get("authors") or []
    first = (authors[0].split()[-1] if authors else "").strip(",.")
    who = f"{first} et al. " if len(authors) > 1 else (f"{first} " if first else "")
    return f"{who}{d.get('year') or 'n.d.'}, {d['title']}"


def _evidence(state: State) -> tuple[str, list[dict]]:
    items: list[dict] = []
    lines: list[str] = []
    for p in state.get("passages", []):
        d = p["document"]
        n = len(items) + 1
        items.append({"n": n, "kind": "passage", "chunk_id": p["id"], "doc_id": d["id"], "title": d["title"],
                      "year": d.get("year"), "doi": d.get("doi"), "page": p["page"], "text": p["text"],
                      "file_type": d.get("file_type"), "entities": p.get("entities", [])})
        lines.append(f"[{n}] [P] {_label(d)}, page {p['page']}:\n{p['text']}")
    for c in state.get("claims", []):
        d = c["document"]
        n = len(items) + 1
        items.append({"n": n, "kind": "claim", "claim_id": c["id"], "chunk_id": c.get("chunk_id"),
                      "doc_id": d["id"], "title": d["title"], "year": d.get("year"), "doi": d.get("doi"),
                      "page": c["page"], "text": c["evidence"], "quote": c["evidence"],
                      "file_type": d.get("file_type"),
                      "claim": {k: c.get(k) for k in ("relation", "statement", "context", "value", "kind",
                                                      "confidence", "verified")},
                      "subject": c["subject"], "object": c["object"]})
        lines.append(
            f"[{n}] [G] {c['subject']['name']} -{c['relation']}-> {c['object']['name']}"
            f" | {c.get('kind') or 'finding'} | {c['statement']}"
            + (f" | value: {c['value']}" if c.get("value") else "")
            + (f" | context: {c['context']}" if c.get("context") else "")
            + f" | quote (page {c['page']} of {_label(d)}): \"{c['evidence']}\""
        )
    return "\n\n".join(lines), items


def answer(state: State) -> dict:
    if state.get("items") is not None:
        text, items = state.get("evidence_text", ""), state["items"]
    else:
        text, items = _evidence(state)
    _emit("sources", sources=[{k: v for k, v in it.items() if k != "text"} | {"text": it["text"][:600]}
                              for it in items])
    hist = state.get("history") or []
    msgs = [{"role": "system", "content": ANSWER_SYSTEM}]
    for m in hist[-6:]:
        msgs.append({"role": m["role"], "content": m["content"][:2000]})
    chart_line = ""
    if state.get("chart"):
        c = state["chart"]
        chart_line = (f"\n\nA chart is shown above your answer: \"{c['title']}\" ({c['type']}, {len(c['points'])} "
                      f"points, from {'a supplementary table' if c['source'] == 'table' else 'the cited evidence'}). "
                      "Mention it in one sentence; do not list every plotted value.")
    elif state.get("wants_chart"):
        note = (state.get("result") or {}).get("chart_note") or "the evidence has no comparable numbers"
        chart_line = f"\n\nThe user asked for a chart but none could be drawn ({note}). Say so in one sentence."
    msgs.append({"role": "user", "content": f"Evidence:\n\n{text or '(no evidence found)'}\n\n"
                                            f"Question: {state['standalone']}{chart_line}"})
    steps = _step(state, "answer", f"writing with {len(items)} pieces of evidence ({settings.chat_model})")
    out = []
    try:
        for piece in llm.stream(msgs, purpose="chat", max_tokens=2200):
            out.append(piece)
            _emit("token", text=piece)
    except Exception as exc:
        msg = f"\n\n_The language model failed ({exc}). The sources found are listed beside this answer._"
        out.append(msg)
        _emit("token", text=msg)
    return {"answer": "".join(out), "steps": steps, "result": {"items": items}}


_CITE = re.compile(r"\[(\d{1,3})\]")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")


def _support_quote(passage: str, claims_text: list[str]) -> str:
    """The sentences of the passage that best match what the answer cited it for."""
    sents = [s.strip() for s in _SENT.split(re.sub(r"\s+", " ", passage)) if len(s.strip()) > 25]
    if not sents:
        return passage[:300]
    target = set(re.findall(r"[a-z0-9]{4,}", " ".join(claims_text).lower()))
    scored = sorted(
        ((len(target & set(re.findall(r"[a-z0-9]{4,}", s.lower()))), i) for i, s in enumerate(sents)),
        reverse=True,
    )
    keep = sorted(i for _, i in scored[:2])
    return " ".join(sents[i] for i in keep)


def finalize(state: State) -> dict:
    items = state["result"]["items"]
    by_n = {it["n"]: it for it in items}
    ans = state["answer"]
    used = [int(n) for n in _CITE.findall(ans)]
    invalid = sorted({n for n in used if n not in by_n})
    for n in invalid:
        ans = ans.replace(f"[{n}]", "")
    cited = sorted({n for n in used if n in by_n})
    # What each citation was used for: the answer sentences that cite it.
    answer_sents = _SENT.split(ans)
    for it in items:
        if it["kind"] == "passage":
            using = [s for s in answer_sents if f"[{it['n']}]" in s]
            it["quote"] = _support_quote(it["text"], using or [state["standalone"]])
        it["cited"] = it["n"] in cited

    entity_ids = [e["id"] for e in state.get("linked", [])]
    for it in items:
        if it["kind"] == "claim" and it["cited"]:
            entity_ids += [it["subject"]["id"], it["object"]["id"]]
    for it in items:
        if it["kind"] == "passage" and it["cited"]:
            entity_ids += [e["id"] for e in it.get("entities", [])[:3]]
    doc_ids = [it["doc_id"] for it in items if it["cited"]]
    try:
        graph = graphdb.subgraph(entity_ids, doc_ids=doc_ids, expand=2 if len(entity_ids) < 6 else 1)
    except Exception:
        graph = {"nodes": [], "edges": []}
    for p in state.get("paths", []):
        graph.setdefault("paths", []).append(p)
    steps = _step(state, "verify", f"{len(cited)} citations checked"
                  + (f"; removed {len(invalid)} that matched no evidence" if invalid else ""))
    result = {
        "answer": ans, "sources": items, "cited": cited, "invalid_citations": invalid,
        "graph": graph, "entities": [{k: e[k] for k in ("id", "name", "type")} for e in state.get("linked", [])],
        "standalone": state["standalone"], "queries": state.get("queries", []), "steps": steps,
        "chart": state.get("chart"),
        "model": settings.chat_model, "elapsed_ms": int((time.time() - state["t0"]) * 1000),
    }
    _emit("final", result=result)
    return {"answer": ans, "result": result, "steps": steps}


def _build():
    g = StateGraph(State)
    g.add_node("plan", plan)
    g.add_node("link", link)
    g.add_node("retrieve", retrieve)
    g.add_node("broaden", broaden)
    g.add_node("chart", chart)
    g.add_node("answer", answer)
    g.add_node("finalize", finalize)
    g.add_edge(START, "plan")
    g.add_edge("plan", "link")
    g.add_edge("link", "retrieve")
    g.add_conditional_edges("retrieve", needs_more, {"broaden": "broaden", "chart": "chart", "answer": "answer"})
    g.add_edge("chart", "answer")
    g.add_edge("broaden", "retrieve")
    g.add_edge("answer", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


GRAPH = _build()


def run_stream(question: str, history: list[dict]):
    """Yield UI events: step, sources, token, final."""
    state: State = {"question": question, "history": history, "t0": time.time(), "steps": [], "rounds": 0}
    yield from GRAPH.stream(state, stream_mode="custom")
