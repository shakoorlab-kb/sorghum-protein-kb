import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { api, docFileUrl, enc, pageImageUrl, qs } from "../api";
import { relLabel } from "../palette";
import { href } from "../router";
import type { PageView, SourceTarget } from "../types";
import { EntityChip, VerifiedBadge } from "./Chips";
import { Icon } from "./Icon";
import { ErrorBox, Spinner } from "./States";

/** The source of truth: the cited page of the original document, the quote outlined on it. */
export function SourceViewer({ target, onClose, embedded }: { target: SourceTarget; onClose?: () => void; embedded?: boolean }) {
  const [page, setPage] = useState(target.page || 1);
  const [view, setView] = useState<PageView | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [zoom, setZoom] = useState(1);

  useEffect(() => {
    setPage(target.page || 1);
    setZoom(1);
  }, [target]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErr(null);
    api
      .get<PageView>(`api/documents/${enc(target.doc_id)}/page/${page}${qs({ quote: target.quote })}`)
      .then((v) => alive && setView(v))
      .catch((e) => alive && setErr(e))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [target, page]);

  const nPages = view?.n_pages ?? 0;
  const isPdf = (view?.file_type ?? "pdf") === "pdf";
  const cited = page === target.page;

  return (
    <div className={embedded ? "viewer viewer-embedded" : "viewer"}>
      <header className="viewer-head">
        <div className="viewer-title">
          <div className="eyebrow">{target.label ?? "Source"}</div>
          <a className="viewer-doc" href={href({ view: "document", id: target.doc_id })} title="Open in the library">
            {target.title || target.doc_id}
          </a>
          <div className="viewer-meta">
            {target.year ? <span>{target.year}</span> : null}
            <span>Page {target.page}</span>
          </div>
        </div>
        {onClose ? (
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close source viewer" title="Close">
            <Icon name="close" />
          </button>
        ) : null}
      </header>

      {target.claim ? (
        <div className="claim-card">
          <div className="claim-triple">
            {target.claim.subject ? <EntityChip name={target.claim.subject.name} type={target.claim.subject.type} /> : null}
            <span className="rel-arrow">{relLabel(target.claim.relation)} <span aria-hidden>→</span></span>
            {target.claim.object ? <EntityChip name={target.claim.object.name} type={target.claim.object.type} /> : null}
          </div>
          {target.claim.statement ? <p className="claim-statement">{target.claim.statement}</p> : null}
          {target.claim.value ? <p className="claim-context"><span>Value</span> {target.claim.value}</p> : null}
          {target.claim.context ? <p className="claim-context"><span>Context</span> {target.claim.context}</p> : null}
          <div className="claim-flags">
            <VerifiedBadge verified={target.claim.verified} />
            {target.claim.kind ? <span className="badge">{target.claim.kind}</span> : null}
            {typeof target.claim.confidence === "number" ? (
              <span className="badge">confidence {Math.round(target.claim.confidence * 100)}%</span>
            ) : null}
          </div>
        </div>
      ) : null}

      {target.quote ? (
        <blockquote className="viewer-quote">
          <Icon name="quote" size={14} />
          <span>{target.quote}</span>
        </blockquote>
      ) : null}

      <div className="viewer-toolbar">
        <div className="pager">
          <button type="button" className="icon-btn" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} aria-label="Previous page"><Icon name="left" /></button>
          <span className="pager-label">
            Page <strong>{page}</strong>{nPages ? ` of ${nPages}` : ""}
            {cited ? <span className="cited-tag">cited</span> : null}
          </span>
          <button type="button" className="icon-btn" disabled={!!nPages && page >= nPages} onClick={() => setPage((p) => p + 1)} aria-label="Next page"><Icon name="right" /></button>
          {!cited ? <button type="button" className="btn btn-sm" onClick={() => setPage(target.page)}>Back to cited page</button> : null}
        </div>
        <div className="toolbar-right">
          {isPdf ? (
            <>
              <button type="button" className="icon-btn" onClick={() => setZoom((z) => Math.max(0.6, z - 0.2))} aria-label="Zoom out"><Icon name="zoomOut" /></button>
              <span className="zoom-label">{Math.round(zoom * 100)}%</span>
              <button type="button" className="icon-btn" onClick={() => setZoom((z) => Math.min(3, z + 0.2))} aria-label="Zoom in"><Icon name="zoomIn" /></button>
            </>
          ) : null}
          <a className="btn btn-sm" href={docFileUrl(target.doc_id, isPdf ? page : undefined)} target="_blank" rel="noreferrer">
            <Icon name="external" size={14} /> Open original
          </a>
        </div>
      </div>

      {view?.suggested_page && view.suggested_page !== page && (!view.rects || view.rects.length === 0) ? (
        <div className="notice">
          The quote was not found on this page. It appears on page {view.suggested_page}.
          <button type="button" className="btn btn-sm" onClick={() => setPage(view.suggested_page!)}>Go to page {view.suggested_page}</button>
        </div>
      ) : null}
      {view && isPdf && target.quote && cited && view.rects.length === 0 && !view.suggested_page ? (
        <div className="notice notice-muted">
          This page has no exact text match to outline (it may be a scanned page read by OCR). Read the page to confirm the quote.
        </div>
      ) : null}

      <div className="viewer-body">
        {err ? <ErrorBox error={err} /> : null}
        {loading && !view ? <div className="viewer-loading"><Spinner /> Loading page…</div> : null}
        {view && !err ? (
          isPdf ? (
            <PdfPage docId={target.doc_id} view={view} zoom={zoom} loading={loading} />
          ) : (
            <TextPage text={view.text} quote={target.quote} />
          )
        ) : null}
      </div>
    </div>
  );
}

