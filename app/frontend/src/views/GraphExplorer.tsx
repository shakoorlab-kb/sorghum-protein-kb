import { useCallback, useEffect, useMemo, useState } from "react";

import { api, enc, qs } from "../api";
import { EntityChip, TypeBadge, VerifiedBadge } from "../components/Chips";
import { EntityPicker } from "../components/EntityPicker";
import { GraphCanvas, GraphLegend } from "../components/GraphCanvas";
import { Icon } from "../components/Icon";
import { Empty, ErrorBox, Loading } from "../components/States";
import { useInspector } from "../inspector";
import { ENTITY_TYPES, relLabel, typeColor, typeLabel } from "../palette";
import { href, navigate } from "../router";
import { claimTarget } from "../targets";
import type { Claim, EntityDetail, EntitySearchHit, GraphEdge, GraphView } from "../types";

type Panel =
  | { kind: "entity"; id: string }
  | { kind: "relation"; edge: GraphEdge; names: [string, string] }
  | null;

export function GraphExplorer({ entity, theme }: { entity?: string; theme: string }) {
  const [view, setView] = useState<GraphView | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [depth, setDepth] = useState<1 | 2>(1);
  const [withDocs, setWithDocs] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [panel, setPanel] = useState<Panel>(null);
  const [pathFrom, setPathFrom] = useState<EntitySearchHit | null>(null);
  const [pathTo, setPathTo] = useState<EntitySearchHit | null>(null);
  const [paths, setPaths] = useState<string[][] | null>(null);
  const [pathIdx, setPathIdx] = useState(0);
  const [mode, setMode] = useState<"explore" | "path">("explore");
  // The landing map: the most discussed entities overall, or of one type.
  const [overviewType, setOverviewType] = useState("");
  const [totals, setTotals] = useState<Record<string, number>>({});
  useEffect(() => {
    api.get<Record<string, number>>("api/graph/types").then(setTotals).catch(() => setTotals({}));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    setPaths(null);
    try {
      if (entity) {
        const v = await api.get<GraphView>(
          `api/graph/entity/${enc(entity)}/neighborhood${qs({ depth, limit: depth === 2 ? 30 : 45, documents: withDocs })}`,
        );
        setView(v);
        setPanel({ kind: "entity", id: entity });
      } else {
        setView(await api.get<GraphView>(`api/graph/overview${qs({ limit: 80, type: overviewType })}`));
        setPanel(null);
      }
    } catch (e) {
      setErr(e);
    } finally {
      setLoading(false);
    }
  }, [entity, depth, withDocs, overviewType]);

  useEffect(() => {
    if (mode === "explore") load();
  }, [load, mode]);

  const findPath = async () => {
    if (!pathFrom || !pathTo) return;
    setLoading(true);
    setErr(null);
    try {
      const v = await api.get<GraphView>(`api/graph/path${qs({ a: pathFrom.id, b: pathTo.id, max_hops: 4 })}`);
      setView(v);
      // Parallel relations give the same node sequence more than once.
      const seen = new Set<string>();
      setPaths((v.paths ?? []).filter((p) => {
        const k = p.join("|");
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
      }));
      setPathIdx(0);
      setPanel(null);
    } catch (e) {
      setErr(e);
    } finally {
      setLoading(false);
    }
  };

  const shown = useMemo<GraphView | null>(() => {
    if (!view) return null;
    if (!hidden.size) return view;
    const keep = new Set(
      view.nodes.filter((n) => n.id === view.center || n.focus || !hidden.has(n.kind === "document" ? "DOCUMENT" : n.type)).map((n) => n.id),
    );
    return { ...view, nodes: view.nodes.filter((n) => keep.has(n.id)), edges: view.edges.filter((e) => keep.has(e.source) && keep.has(e.target)) };
  }, [view, hidden]);

  const presentTypes = useMemo(() => {
    const counts = new Map<string, number>();
    view?.nodes.forEach((n) => {
      const t = n.kind === "document" ? "DOCUMENT" : n.type;
      counts.set(t, (counts.get(t) ?? 0) + 1);
    });
    return counts;
  }, [view]);

  const nameOf = (id: string) => view?.nodes.find((n) => n.id === id)?.name ?? id.split(":").slice(1).join(":");

  return (
    <div className="explorer">
      <aside className="explorer-side">
        <div className="seg" role="tablist" aria-label="Explorer mode">
          <button type="button" role="tab" aria-selected={mode === "explore"} className={mode === "explore" ? "seg-btn active" : "seg-btn"} onClick={() => setMode("explore")}>
            <Icon name="graph" size={14} /> Explore
          </button>
          <button type="button" role="tab" aria-selected={mode === "path"} className={mode === "path" ? "seg-btn active" : "seg-btn"} onClick={() => setMode("path")}>
            <Icon name="route" size={14} /> Find a path
          </button>
        </div>

        {mode === "explore" ? (
          <>
            <EntityPicker placeholder="Search genes, traits, processes…" onPick={(h) => navigate({ view: "graph", entity: h.id })} />
            {entity ? (
              <button type="button" className="link-btn" onClick={() => navigate({ view: "graph" })}>
                <Icon name="left" size={12} /> Back to the overview
              </button>
            ) : (
              <div className="field-group">
                <div className="field-label">Show on the map</div>
                <select value={overviewType} onChange={(e) => setOverviewType(e.target.value)} aria-label="Entities shown on the map">
                  <option value="">Most discussed, all types</option>
                  {ENTITY_TYPES.filter((t) => totals[t]).map((t) => (
                    <option key={t} value={t}>{typeLabel(t)}: top 30 of {totals[t].toLocaleString()}</option>
                  ))}
                </select>
              </div>
            )}
            <div className="field-group">
              <div className="field-label">Neighbourhood</div>
              <div className="seg seg-small">
                <button type="button" className={depth === 1 ? "seg-btn active" : "seg-btn"} onClick={() => setDepth(1)} disabled={!entity}>1 step</button>
                <button type="button" className={depth === 2 ? "seg-btn active" : "seg-btn"} onClick={() => setDepth(2)} disabled={!entity}>2 steps</button>
              </div>
              <label className="check">
                <input type="checkbox" checked={withDocs} onChange={(e) => setWithDocs(e.target.checked)} disabled={!entity} />
                Show documents that discuss it
              </label>
            </div>
          </>
        ) : (
          <div className="field-group">
            <div className="field-label">From</div>
            <EntityPicker placeholder="First entity" onPick={setPathFrom} value={pathFrom?.name} compact />
            <div className="field-label">To</div>
            <EntityPicker placeholder="Second entity" onPick={setPathTo} value={pathTo?.name} compact />
            <button type="button" className="btn btn-primary btn-block" disabled={!pathFrom || !pathTo} onClick={findPath}>
              <Icon name="route" /> Find connections
            </button>
            {paths ? (
              paths.length ? (
                <ol className="path-list">
                  {paths.map((p, i) => (
                    <li key={i}>
                      <button type="button" className={i === pathIdx ? "path-item active" : "path-item"} onClick={() => setPathIdx(i)}>
                        {p.map((id) => nameOf(id)).join(" → ")}
                      </button>
                    </li>
                  ))}
                </ol>
              ) : (
                <div className="muted small">No path of up to 4 steps connects these two entities.</div>
              )
            ) : null}
          </div>
        )}

        <div className="field-group">
          <div className="field-label">On this map</div>
          <div className="type-filters">
            {[...ENTITY_TYPES, "DOCUMENT"].filter((t) => presentTypes.has(t)).map((t) => {
              const on = !hidden.has(t);
              return (
                <button
                  type="button"
                  key={t}
                  className={on ? "type-filter on" : "type-filter"}
                  style={{ ["--c" as string]: typeColor(t) }}
                  aria-pressed={on}
                  onClick={() => setHidden((h) => {
                    const n = new Set(h);
                    if (n.has(t)) n.delete(t); else n.add(t);
                    return n;
                  })}
                >
                  <span className="type-dot" style={{ background: typeColor(t) }} />
                  {typeLabel(t)}{" "}
                  <span className="chip-count" title={totals[t] ? `${presentTypes.get(t)} of the ${totals[t]} ${typeLabel(t).toLowerCase()} entities in the graph are on this map` : undefined}>
                    {presentTypes.get(t)}{totals[t] ? ` of ${totals[t].toLocaleString()}` : ""}
                  </span>
                </button>
              );
            })}
            {!presentTypes.size ? <span className="muted small">No entities loaded.</span> : null}
          </div>
        </div>
      </aside>

      <section className="explorer-canvas">
        <div className="canvas-head">
          <div>
            <h2>{mode === "path" ? "Connections between entities" : entity ? nameOf(entity) : "Knowledge graph overview"}</h2>
            <div className="muted small">
              {shown ? `${shown.nodes.length} nodes, ${shown.edges.length} links` : ""}
              {!entity && mode === "explore"
                ? overviewType
                  ? ` · the 30 most widely discussed ${typeLabel(overviewType).toLowerCase()} entities and their strongest links`
                  : " · the most widely discussed entities and how they relate"
                : ""}
            </div>
          </div>
          {loading ? <span className="muted small">Loading…</span> : null}
        </div>
        {err ? <ErrorBox error={err} onRetry={load} /> : null}
        <GraphCanvas
          view={shown}
          height="100%"
          theme={theme}
          selectedId={panel?.kind === "entity" ? panel.id : null}
          highlightPath={paths && paths[pathIdx] ? paths[pathIdx] : null}
          emptyText={loading ? "Loading graph…" : "The graph is empty. Ingest documents and build the graph first."}
          onNodeClick={(n) => {
            if (n.kind === "document" && n.doc_id) navigate({ view: "document", id: n.doc_id });
            else if (mode === "path") setPanel({ kind: "entity", id: n.id });
            else navigate({ view: "graph", entity: n.id });
          }}
          onEdgeClick={(e) => {
            if (e.type === "DISCUSSES" || e.type === "CITES") return;
            setPanel({ kind: "relation", edge: e, names: [nameOf(e.source), nameOf(e.target)] });
          }}
        />
        <GraphLegend view={shown} />
      </section>

      <aside className="explorer-detail">
        {panel?.kind === "entity" ? <EntityPanel id={panel.id} /> : null}
        {panel?.kind === "relation" ? <RelationPanel edge={panel.edge} names={panel.names} /> : null}
        {!panel ? (
          <Empty icon="graph" title="Select an entity or a link">
            Click a node to see what the literature says about it, or a link to read the evidence behind a relation.
          </Empty>
        ) : null}
      </aside>
    </div>
  );
}

