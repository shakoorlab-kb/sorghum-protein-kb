import { useCallback, useEffect, useMemo, useState } from "react";

import { api, setUnauthorizedHandler } from "./api";
import { Icon } from "./components/Icon";
import { SourceViewer } from "./components/SourceViewer";
import { Spinner } from "./components/States";
import { InspectorContext } from "./inspector";
import { href, type Route, useRoute } from "./router";
import type { SourceTarget } from "./types";
import { Chat } from "./views/Chat";
import { DocumentView } from "./views/DocumentView";
import { GraphExplorer } from "./views/GraphExplorer";
import { Ingest } from "./views/Ingest";
import { Library } from "./views/Library";
import { Login, Logo } from "./views/Login";
import { Overview } from "./views/Overview";

type Theme = "light" | "dark";

function storedTheme(): Theme | null {
  try {
    const t = localStorage.getItem("si-theme");
    return t === "light" || t === "dark" ? t : null;
  } catch {
    return null;
  }
}

function systemTheme(): Theme {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

const NAV: { view: Route["view"]; label: string; icon: string; to: Route }[] = [
  { view: "chat", label: "Chat", icon: "chat", to: { view: "chat" } },
  { view: "graph", label: "Graph", icon: "graph", to: { view: "graph" } },
  { view: "library", label: "Library", icon: "library", to: { view: "library" } },
  { view: "ingest", label: "Ingest", icon: "upload", to: { view: "ingest" } },
  { view: "overview", label: "Overview", icon: "dashboard", to: { view: "overview" } },
];

export function App() {
  const route = useRoute();
  const [user, setUser] = useState<string | null | undefined>(undefined);
  const [theme, setTheme] = useState<Theme>(() => storedTheme() ?? systemTheme());
  const [source, setSource] = useState<SourceTarget | null>(null);
  const [lastChat, setLastChat] = useState<{ conversation?: string; ask?: string }>({});

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    if (storedTheme()) return;
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    const on = () => setTheme(mq.matches ? "dark" : "light");
    mq?.addEventListener?.("change", on);
    return () => mq?.removeEventListener?.("change", on);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    api.get<{ user: string }>("api/auth/me").then((r) => setUser(r.user)).catch(() => setUser(null));
  }, []);

  useEffect(() => {
    if (route.view === "chat") setLastChat({ conversation: route.conversation, ask: route.ask });
  }, [route]);

  // Close the viewer with Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && source && !document.querySelector(".modal")) setSource(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [source]);

  const inspector = useMemo(() => ({ openSource: (t: SourceTarget) => setSource({ ...t }), close: () => setSource(null) }), []);

  const toggleTheme = useCallback(() => {
    setTheme((t) => {
      const next = t === "dark" ? "light" : "dark";
      try {
        localStorage.setItem("si-theme", next);
      } catch {
        /* storage unavailable; theme still applies for this visit */
      }
      return next;
    });
  }, []);

  const logout = async () => {
    await api.post("api/auth/logout").catch(() => {});
    setUser(null);
  };

  if (user === undefined) {
    return <div className="boot"><Spinner size={22} /></div>;
  }
  if (user === null) return <Login onLogin={(u) => setUser(u)} />;

  const active = route.view === "document" ? "library" : route.view;
  const docked = route.view === "chat";

  return (
    <InspectorContext.Provider value={inspector}>
      <div className={`shell ${source && docked ? "with-inspector" : ""}`}>
        <nav className="rail" aria-label="Main">
          <a className="brand" href={href({ view: "chat" })} title="Sorghum Protein Intelligence">
            <span className="brand-mark"><Logo /></span>
            <span className="brand-text">
              <span className="brand-name">Sorghum Protein</span>
              <span className="brand-sub">Intelligence</span>
            </span>
          </a>
          <ul className="nav">
            {NAV.map((n) => (
              <li key={n.view}>
                <a href={href(n.to)} className={active === n.view ? "nav-item active" : "nav-item"} aria-current={active === n.view ? "page" : undefined}>
                  <Icon name={n.icon} size={18} />
                  <span>{n.label}</span>
                </a>
              </li>
            ))}
          </ul>
          <div className="rail-foot">
            <button type="button" className="nav-item" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
              <Icon name={theme === "dark" ? "sun" : "moon"} size={18} />
              <span>{theme === "dark" ? "Light" : "Dark"}</span>
            </button>
            <div className="user-row">
              <span className="user-avatar">{user.slice(0, 1).toUpperCase()}</span>
              <span className="user-name">{user}</span>
              <button type="button" className="icon-btn" onClick={logout} aria-label="Sign out" title="Sign out">
                <Icon name="logout" size={16} />
              </button>
            </div>
          </div>
        </nav>

        <main className="main">
          <div className={route.view === "chat" ? "view-host" : "view-host hidden"}>
            <Chat conversationId={route.view === "chat" ? route.conversation : lastChat.conversation} ask={route.view === "chat" ? route.ask : lastChat.ask} theme={theme} />
          </div>
          {route.view === "graph" ? <div className="view-host"><GraphExplorer entity={route.entity} theme={theme} /></div> : null}
          {route.view === "library" ? <div className="view-host scroll"><Library /></div> : null}
          {route.view === "document" ? <div className="view-host scroll"><DocumentView id={route.id} theme={theme} /></div> : null}
          {route.view === "ingest" ? <div className="view-host scroll"><Ingest /></div> : null}
          {route.view === "overview" ? <div className="view-host scroll"><Overview /></div> : null}
        </main>

        {source ? (
          <>
            {!docked ? <div className="drawer-backdrop" onClick={() => setSource(null)} /> : null}
            <aside className={docked ? "inspector docked" : "inspector drawer"} aria-label="Source viewer">
              <SourceViewer target={source} onClose={() => setSource(null)} />
            </aside>
          </>
        ) : null}
      </div>
    </InspectorContext.Provider>
  );
}