function PdfPage({ docId, view, zoom, loading }: { docId: string; view: PageView; zoom: number; loading: boolean }) {
  const wrap = useRef<HTMLDivElement>(null);
  const img = useRef<HTMLImageElement>(null);
  const [boxW, setBoxW] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [imgErr, setImgErr] = useState(false);
  const src = pageImageUrl(docId, view.page, zoom > 1.4 ? 180 : 110);

  useEffect(() => {
    setLoaded(false);
    setImgErr(false);
  }, [src]);

  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const measure = () => setBoxW(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const width = Math.max(200, (boxW - 2) * zoom);
  const scale = view.width ? width / view.width : 1;

  // Scroll the first highlight into view once the page is drawn.
  useEffect(() => {
    if (!loaded || !view.rects.length || !wrap.current) return;
    const first = wrap.current.querySelector(".hl-box") as HTMLElement | null;
    first?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [loaded, view.rects]);

  return (
    <div ref={wrap} className="pdf-wrap">
      <div className="pdf-page" style={{ width }}>
        {imgErr ? (
          <div className="state state-error">Could not render this page.</div>
        ) : (
          <img
            ref={img}
            src={src}
            alt={`Page ${view.page}`}
            style={{ width, opacity: loading ? 0.6 : 1 }}
            onLoad={() => setLoaded(true)}
            onError={() => setImgErr(true)}
          />
        )}
        {loaded && view.width
          ? view.rects.map((r, i) => (
              <span
                key={i}
                className="hl-box"
                style={{ left: r[0] * scale - 2, top: r[1] * scale - 1, width: (r[2] - r[0]) * scale + 4, height: (r[3] - r[1]) * scale + 2 }}
              />
            ))
          : null}
      </div>
    </div>
  );
}

function escapeRe(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function TextPage({ text, quote }: { text: string; quote?: string }) {
  const parts = useMemo(() => {
    if (!quote || quote.trim().length < 8) return [{ t: text, hl: false }];
    const words = quote.trim().split(/\s+/).slice(0, 60).map(escapeRe);
    const tries = [words, words.slice(0, 12), words.slice(-10)];
    for (const w of tries) {
      if (w.length < 3) continue;
      const re = new RegExp(w.join("\\s+"), "i");
      const m = re.exec(text);
      if (m) {
        return [
          { t: text.slice(0, m.index), hl: false },
          { t: m[0], hl: true },
          { t: text.slice(m.index + m[0].length), hl: false },
        ];
      }
    }
    return [{ t: text, hl: false }];
  }, [text, quote]);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector("mark")?.scrollIntoView({ block: "center" });
  }, [parts]);
  return (
    <div ref={ref} className="text-page">
      {parts.map((p, i) => (p.hl ? <mark key={i}>{p.t}</mark> : <span key={i}>{p.t}</span>))}
    </div>
  );
}