function ClaimItem({ c, showDoc = true }: { c: Claim; showDoc?: boolean }) {
  const { openSource } = useInspector();
  return (
    <li>
      <button type="button" className="claim-item" onClick={() => openSource(claimTarget(c))}>
        <div className="claim-line">
          <span style={{ color: typeColor(c.subject.type) }}>{c.subject.name}</span>
          <span className="rel">{relLabel(c.relation)}</span>
          <span style={{ color: typeColor(c.object.type) }}>{c.object.name}</span>
        </div>
        {c.evidence ? <div className="claim-quote">“{c.evidence}”</div> : null}
        <div className="claim-foot">
          {showDoc && c.document ? <span className="claim-doc">{c.document.title}{c.document.year ? ` (${c.document.year})` : ""}</span> : null}
          <span>p. {c.page}</span>
          {c.verified ? <span className="ok-dot" title="Quote verified on the page">verified</span> : <span className="warn-dot">unverified</span>}
        </div>
      </button>
    </li>
  );
}

function EntityPanel({ id }: { id: string }) {
  const [d, setD] = useState<EntityDetail | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [relFilter, setRelFilter] = useState<string>("");
  useEffect(() => {
    let alive = true;
    setD(null);
    setErr(null);
    api.get<EntityDetail>(`api/graph/entity/${enc(id)}`).then((v) => alive && setD(v)).catch((e) => alive && setErr(e));
    return () => {
      alive = false;
    };
  }, [id]);
  if (err) return <ErrorBox error={err} />;
  if (!d) return <Loading label="Loading entity" />;
  const rels = [...new Set(d.claims.map((c) => c.relation))];
  const claims = relFilter ? d.claims.filter((c) => c.relation === relFilter) : d.claims;
  return (
    <div className="detail">
      <TypeBadge type={d.type} />
      <h3 className="detail-title">{d.name}</h3>
      {d.description ? <p className="detail-desc">{d.description}</p> : null}
      <div className="detail-stats">
        <div><strong>{d.n_docs ?? 0}</strong><span>documents</span></div>
        <div><strong>{d.mentions ?? 0}</strong><span>mentions</span></div>
        <div><strong>{d.degree ?? 0}</strong><span>relations</span></div>
      </div>
      {d.aliases?.length ? (
        <div className="aliases"><span className="field-label">Also called</span> {d.aliases.slice(0, 12).join(", ")}</div>
      ) : null}
      <a className="btn btn-primary btn-block" href={href({ view: "chat", ask: `What does the literature say about ${d.name}?` })}>
        <Icon name="chat" /> Ask about this
      </a>
      <div className="section-title">Claims <span className="count">{d.claims.length}</span></div>
      {rels.length > 1 ? (
        <div className="rel-filters">
          <button type="button" className={!relFilter ? "mini-chip active" : "mini-chip"} onClick={() => setRelFilter("")}>All</button>
          {rels.map((r) => (
            <button type="button" key={r} className={relFilter === r ? "mini-chip active" : "mini-chip"} onClick={() => setRelFilter(r)}>{relLabel(r)}</button>
          ))}
        </div>
      ) : null}
      {claims.length ? <ul className="claim-list">{claims.map((c) => <ClaimItem key={c.id} c={c} />)}</ul> : <div className="muted small">No claims extracted yet.</div>}
      <div className="section-title">Documents <span className="count">{d.documents.length}</span></div>
      <ul className="doc-mini-list">
        {d.documents.map((doc) => (
          <li key={doc.id}>
            <a href={href({ view: "document", id: doc.id })}>
              <span>{doc.title}</span>
              <span className="muted">{doc.year ?? ""} · {doc.n} mentions</span>
            </a>
          </li>
        ))}
        {!d.documents.length ? <li className="muted small">None yet.</li> : null}
      </ul>
    </div>
  );
}

