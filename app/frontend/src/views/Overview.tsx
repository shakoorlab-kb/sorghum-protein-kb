import { useEffect, useState } from "react";

import { api } from "../api";
import { StatusPill, TypeDot } from "../components/Chips";
import { Icon } from "../components/Icon";
import { ErrorBox, Loading } from "../components/States";
import { fmtAgo, fmtCost, fmtInt } from "../format";
import { NEGATIVE_RELS, POSITIVE_RELS, relLabel, typeColor, typeLabel } from "../palette";
import { href, navigate } from "../router";
import type { Health, Stats } from "../types";

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {sub ? <div className="kpi-sub">{sub}</div> : null}
    </div>
  );
}

function Bars({ rows, color }: { rows: { key: string; label: string; n: number }[]; color: (k: string) => string }) {
  const max = Math.max(1, ...rows.map((r) => r.n));
  return (
    <ul className="bars">
      {rows.map((r) => (
        <li key={r.key}>
          <span className="bar-label">{r.label}</span>
          <span className="bar-track"><span className="bar-fill" style={{ width: `${(100 * r.n) / max}%`, background: color(r.key) }} /></span>
          <span className="bar-n">{fmtInt(r.n)}</span>
        </li>
      ))}
    </ul>
  );
}

function YearChart({ years }: { years: { year: number; n: number }[] }) {
  if (!years.length) return <div className="muted small">No dated documents.</div>;
  const min = Math.min(...years.map((y) => y.year));
  const max = Math.max(...years.map((y) => y.year));
  const by = new Map(years.map((y) => [y.year, y.n]));
  const all = Array.from({ length: max - min + 1 }, (_, i) => ({ year: min + i, n: by.get(min + i) ?? 0 }));
  const top = Math.max(1, ...all.map((a) => a.n));
  const W = 640;
  const H = 140;
  const bw = W / all.length;
  return (
    <svg className="year-chart" viewBox={`0 0 ${W} ${H + 20}`} role="img" aria-label="Documents by publication year">
      {all.map((a, i) => {
        const h = (a.n / top) * H;
        return (
          <g key={a.year}>
            <rect x={i * bw + 1} y={H - h} width={Math.max(1, bw - 2)} height={h} rx={1.5} className="year-bar">
              <title>{`${a.year}: ${a.n} document${a.n === 1 ? "" : "s"}`}</title>
            </rect>
          </g>
        );
      })}
      <text x={0} y={H + 15} className="axis-label">{min}</text>
      <text x={W} y={H + 15} textAnchor="end" className="axis-label">{max}</text>
      <line x1={0} x2={W} y1={H + 0.5} y2={H + 0.5} className="axis-line" />
    </svg>
  );
}

