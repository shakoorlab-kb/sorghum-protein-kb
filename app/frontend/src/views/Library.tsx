import { useEffect, useState } from "react";

import { api, qs } from "../api";
import { StatusPill } from "../components/Chips";
import { Icon } from "../components/Icon";
import { Empty, ErrorBox, Spinner } from "../components/States";
import { authorsShort, fmtInt } from "../format";
import { href } from "../router";
import type { DocumentRow } from "../types";

const PAGE = 60;

export function Library() {
  const [q, setQ] = useState("");
  const [origin, setOrigin] = useState("");
  const [docType, setDocType] = useState("");
  const [items, setItems] = useState<DocumentRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [more, setMore] = useState(false);

  const load = (offset = 0) => {
    setLoading(true);
    setErr(null);
    api
      .get<{ items: DocumentRow[] }>(`api/documents${qs({ q, origin, doc_type: docType, limit: PAGE, offset })}`)
      .then((r) => {
        setItems((prev) => (offset ? [...prev, ...r.items] : r.items));
        setMore(r.items.length === PAGE);
      })
      .catch(setErr)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    const t = setTimeout(() => load(0), 220);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, origin, docType]);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Library</h1>
          <p className="muted">Every document in the graph: the Sorghum KB papers and anything uploaded here.</p>
        </div>
        <a className="btn btn-primary" href={href({ view: "ingest" })}><Icon name="upload" /> Add documents</a>
      </header>

      <div className="toolbar">
        <div className="search-field grow">
          <Icon name="search" size={14} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search titles, abstracts and DOIs" aria-label="Search documents" />
        </div>
        <select value={origin} onChange={(e) => setOrigin(e.target.value)} aria-label="Origin">
          <option value="">All sources</option>
          <option value="kb">Sorghum KB</option>
          <option value="upload">Uploaded here</option>
        </select>
        <select value={docType} onChange={(e) => setDocType(e.target.value)} aria-label="Document type">
          <option value="">All types</option>
          <option value="paper">Papers</option>
          <option value="thesis">Theses</option>
          <option value="patent">Patents</option>
          <option value="supplement">Supplementary files</option>
          <option value="document">Other documents</option>
        </select>
        {loading ? <Spinner /> : null}
      </div>

      {err ? <ErrorBox error={err} onRetry={() => load(0)} /> : null}
      {!items.length && !loading && !err ? (
        <Empty icon="library" title={q ? "No documents match" : "The library is empty"}>
          {q ? "Try other words." : <>Sync the Sorghum KB or upload files on the <a href={href({ view: "ingest" })}>Ingest</a> page.</>}
        </Empty>
      ) : null}

      {items.length ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Title</th>
                <th className="num">Year</th>
                <th>Type</th>
                <th>Source</th>
                <th className="num">Pages</th>
                <th className="num">Claims</th>
                <th className="num">Entities</th>
                <th>Graph</th>
              </tr>
            </thead>
            <tbody>
              {items.map((d) => (
                <tr key={d.id}>
                  <td className="title-cell">
                    <a href={href({ view: "document", id: d.id })}>{d.title}</a>
                    <div className="muted small">
                      {authorsShort(d.authors)}
                      {d.journal ? ` · ${d.journal}` : ""}
                      {d.doi ? ` · ${d.doi}` : ""}
                    </div>
                  </td>
                  <td className="num">{d.year ?? ""}</td>
                  <td><span className="tag">{d.doc_type ?? "paper"}</span></td>
                  <td><span className={d.origin === "upload" ? "tag tag-accent" : "tag"}>{d.origin === "upload" ? "Upload" : "KB"}</span></td>
                  <td className="num">{fmtInt(d.n_pages)}</td>
                  <td className="num">{fmtInt(d.n_claims)}</td>
                  <td className="num">{fmtInt(d.n_entities)}</td>
                  <td><StatusPill status={d.extracted ? "ready" : d.status ?? "indexed"} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {more ? (
        <div className="center-row">
          <button type="button" className="btn" onClick={() => load(items.length)} disabled={loading}>Load more</button>
        </div>
      ) : null}
    </div>
  );
}
