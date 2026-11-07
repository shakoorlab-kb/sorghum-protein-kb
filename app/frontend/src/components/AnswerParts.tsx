import { useMemo, useState } from "react";

import { api, qs } from "../api";
import { useInspector } from "../inspector";
import { relLabel } from "../palette";
import { navigate } from "../router";
import { claimTarget, sourceTarget } from "../targets";
import type { Claim, GraphView, Source, Step } from "../types";
import { EntityChip } from "./Chips";
import { GraphCanvas, GraphLegend } from "./GraphCanvas";
import { Icon } from "./Icon";
import { Spinner } from "./States";

const STEP_LABEL: Record<string, string> = {
  plan: "Understanding the question",
  link: "Linking to graph entities",
  retrieve: "Retrieving passages and graph claims",
  broaden: "Searching more broadly",
  chart: "Drawing the chart",
  answer: "Writing the answer",
  verify: "Checking citations",
};

export function StepsTimeline({ steps, working }: { steps: Step[]; working: boolean }) {
  const [open, setOpen] = useState(false);
  if (!steps.length && !working) return null;
  const show = working || open;
  const last = steps[steps.length - 1];
  return (
    <div className={`steps ${working ? "steps-live" : ""}`}>
      <button type="button" className="steps-toggle" onClick={() => setOpen((o) => !o)} disabled={working} aria-expanded={show}>
        {working ? <Spinner size={12} /> : <Icon name="check" size={13} />}
        <span>
          {working ? (last ? STEP_LABEL[last.step] ?? last.step : "Starting") : `Reasoned in ${steps.length} steps`}
          {!working && last ? ` · ${(last.ms / 1000).toFixed(1)} s` : ""}
        </span>
        {!working ? <Icon name={open ? "left" : "right"} size={12} className="steps-chevron" /> : null}
      </button>
      {show ? (
        <ol className="steps-list">
          {steps.map((s, i) => (
            <li key={i} className="step">
              <span className="step-dot" />
              <div>
                <div className="step-name">{STEP_LABEL[s.step] ?? s.step} <span className="step-ms">{(s.ms / 1000).toFixed(1)} s</span></div>
                <div className="step-detail">{s.detail}</div>
                {s.queries?.length ? (
                  <div className="step-queries">{s.queries.map((q) => <span key={q} className="query-tag">{q}</span>)}</div>
                ) : null}
                {s.entities?.length ? (
                  <div className="step-queries">
                    {s.entities.map((e) => <EntityChip key={e.id} name={e.name} type={e.type} onClick={() => navigate({ view: "graph", entity: e.id })} />)}
                  </div>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

export function SourcesList({ sources, cited }: { sources: Source[]; cited: number[] }) {
  const { openSource } = useInspector();
  const [showAll, setShowAll] = useState(false);
  const citedSet = useMemo(() => new Set(cited), [cited]);
  const ordered = useMemo(
    () => [...sources].sort((a, b) => Number(citedSet.has(b.n)) - Number(citedSet.has(a.n)) || a.n - b.n),
    [sources, citedSet],
  );
  const citedList = ordered.filter((s) => citedSet.has(s.n));
  const rest = ordered.filter((s) => !citedSet.has(s.n));
  if (!sources.length) return <div className="muted small">No sources were found for this question.</div>;
  const renderRow = (s: Source) => (
    <li key={s.n}>
      <button type="button" className={`source-row ${citedSet.has(s.n) ? "is-cited" : ""}`} onClick={() => openSource(sourceTarget(s))}>
        <span className="source-n">{s.n}</span>
        <span className="source-main">
          <span className="source-title">{s.title}</span>
          <span className="source-meta">
            <span className={`kind kind-${s.kind}`}>{s.kind === "claim" ? "Graph claim" : "Passage"}</span>
            {s.year ? <span>{s.year}</span> : null}
            <span>p. {s.page}</span>
            {s.kind === "claim" && s.subject && s.object && s.claim ? (
              <span className="source-triple">{s.subject.name} <em>{relLabel(s.claim.relation)}</em> {s.object.name}</span>
            ) : null}
          </span>
          <span className="source-snippet">{(s.quote || s.text || "").slice(0, 220)}</span>
        </span>
      </button>
    </li>
  );
  return (
    <div className="sources">
      <ol className="source-list">{citedList.map(renderRow)}</ol>
      {rest.length ? (
        <>
          <button type="button" className="link-btn" onClick={() => setShowAll((v) => !v)} aria-expanded={showAll}>
            {showAll ? "Hide" : "Show"} {rest.length} more retrieved but not cited
          </button>
          {showAll ? <ol className="source-list source-list-muted">{rest.map(renderRow)}</ol> : null}
        </>
      ) : null}
    </div>
  );
}

/** The answer's connections. Clicking an edge opens the strongest claim behind it. */
export function ConnectionsGraph({ graph, theme, height = 380 }: { graph: GraphView; theme: string; height?: number }) {
  const { openSource } = useInspector();
  const [busy, setBusy] = useState(false);
  const [claims, setClaims] = useState<{ label: string; items: Claim[] } | null>(null);
  return (
    <div className="connections">
      <GraphCanvas
        view={graph}
        height={height}
        theme={theme}
        emptyText="No graph connections for this answer."
        onNodeClick={(n) => (n.kind === "document" && n.doc_id ? navigate({ view: "document", id: n.doc_id }) : navigate({ view: "graph", entity: n.id }))}
        onEdgeClick={async (e) => {
          if (e.type === "DISCUSSES" || e.type === "CITES") return;
          setBusy(true);
          try {
            const r = await api.get<{ items: Claim[] }>(`api/graph/relation${qs({ a: e.source, b: e.target })}`);
            const items = r.items.filter((c) => c.relation === e.type).concat(r.items.filter((c) => c.relation !== e.type));
            setClaims({ label: `${relLabel(e.type)}`, items });
            if (items[0]) openSource(claimTarget(items[0]));
          } finally {
            setBusy(false);
          }
        }}
      />
      <GraphLegend view={graph} />
      <div className="muted small">Click an entity to explore it, or a link to read the evidence behind it. {busy ? <Spinner size={10} /> : null}</div>
      {claims && claims.items.length > 1 ? (
        <ul className="mini-claims">
          {claims.items.slice(0, 6).map((c) => (
            <li key={c.id}>
              <button type="button" className="mini-claim" onClick={() => openSource(claimTarget(c))}>
                <strong>{c.subject.name}</strong> {relLabel(c.relation)} <strong>{c.object.name}</strong>
                <span className="muted"> · {c.document?.title?.slice(0, 60)} p. {c.page}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