export function Overview() {
  const [s, setS] = useState<Stats | null>(null);
  const [h, setH] = useState<Health | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const load = () => {
    setErr(null);
    api.get<Stats>("api/stats").then(setS).catch(setErr);
    api.get<Health>("api/health").then(setH).catch(() => setH(null));
  };
  useEffect(load, []);

  if (err) return <div className="page"><ErrorBox error={err} onRetry={load} /></div>;
  if (!s) return <div className="page"><Loading label="Loading overview" /></div>;

  const verifiedPct = s.claims ? Math.round((100 * s.verified_claims) / s.claims) : 0;
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Overview</h1>
          <p className="muted">The library, the knowledge graph built from it, and what it costs to run.</p>
        </div>
        <div className="health">
          <span className={h?.neo4j ? "health-dot ok" : "health-dot err"} /> Graph database {h?.neo4j ? "online" : "offline"}
          <span className={h?.llm ? "health-dot ok" : "health-dot err"} /> Language model {h?.llm ? "configured" : "not configured"}
        </div>
      </header>

      <div className="kpis">
        <Kpi label="Documents" value={fmtInt(s.documents)} sub={`${fmtInt(s.extracted_documents)} with graph`} />
        <Kpi label="Pages" value={fmtInt(s.pages)} />
        <Kpi label="Passages" value={fmtInt(s.chunks)} sub="searchable chunks" />
        <Kpi label="Entities" value={fmtInt(s.entities)} />
        <Kpi label="Claims" value={fmtInt(s.claims)} sub={`${verifiedPct}% quote-verified`} />
        <Kpi label="Relations" value={fmtInt(s.relations)} sub="entity to entity" />
        <Kpi label="Citations" value={fmtInt(s.citations)} sub="paper to paper" />
        <Kpi label="Model spend" value={fmtCost(s.usage.cost)} sub={`${fmtInt(s.usage.calls)} calls`} />
      </div>

      <div className="grid-2">
        <section className="card">
          <div className="section-title">Entities by type</div>
          <Bars rows={s.entity_types.map((t) => ({ key: t.type, label: typeLabel(t.type), n: t.n }))} color={typeColor} />
        </section>
        <section className="card">
          <div className="section-title">Claims by relation</div>
          <Bars
            rows={s.relation_types.map((t) => ({ key: t.type, label: relLabel(t.type), n: t.n }))}
            color={(k) => (POSITIVE_RELS.has(k) ? "#16a34a" : NEGATIVE_RELS.has(k) ? "#dc2626" : "var(--chart-neutral)")}
          />
        </section>
      </div>

      <div className="grid-2">
        <section className="card">
          <div className="section-title">Documents by year</div>
          <YearChart years={s.years} />
          <div className="origin-row">
            {s.origins.map((o) => (
              <span key={`${o.origin}-${o.doc_type}`} className="tag">{o.origin === "upload" ? "Upload" : "KB"} · {o.doc_type ?? "paper"}: {o.n}</span>
            ))}
          </div>
        </section>
        <section className="card">
          <div className="section-title">Most discussed entities</div>
          <ol className="top-entities">
            {s.top_entities.map((e) => (
              <li key={e.id}>
                <button type="button" onClick={() => navigate({ view: "graph", entity: e.id })}>
                  <TypeDot type={e.type} />
                  <span className="te-name">{e.name}</span>
                  <span className="te-meta">{e.n_docs} docs · {e.degree} links</span>
                </button>
              </li>
            ))}
            {!s.top_entities.length ? <li className="muted small">No entities yet. <a href={href({ view: "ingest" })}>Build the graph</a>.</li> : null}
          </ol>
        </section>
      </div>

      <div className="grid-2">
        <section className="card">
          <div className="section-title">Models</div>
          <dl className="kv">
            <dt>Chat</dt><dd>{s.models.chat}</dd>
            <dt>Graph extraction</dt><dd>{s.models.extract}</dd>
            <dt>Embeddings (local)</dt><dd>{s.models.embed}</dd>
          </dl>
          <div className="section-title">Usage</div>
          <table className="table table-compact">
            <thead><tr><th>Purpose</th><th>Model</th><th className="num">Calls</th><th className="num">Cost</th></tr></thead>
            <tbody>
              {s.usage.by_purpose.map((u) => (
                <tr key={`${u.purpose}-${u.model}`}><td>{u.purpose}</td><td className="muted">{u.model}</td><td className="num">{fmtInt(u.calls)}</td><td className="num">{fmtCost(u.cost)}</td></tr>
              ))}
              {!s.usage.by_purpose.length ? <tr><td colSpan={4} className="muted">No model calls yet.</td></tr> : null}
            </tbody>
          </table>
        </section>
        <section className="card">
          <div className="card-head-inline">
            <div className="section-title">Recent jobs</div>
            <a className="link-btn" href={href({ view: "ingest" })}>All jobs <Icon name="right" size={12} /></a>
          </div>
          <ul className="job-mini">
            {s.jobs.map((j) => (
              <li key={j.id}>
                <span className="job-label">{j.label}</span>
                <StatusPill status={j.status} />
                <span className="muted small">{fmtAgo(j.updated_at)}</span>
              </li>
            ))}
            {!s.jobs.length ? <li className="muted small">No jobs yet.</li> : null}
          </ul>
        </section>
      </div>
    </div>
  );
}
