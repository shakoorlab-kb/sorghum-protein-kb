import { useCallback, useEffect, useRef, useState } from "react";

import { api, enc } from "../api";
import { StatusPill } from "../components/Chips";
import { Icon } from "../components/Icon";
import { ErrorBox, Spinner } from "../components/States";
import { fmtAgo } from "../format";
import { href } from "../router";
import type { Job } from "../types";

const ACCEPT = ".pdf,.docx,.txt,.md,.markdown,.csv,.tsv,.json,.html,.htm";

export function Ingest() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobsErr, setJobsErr] = useState<unknown>(null);
  const [drag, setDrag] = useState(false);
  const [extract, setExtract] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [source, setSource] = useState("");
  const [notice, setNotice] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const loadJobs = useCallback(() => {
    api.get<{ items: Job[] }>("api/jobs").then((r) => { setJobs(r.items); setJobsErr(null); }).catch(setJobsErr);
  }, []);

  useEffect(() => {
    loadJobs();
    const t = setInterval(loadJobs, 2000);
    return () => clearInterval(t);
  }, [loadJobs]);

  const upload = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    setUploading(true);
    setNotice(null);
    const form = new FormData();
    list.forEach((f) => form.append("files", f));
    form.append("extract", String(extract));
    try {
      const r = await api.form<{ jobs: { file: string; job_id?: string; error?: string }[] }>("api/ingest/files", form);
      const bad = r.jobs.filter((j) => j.error);
      setNotice(
        bad.length
          ? { kind: "err", text: bad.map((b) => `${b.file}: ${b.error}`).join("; ") }
          : { kind: "ok", text: `${r.jobs.length} file${r.jobs.length === 1 ? "" : "s"} queued.` },
      );
      loadJobs();
    } catch (e) {
      setNotice({ kind: "err", text: (e as Error).message });
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const fromSource = async () => {
    if (!source.trim()) return;
    setNotice(null);
    try {
      await api.post("api/ingest/url", { source: source.trim(), extract });
      setSource("");
      setNotice({ kind: "ok", text: "Queued. The document is downloaded, read and added to the graph." });
      loadJobs();
    } catch (e) {
      setNotice({ kind: "err", text: (e as Error).message });
    }
  };

  const syncKb = async () => {
    setNotice(null);
    try {
      await api.post("api/ingest/kb-sync", { extract: true });
      setNotice({ kind: "ok", text: "KB sync started. Only new or changed papers are processed." });
      loadJobs();
    } catch (e) {
      setNotice({ kind: "err", text: (e as Error).message });
    }
  };

  const running = jobs.filter((j) => j.status === "running" || j.status === "queued");

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Ingest</h1>
          <p className="muted">Add documents to the library. Each one is read page by page, indexed for search and turned into graph claims that cite their page.</p>
        </div>
      </header>

      <div className="ingest-grid">
        <section
          className={`dropzone ${drag ? "drag" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files); }}
        >
          <div className="dz-icon"><Icon name="upload" size={26} /></div>
          <div className="dz-title">Drop files here</div>
          <div className="muted small">PDF, Word (.docx), text, Markdown, CSV or HTML. Scanned PDFs are read with OCR.</div>
          <button type="button" className="btn btn-primary" onClick={() => fileInput.current?.click()} disabled={uploading}>
            {uploading ? <Spinner size={14} /> : <Icon name="file" />} Choose files
          </button>
          <input ref={fileInput} type="file" multiple accept={ACCEPT} hidden onChange={(e) => e.target.files && upload(e.target.files)} />
        </section>

        <div className="ingest-side">
          <section className="card">
            <div className="section-title">From a DOI or link</div>
            <p className="muted small">A DOI finds the open-access PDF through OpenAlex. A URL can point to a PDF or a web page.</p>
            <form className="inline-form" onSubmit={(e) => { e.preventDefault(); fromSource(); }}>
              <input value={source} onChange={(e) => setSource(e.target.value)} placeholder="10.1073/pnas.080076297 or https://…" aria-label="DOI or URL" />
              <button type="submit" className="btn btn-primary" disabled={!source.trim()}>Add</button>
            </form>
          </section>
          <section className="card">
            <div className="section-title">Sorghum KB library</div>
            <p className="muted small">Index papers from the lab's Sorghum KB that are new or changed, and build their graph.</p>
            <button type="button" className="btn" onClick={syncKb}><Icon name="database" /> Sync KB library</button>
          </section>
          <label className="check">
            <input type="checkbox" checked={extract} onChange={(e) => setExtract(e.target.checked)} />
            Build graph claims with the language model (recommended)
          </label>
        </div>
      </div>

      {notice ? <div className={notice.kind === "ok" ? "notice" : "notice notice-err"}>{notice.text}</div> : null}

      <section className="card card-flush">
        <div className="card-head">
          <div className="section-title">Jobs {running.length ? <span className="count">{running.length} active</span> : null}</div>
          <button type="button" className="icon-btn" onClick={loadJobs} aria-label="Refresh jobs" title="Refresh"><Icon name="refresh" /></button>
        </div>
        {jobsErr ? <ErrorBox error={jobsErr} onRetry={loadJobs} /> : null}
        {!jobs.length && !jobsErr ? <div className="pad muted">No jobs yet.</div> : null}
        {jobs.length ? (
          <div className="table-wrap">
            <table className="table jobs-table">
              <thead>
                <tr><th>Job</th><th>Status</th><th className="progress-col">Progress</th><th>Message</th><th>Updated</th><th /></tr>
              </thead>
              <tbody>
                {jobs.map((j) => {
                  const docId = typeof j.detail?.doc_id === "string" ? j.detail.doc_id : null;
                  const active = j.status === "running" || j.status === "queued";
                  return (
                    <tr key={j.id}>
                      <td className="title-cell">
                        {docId && j.status === "done" ? <a href={href({ view: "document", id: docId })}>{j.label}</a> : <span>{j.label}</span>}
                        <div className="muted small">{j.kind.replace("_", " ")}</div>
                      </td>
                      <td><StatusPill status={j.status} /></td>
                      <td>
                        <div className="progress" role="progressbar" aria-valuenow={Math.round(j.progress * 100)} aria-valuemin={0} aria-valuemax={100}>
                          <div className={`progress-bar ${j.status}`} style={{ width: `${Math.round(j.progress * 100)}%` }} />
                        </div>
                        <div className="muted small">{Math.round(j.progress * 100)}%</div>
                      </td>
                      <td className="msg-cell" title={j.message}><span className="clamp-3">{j.message}</span></td>
                      <td className="muted small nowrap">{fmtAgo(j.updated_at)}</td>
                      <td>
                        {active ? (
                          <button type="button" className="btn btn-sm" onClick={() => api.post(`api/jobs/${enc(j.id)}/cancel`).then(loadJobs)}>Cancel</button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}