function RelationPanel({ edge, names }: { edge: GraphEdge; names: [string, string] }) {
  const [items, setItems] = useState<Claim[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    let alive = true;
    setItems(null);
    setErr(null);
    api
      .get<{ items: Claim[] }>(`api/graph/relation${qs({ a: edge.source, b: edge.target })}`)
      .then((r) => alive && setItems(r.items))
      .catch((e) => alive && setErr(e));
    return () => {
      alive = false;
    };
  }, [edge]);
  const main = items?.filter((c) => c.relation === edge.type) ?? [];
  const other = items?.filter((c) => c.relation !== edge.type) ?? [];
  return (
    <div className="detail">
      <div className="eyebrow">Relation</div>
      <div className="relation-head">
        <EntityChip name={names[0]} type={edge.source.split(":")[0]} onClick={() => navigate({ view: "graph", entity: edge.source })} />
        <span className="rel-arrow">{relLabel(edge.type)} →</span>
        <EntityChip name={names[1]} type={edge.target.split(":")[0]} onClick={() => navigate({ view: "graph", entity: edge.target })} />
      </div>
      <div className="detail-stats">
        <div><strong>{edge.n ?? 0}</strong><span>claims</span></div>
        <div><strong>{edge.n_docs ?? 0}</strong><span>documents</span></div>
        <div><strong>{Number(edge.verified ?? 0)}</strong><span>verified</span></div>
      </div>
      {err ? <ErrorBox error={err} /> : null}
      {!items && !err ? <Loading label="Loading evidence" /> : null}
      {items ? (
        <>
          <div className="section-title">Evidence <span className="count">{main.length}</span></div>
          <ul className="claim-list">{main.map((c) => <ClaimItem key={c.id} c={c} />)}</ul>
          {other.length ? (
            <>
              <div className="section-title">Other relations between them <span className="count">{other.length}</span></div>
              <ul className="claim-list">{other.map((c) => <ClaimItem key={c.id} c={c} />)}</ul>
            </>
          ) : null}
          {!main.length && !other.length ? <div className="muted small">No claims found.</div> : null}
        </>
      ) : null}
      <div className="hint-row"><VerifiedBadge verified /> means the quote was matched word for word on the cited page.</div>
    </div>
  );
}
