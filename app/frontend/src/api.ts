/** API client. Every URL is relative (no leading slash) so the console works both at
 *  the server root and behind the /sorghum-intel/ prefix. */

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

type Unauthorized = () => void;
let onUnauthorized: Unauthorized = () => {};
export function setUnauthorizedHandler(fn: Unauthorized) {
  onUnauthorized = fn;
}

async function parseError(r: Response): Promise<string> {
  try {
    const j = await r.json();
    if (typeof j.detail === "string") return j.detail;
    return JSON.stringify(j.detail ?? j);
  } catch {
    return `${r.status} ${r.statusText}`;
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(path, { credentials: "same-origin", ...init });
  if (r.status === 401 && !path.startsWith("api/auth/")) {
    onUnauthorized();
    throw new ApiError(401, "login required");
  }
  if (!r.ok) throw new ApiError(r.status, await parseError(r));
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  del: <T,>(path: string) => request<T>(path, { method: "DELETE" }),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  patch: <T,>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  form: <T,>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
};

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const enc = encodeURIComponent;

/** POST and read a server-sent-event stream ("data: {json}" lines). */
export async function streamSSE(
  path: string,
  body: unknown,
  onEvent: (ev: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (r.status === 401) {
    onUnauthorized();
    throw new ApiError(401, "login required");
  }
  if (!r.ok || !r.body) throw new ApiError(r.status, await parseError(r));
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of block.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (!payload) continue;
        try {
          onEvent(JSON.parse(payload));
        } catch {
          /* ignore a malformed line */
        }
      }
    }
  }
}

export function docFileUrl(docId: string, page?: number) {
  return `api/documents/${enc(docId)}/file${page ? `#page=${page}` : ""}`;
}

export function pageImageUrl(docId: string, page: number, dpi = 110) {
  return `api/documents/${enc(docId)}/page/${page}.png?dpi=${dpi}`;
}
