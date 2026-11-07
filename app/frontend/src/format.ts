export function fmtInt(n?: number | null): string {
  return (n ?? 0).toLocaleString("en-US");
}

export function fmtCost(c?: number | null): string {
  const v = c ?? 0;
  return v < 0.01 && v > 0 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
}

export function fmtAgo(ts?: number | null): string {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)} d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export function shorten(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export function authorsShort(a?: string[]): string {
  if (!a || a.length === 0) return "";
  const last = (x: string) => {
    const parts = x.trim().split(/\s+/);
    return parts.length > 1 && parts[parts.length - 1].length > 2 ? parts[parts.length - 1] : parts[0];
  };
  if (a.length === 1) return last(a[0]);
  if (a.length === 2) return `${last(a[0])} & ${last(a[1])}`;
  return `${last(a[0])} et al.`;
}
