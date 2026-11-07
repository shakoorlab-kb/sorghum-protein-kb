import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, enc, streamSSE } from "../api";
import { ConnectionsGraph, SourcesList, StepsTimeline } from "../components/AnswerParts";
import { ChartCard } from "../components/Chart";
import { EntityChip } from "../components/Chips";
import { Confirm } from "../components/Confirm";
import { Icon } from "../components/Icon";
import { Markdown } from "../components/Markdown";
import { ErrorBox, Spinner } from "../components/States";
import { fmtAgo } from "../format";
import { useInspector } from "../inspector";
import { navigate } from "../router";
import { sourceTarget } from "../targets";
import type { ChartSpec, ChatMessage, ChatResult, Conversation, Source, Step } from "../types";

const STARTERS = [
  "Why does wet cooking reduce sorghum protein digestibility?",
  "Which genes or mutations improve kafirin digestibility, and how?",
  "How do tannins affect protein digestibility in sorghum grain?",
  "What is the role of gamma-kafirin in protein body structure?",
  "How does fermentation change sorghum protein quality?",
  "Which sorghum lines combine high lysine with high digestibility?",
];

interface StoredMessage { id: string; role: "user" | "assistant"; content: string; payload?: Partial<ChatResult> }

export function Chat({ conversationId, ask, theme }: { conversationId?: string; ask?: string; theme: string }) {
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [convErr, setConvErr] = useState<unknown>(null);
  const [activeId, setActiveId] = useState<string | undefined>(conversationId);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingConv, setLoadingConv] = useState(false);
  const [draft, setDraft] = useState(ask ?? "");
  const [busy, setBusy] = useState(false);
  const [toDelete, setToDelete] = useState<Conversation | null>(null);
  const [filter, setFilter] = useState("");
  const abort = useRef<AbortController | null>(null);
  const localIds = useRef(new Set<string>());
  const thread = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  const loadConvs = useCallback(() => {
    api.get<{ items: Conversation[] }>("api/conversations").then((r) => { setConvs(r.items); setConvErr(null); }).catch(setConvErr);
  }, []);
  useEffect(loadConvs, [loadConvs]);

  useEffect(() => {
    if (ask) {
      setDraft(ask);
      setTimeout(() => input.current?.focus(), 50);
    }
  }, [ask]);

  // Open a saved conversation (not one this view just created while streaming).
  useEffect(() => {
    setActiveId(conversationId);
    if (conversationId && localIds.current.has(conversationId)) return;
    if (!conversationId) {
      setMessages([]);
      return;
    }
    setLoadingConv(true);
    api
      .get<{ messages: StoredMessage[] }>(`api/conversations/${enc(conversationId)}`)
      .then((c) => setMessages(c.messages.map((m) => ({ id: m.id, role: m.role, content: m.content, payload: m.payload }))))
      .catch(() => setMessages([{ id: "err", role: "assistant", content: "", error: "This conversation could not be loaded." }]))
      .finally(() => setLoadingConv(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  const stickToBottom = useRef(true);
  useEffect(() => {
    const el = thread.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const send = async (text?: string) => {
    const q = (text ?? draft).trim();
    if (!q || busy) return;
    setDraft("");
    setBusy(true);
    stickToBottom.current = true;
    const uid = `u${Date.now()}`;
    const aid = `a${Date.now()}`;
    setMessages((m) => [
      ...m,
      { id: uid, role: "user", content: q },
      { id: aid, role: "assistant", content: "", pending: true, payload: { steps: [], sources: [], cited: [] } },
    ]);
    const patch = (fn: (m: ChatMessage) => ChatMessage) => setMessages((all) => all.map((m) => (m.id === aid ? fn(m) : m)));
    const ctrl = new AbortController();
    abort.current = ctrl;
    let convId = activeId;
    try {
      await streamSSE(
        "api/chat",
        { question: q, conversation_id: activeId ?? null },
        (ev) => {
          switch (ev.type) {
            case "conversation": {
              const id = String(ev.conversation_id);
              convId = id;
              localIds.current.add(id);
              if (id !== activeId) {
                setActiveId(id);
                history.replaceState(null, "", `#/chat/${encodeURIComponent(id)}`);
              }
              break;
            }
            case "step":
              patch((m) => ({ ...m, payload: { ...m.payload, steps: [...(m.payload?.steps ?? []), ev as unknown as Step] } }));
              break;
            case "sources":
              patch((m) => ({ ...m, payload: { ...m.payload, sources: ev.sources as Source[] } }));
              break;
            case "chart":
              patch((m) => ({ ...m, payload: { ...m.payload, chart: ev.chart as ChartSpec } }));
              break;
            case "token":
              patch((m) => ({ ...m, content: m.content + String(ev.text ?? "") }));
              break;
            case "final": {
              const r = ev.result as ChatResult;
              patch((m) => ({ ...m, content: r.answer, payload: r, pending: false }));
              break;
            }
            case "error":
              patch((m) => ({ ...m, error: String(ev.message ?? "error"), pending: false }));
              break;
          }
        },
        ctrl.signal,
      );
    } catch (e) {
      if ((e as Error).name === "AbortError") patch((m) => ({ ...m, error: "Stopped.", pending: false }));
      else patch((m) => ({ ...m, error: (e as Error).message, pending: false }));
    } finally {
      patch((m) => ({ ...m, pending: false }));
      setBusy(false);
      abort.current = null;
      loadConvs();
      if (convId && location.hash !== `#/chat/${encodeURIComponent(convId)}`) {
        history.replaceState(null, "", `#/chat/${encodeURIComponent(convId)}`);
      }
    }
  };

  const newChat = () => {
    abort.current?.abort();
    setActiveId(undefined);
    setMessages([]);
    setDraft("");
    navigate({ view: "chat" });
    setTimeout(() => input.current?.focus(), 30);
  };

  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return f ? convs.filter((c) => c.title.toLowerCase().includes(f)) : convs;
  }, [convs, filter]);

  return (
    <div className="chat-layout">
      <aside className="conv-panel" aria-label="Conversations">
        <button type="button" className="btn btn-primary btn-block" onClick={newChat}>
          <Icon name="plus" /> New chat
        </button>
        <div className="search-field small-field">
          <Icon name="search" size={14} />
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Search chats" aria-label="Search chats" />
        </div>
        {convErr ? <ErrorBox error={convErr} onRetry={loadConvs} /> : null}
        <ul className="conv-list">
          {shown.map((c) => (
            <li key={c.id} className={c.id === activeId ? "active" : ""}>
              <a href={`#/chat/${encodeURIComponent(c.id)}`} className="conv-link" onClick={() => localIds.current.delete(c.id)}>
                <span className="conv-title">{c.title}</span>
                <span className="conv-time">{fmtAgo(c.updated_at)}</span>
              </a>
              <button type="button" className="icon-btn conv-del" aria-label={`Delete ${c.title}`} title="Delete" onClick={() => setToDelete(c)}>
                <Icon name="trash" size={14} />
              </button>
            </li>
          ))}
          {!shown.length && !convErr ? <li className="muted small conv-empty">{filter ? "No matching chats." : "Your chats will appear here."}</li> : null}
        </ul>
      </aside>

      <section className="chat-main">
        <div
          className="thread"
          ref={thread}
          onScroll={(e) => {
            const el = e.currentTarget;
            stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
          }}
        >
          {loadingConv ? <div className="state"><Spinner /> Loading conversation…</div> : null}
          {!messages.length && !loadingConv ? (
            <div className="welcome">
              <div className="welcome-mark"><Icon name="sparkle" size={22} /></div>
              <h1>Ask the sorghum protein literature</h1>
              <p>
                Answers come from the lab's library and its knowledge graph. Every statement is cited, and every citation
                opens the original page with the supporting text outlined.
              </p>
              <div className="starters">
                {STARTERS.map((s) => (
                  <button type="button" key={s} className="starter" onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          {messages.map((m) => (m.role === "user" ? (
            <div key={m.id} className="msg msg-user"><div className="bubble">{m.content}</div></div>
          ) : (
            <AssistantMessage key={m.id} m={m} theme={theme} />
          )))}
        </div>

        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <textarea
            ref={input}
            value={draft}
            rows={1}
            placeholder="Ask about kafirins, digestibility, processing, genes, germplasm…"
            aria-label="Question"
            onChange={(e) => {
              setDraft(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = `${Math.min(e.target.scrollHeight, 200)}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          {busy ? (
            <button type="button" className="btn btn-stop" onClick={() => abort.current?.abort()} aria-label="Stop">
              <Icon name="stop" /> Stop
            </button>
          ) : (
            <button type="submit" className="btn btn-primary btn-send" disabled={!draft.trim()} aria-label="Send">
              <Icon name="send" />
            </button>
          )}
        </form>
        <div className="composer-hint">Enter to send, Shift+Enter for a new line. Answers cite only what the library says.</div>
      </section>

      {toDelete ? (
        <Confirm
          title="Delete this chat?"
          body={`"${toDelete.title}" and its answers will be removed.`}
          confirmLabel="Delete"
          danger
          onCancel={() => setToDelete(null)}
          onConfirm={async () => {
            const id = toDelete.id;
            setToDelete(null);
            await api.del(`api/conversations/${enc(id)}`).catch(() => {});
            if (id === activeId) newChat();
            loadConvs();
          }}
        />
      ) : null}
    </div>
  );
}

function AssistantMessage({ m, theme }: { m: ChatMessage; theme: string }) {
  const { openSource } = useInspector();
  const [tab, setTab] = useState<"sources" | "graph">("sources");
  const p = m.payload ?? {};
  const sources = p.sources ?? [];
  const cited = p.cited ?? [];
  const byN = useMemo(() => new Map(sources.map((s) => [s.n, s])), [sources]);
  const valid = useMemo(() => new Set(sources.map((s) => s.n)), [sources]);
  const titles = useMemo(
    () => Object.fromEntries(sources.map((s) => [s.n, `${s.title}${s.year ? ` (${s.year})` : ""}, p. ${s.page}`])),
    [sources],
  );
  const graph = p.graph;
  const done = !m.pending;

  return (
    <div className="msg msg-assistant">
      <div className="avatar" aria-hidden><Icon name="sparkle" size={14} /></div>
      <div className="msg-body">
        <StepsTimeline steps={p.steps ?? []} working={!!m.pending} />
        {p.chart ? (
          <ChartCard
            chart={p.chart}
            theme={theme}
            onSource={(s) => openSource({ doc_id: s.doc_id, page: s.page || 1, title: s.title, year: s.year, label: "Chart data" })}
          />
        ) : null}
        {m.content ? (
          <Markdown
            text={m.content}
            valid={valid.size ? valid : undefined}
            citedTitles={titles}
            onCite={(n) => {
              const s = byN.get(n);
              if (s) openSource(sourceTarget(s));
            }}
          />
        ) : m.pending ? <div className="typing"><span /><span /><span /></div> : null}
        {m.error ? <div className="state state-error small">{m.error}</div> : null}
        {done && p.entities?.length ? (
          <div className="answer-entities">
            {p.entities.map((e) => <EntityChip key={e.id} name={e.name} type={e.type} onClick={() => navigate({ view: "graph", entity: e.id })} />)}
          </div>
        ) : null}
        {sources.length && (done || m.content) ? (
          <div className="answer-panel">
            <div className="tabs" role="tablist">
              <button type="button" role="tab" aria-selected={tab === "sources"} className={tab === "sources" ? "tab active" : "tab"} onClick={() => setTab("sources")}>
                Sources <span className="tab-count">{cited.length || sources.length}</span>
              </button>
              <button type="button" role="tab" aria-selected={tab === "graph"} className={tab === "graph" ? "tab active" : "tab"} onClick={() => setTab("graph")} disabled={!graph}>
                Connections <span className="tab-count">{graph?.nodes.length ?? 0}</span>
              </button>
              {done && p.elapsed_ms ? <span className="answer-meta">{p.model} · {(p.elapsed_ms / 1000).toFixed(1)} s</span> : null}
            </div>
            {tab === "sources" ? <SourcesList sources={sources} cited={cited} /> : graph ? <ConnectionsGraph graph={graph} theme={theme} /> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
