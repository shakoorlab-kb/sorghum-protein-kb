import { useCallback, useEffect, useMemo, useState } from "react";

import { api, docFileUrl, enc } from "../api";
import { EntityChip, StatusPill } from "../components/Chips";
import { Confirm } from "../components/Confirm";
import { GraphCanvas, GraphLegend } from "../components/GraphCanvas";
import { Icon } from "../components/Icon";
import { SourceViewer } from "../components/SourceViewer";
import { ErrorBox, Loading } from "../components/States";
import { fmtInt } from "../format";
import { useInspector } from "../inspector";
import { relLabel, typeColor } from "../palette";
import { href, navigate } from "../router";
import { claimTarget } from "../targets";
import type { Claim, DocumentDetail, GraphView, SourceTarget } from "../types";

type Tab = "claims" | "graph" | "pages";

/** PDF text keeps the column's line breaks; join them back into paragraphs. */
function tidy(text: string): string {
  return text
    .replace(/(\w)-\n(\w)/g, "$1$2")
    .split(/\n\s*\n/)
    .map((p) => p.replace(/\s*\n\s*/g, " ").trim())
    .join("\n\n");
}

export function DocumentView({ id, theme }: { id: string; theme: string }) {
  const { openSource } = useInspector();
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [tab, setTab] = useState<Tab>("claims");
  const [claims, setClaims] = useState<Claim[] | null>(null);
  const [graph, setGraph] = useState<GraphView | null>(null);
  const [confirmDel, setConfirmDel] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [claimFilter, setClaimFilter] = useState("");
  const [pageTarget, setPageTarget] = useState<SourceTarget | null>(null);
  const [absOpen, setAbsOpen] = useState(false);

  const load = useCallback(() => {
    setErr(null);
    setDoc(null);
    setClaims(null);
    setGraph(null);
    api.get<DocumentDetail>(`api/documents/${enc(id)}`).then(setDoc).catch(setErr);
    api.get<{ items: Claim[] }>(`api/documents/${enc(id)}/claims`).then((r) => setClaims(r.items)).catch(() => setClaims([]));
  }, [id]);
  useEffect(load, [load]);

  useEffect(() => {
    if (tab === "graph" && !graph) api.get<GraphView>(`api/documents/${enc(id)}/graph`).then(setGraph).catch(() => setGraph({ nodes: [], edges: [] }));
  }, [tab, graph, id]);

  useEffect(() => {
    if (doc) setPageTarget({ doc_id: doc.id, page: 1, title: doc.title, year: doc.year, label: "Document" });
  }, [doc]);

  const shownClaims = useMemo(() => {
    const f = claimFilter.trim().toLowerCase();
    if (!claims) return [];
    if (!f) return claims;
    return claims.filter((c) => `${c.subject.name} ${c.relation} ${c.object.name} ${c.statement} ${c.evidence}`.toLowerCase().includes(f));
  }, [claims, claimFilter]);

  if (err) return <div className="page"><ErrorBox error={err} onRetry={load} /></div>;
  if (!doc) return <div className="page"><Loading label="Loading document" /></div>;

  const reextract = async () => {
    try {
      await api.post(`api/documents/${enc(id)}/reextract`);
      setMsg("Graph rebuild started. Follow it on the Ingest page.");
    } catch (e) {
      setMsg((e as Error).message);
    }
  };

  return (
    <div className="page doc-page">
      <a className="link-btn back" href={href({ view: "library" })}><Icon name="left" size={12} /> Library</a>
      <header className="doc-head">
        <div className="doc-head-main">
          <div className="doc-tags">
            <span className="tag">{doc.doc_type ?? "paper"}</span>
            <span className={doc.origin === "upload" ? "tag tag-accent" : "tag"}>{doc.origin === "upload" ? "Uploaded here" : "Sorghum KB"}</span>
            <StatusPill status={doc.extracted_at && doc.extracted_at !== "None" ? "ready" : doc.status ?? "indexed"} />
          </div>
          <h1>{doc.title}</h1>
          <div className="doc-byline">
            {doc.authors?.length ? <span>{doc.authors.slice(0, 8).join(", ")}{doc.authors.length > 8 ? ", et al." : ""}</span> : null}
          </div>
          <div className="doc-byline muted">
            {[doc.journal, doc.year].filter(Boolean).join(" · ")}
            {doc.doi ? <> · <a href={`https://doi.org/${doc.doi}`} target="_blank" rel="noreferrer">{doc.doi}</a></> : null}
          </div>
        </div>
        <div className="doc-actions">
          <a className="btn btn-primary" href={docFileUrl(doc.id)} target="_blank" rel="noreferrer"><Icon name="external" /> Open original</a>
          <a className="btn" href={href({ view: "chat", ask: `Summarise the key findings of "${doc.title}"` })}><Icon name="chat" /> Ask about it</a>
          <button type="button" className="btn" onClick={reextract}><Icon name="refresh" /> Rebuild graph</button>
          <button type="button" className="btn btn-danger-ghost" onClick={() => setConfirmDel(true)}><Icon name="trash" /> Remove</button>
        </div>
      </header>
      {msg ? <div className="notice">{msg}</div> : null}

      <div className="doc-stats">
        <div><strong>{fmtInt(doc.n_pages)}</strong><span>pages</span></div>
        <div><strong>{fmtInt(doc.n_chunks)}</strong><span>passages</span></div>
        <div><strong>{fmtInt(doc.n_claims)}</strong><span>claims</span></div>
        <div><strong>{fmtInt(doc.top_entities.length)}</strong><span>top entities</span></div>
        <div><strong>{fmtInt(doc.n_cites)}</strong><span>cites in library</span></div>
        <div><strong>{fmtInt(doc.n_cited_by)}</strong><span>cited by</span></div>
      </div>

      {doc.summary || doc.key_findings?.length ? (
        <section className="card">
          <div className="section-title">
            Summary{doc.study_type ? <span className="badge" style={{ marginLeft: 8 }}>{doc.study_type}</span> : null}
          </div>
          {doc.summary ? <p className="abstract">{doc.summary}</p> : null}
          {doc.key_findings?.length ? (
            <>
              <div className="section-title" style={{ marginTop: 12 }}>Key findings</div>
              <ul className="findings">
                {doc.key_findings.map((f, i) => <li key={i}>{f}</li>)}
              </ul>
            </>
          ) : null}
        </section>
      ) : null}

      {doc.abstract ? (
        <section className="card">
          <div className="section-title">Abstract</div>
          <p className={absOpen ? "abstract" : "abstract clamped"}>{tidy(doc.abstract)}</p>
          {doc.abstract.length > 600 ? (
            <button type="button" className="link-btn" onClick={() => setAbsOpen((o) => !o)}>{absOpen ? "Show less" : "Show the full abstract"}</button>
          ) : null}
        </section>
      ) : null}

      {doc.parent ? (
        <section className="card">
          <div className="section-title">Supplementary file of</div>
          <a className="link-btn" href={href({ view: "document", id: doc.parent.id })}>{doc.parent.title}</a>
        </section>
      ) : null}

      {doc.supplements?.length ? (
        <section className="card">
          <div className="section-title">Supplementary files ({doc.supplements.length})</div>
          <ul className="findings">
            {doc.supplements.map((s) => {
              const m = s.title.match(/\[supplement: (.*)\]$/);
              return (
                <li key={s.id}>
                  <a className="link-btn" href={href({ view: "document", id: s.id })}>{m ? m[1] : s.title}</a>
                  {s.n_pages ? <span className="muted small"> · {s.n_pages} pages of rows</span> : null}
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {doc.top_entities.length ? (
        <section className="card">
          <div className="section-title">What it discusses</div>
          <div className="chip-cloud">
            {doc.top_entities.map((e) => (
              <EntityChip key={e.id} name={e.name} type={e.type} count={e.n} onClick={() => navigate({ view: "graph", entity: e.id })} />
            ))}
          </div>
        </section>
      ) : null}

      <div className="tabs tabs-lg" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "claims"} className={tab === "claims" ? "tab active" : "tab"} onClick={() => setTab("claims")}>
          Claims <span className="tab-count">{claims?.length ?? ""}</span>
        </button>
        <button type="button" role="tab" aria-selected={tab === "graph"} className={tab === "graph" ? "tab active" : "tab"} onClick={() => setTab("graph")}>Graph</button>
        <button type="button" role="tab" aria-selected={tab === "pages"} className={tab === "pages" ? "tab active" : "tab"} onClick={() => setTab("pages")}>Pages</button>
      </div>

      {tab === "claims" ? (
        <section>
          <div className="toolbar">
            <div className="search-field grow">
              <Icon name="search" size={14} />
              <input value={claimFilter} onChange={(e) => setClaimFilter(e.target.value)} placeholder="Filter claims" aria-label="Filter claims" />
            </div>
          </div>
          {!claims ? <Loading label="Loading claims" /> : null}
          {claims && !claims.length ? (
            <div className="muted">No claims yet. {doc.extracted_at ? "" : "The graph has not been built for this document; use Rebuild graph."}</div>
          ) : null}
          {shownClaims.length ? (
            <div className="table-wrap">
              <table className="table claims-table">
                <thead>
                  <tr><th>Subject</th><th>Relation</th><th>Object</th><th>Evidence</th><th className="num">Page</th><th /></tr>
                </thead>
                <tbody>
                  {shownClaims.map((c) => (
                    <tr key={c.id}>
                      <td><span className="ent-txt" style={{ color: typeColor(c.subject.type) }}>{c.subject.name}</span></td>
                      <td><span className="rel-tag">{relLabel(c.relation)}</span></td>
                      <td><span className="ent-txt" style={{ color: typeColor(c.object.type) }}>{c.object.name}</span></td>
                      <td className="evidence-cell">
                        <div>{c.statement}{c.kind && c.kind !== "finding" ? <span className="badge" style={{ marginLeft: 6 }}>{c.kind}</span> : null}</div>
                        {c.value ? <div className="small"><strong>{c.value}</strong></div> : null}
                        {c.context ? <div className="muted small">{c.context}</div> : null}
                      </td>
                      <td className="num">
                        <button type="button" className="link-btn" onClick={() => openSource(claimTarget(c, { id: doc.id, title: doc.title, year: doc.year }))}>
                          p. {c.page}
                        </button>
                      </td>
                      <td>{c.verified ? <span className="ok-dot">verified</span> : <span className="warn-dot">unverified</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      ) : null}

      {tab === "graph" ? (
        <section className="card card-flush">
          <GraphCanvas
            view={graph}
            height={560}
            theme={theme}
            emptyText={graph ? "No graph for this document yet." : "Loading graph…"}
            onNodeClick={(n) => (n.kind === "document" && n.doc_id ? navigate({ view: "document", id: n.doc_id }) : navigate({ view: "graph", entity: n.id }))}
            onEdgeClick={(e) => {
              const c = claims?.find((x) => x.subject.id === e.source && x.object.id === e.target && x.relation === e.type);
              if (c) openSource(claimTarget(c, { id: doc.id, title: doc.title, year: doc.year }));
            }}
          />
          <div className="pad"><GraphLegend view={graph} /></div>
        </section>
      ) : null}

      {tab === "pages" && pageTarget ? (
        <section className="card card-flush page-viewer-card">
          <SourceViewer target={pageTarget} embedded />
        </section>
      ) : null}

      {confirmDel ? (
        <Confirm
          title="Remove this document?"
          body={doc.origin === "upload"
            ? "The document, its uploaded file, passages and graph claims will be deleted."
            : "Its passages and graph claims will be removed from this app. The Sorghum KB itself is not changed, and the next KB sync will add it back."}
          confirmLabel="Remove"
          danger
          onCancel={() => setConfirmDel(false)}
          onConfirm={async () => {
            setConfirmDel(false);
            try {
              await api.del(`api/documents/${enc(doc.id)}`);
              navigate({ view: "library" });
            } catch (e) {
              setMsg((e as Error).message);
            }
          }}
        />
      ) : null}
    </div>
  );
}
