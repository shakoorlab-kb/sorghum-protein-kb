import type { ReactNode } from "react";

import { Icon } from "./Icon";

export function Spinner({ size = 16 }: { size?: number }) {
  return <span className="spinner" style={{ width: size, height: size }} aria-label="Loading" />;
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="state">
      <Spinner /> <span>{label}…</span>
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="state state-error" role="alert">
      <Icon name="alert" />
      <span>{msg}</span>
      {onRetry ? <button type="button" className="btn btn-sm" onClick={onRetry}>Retry</button> : null}
    </div>
  );
}

export function Empty({ icon = "sparkle", title, children }: { icon?: string; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-icon"><Icon name={icon} size={22} /></div>
      <div className="empty-title">{title}</div>
      {children ? <div className="empty-body">{children}</div> : null}
    </div>
  );
}
