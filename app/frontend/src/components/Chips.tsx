import { typeColor, typeLabel } from "../palette";

export function TypeDot({ type, size = 8 }: { type?: string; size?: number }) {
  return <span className="type-dot" style={{ background: typeColor(type), width: size, height: size }} />;
}

export function TypeBadge({ type }: { type?: string }) {
  return (
    <span className="type-badge" style={{ ["--c" as string]: typeColor(type) }}>
      {typeLabel(type)}
    </span>
  );
}

export function EntityChip({
  name, type, onClick, count, title,
}: { name: string; type?: string; onClick?: () => void; count?: number; title?: string }) {
  const body = (
    <>
      <TypeDot type={type} />
      <span className="chip-label">{name}</span>
      {count !== undefined ? <span className="chip-count">{count}</span> : null}
    </>
  );
  return onClick ? (
    <button type="button" className="entity-chip" style={{ ["--c" as string]: typeColor(type) }} onClick={onClick} title={title ?? `${typeLabel(type)}: ${name}`}>
      {body}
    </button>
  ) : (
    <span className="entity-chip" style={{ ["--c" as string]: typeColor(type) }} title={title ?? `${typeLabel(type)}: ${name}`}>
      {body}
    </span>
  );
}

export function VerifiedBadge({ verified }: { verified?: boolean | null }) {
  return verified ? (
    <span className="badge badge-ok" title="The quote was found verbatim on the cited page">Verified quote</span>
  ) : (
    <span className="badge badge-warn" title="The quote could not be matched exactly on the page; read the page before relying on it">Unverified</span>
  );
}

export function StatusPill({ status }: { status: string }) {
  const cls = status === "done" || status === "ready" ? "ok" : status === "failed" || status === "extract_failed" ? "err" : status === "running" ? "run" : "muted";
  const label: Record<string, string> = {
    done: "Done", failed: "Failed", running: "Running", queued: "Queued", ready: "Graph ready",
    indexed: "Indexed", extract_failed: "Extraction failed",
  };
  return <span className={`pill pill-${cls}`}>{label[status] ?? status}</span>;
}
