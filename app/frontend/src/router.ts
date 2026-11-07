import { useEffect, useState } from "react";

export type Route =
  | { view: "chat"; conversation?: string; ask?: string }
  | { view: "graph"; entity?: string }
  | { view: "library" }
  | { view: "document"; id: string }
  | { view: "ingest" }
  | { view: "overview" };

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [pathPart, query = ""] = raw.split("?");
  const parts = pathPart.split("/").filter(Boolean).map(decodeURIComponent);
  const params = new URLSearchParams(query);
  switch (parts[0]) {
    case "graph":
      return { view: "graph", entity: parts[1] ?? params.get("entity") ?? undefined };
    case "library":
      return parts[1] ? { view: "document", id: parts[1] } : { view: "library" };
    case "ingest":
      return { view: "ingest" };
    case "overview":
      return { view: "overview" };
    default:
      return { view: "chat", conversation: parts[1], ask: params.get("ask") ?? undefined };
  }
}

export function href(route: Route): string {
  switch (route.view) {
    case "graph":
      return route.entity ? `#/graph/${encodeURIComponent(route.entity)}` : "#/graph";
    case "library":
      return "#/library";
    case "document":
      return `#/library/${encodeURIComponent(route.id)}`;
    case "ingest":
      return "#/ingest";
    case "overview":
      return "#/overview";
    default: {
      const base = route.conversation ? `#/chat/${encodeURIComponent(route.conversation)}` : "#/chat";
      return route.ask ? `${base}?ask=${encodeURIComponent(route.ask)}` : base;
    }
  }
}

export function navigate(route: Route, replace = false) {
  const h = href(route);
  if (replace) history.replaceState(null, "", h);
  else location.hash = h;
  if (replace) window.dispatchEvent(new HashChangeEvent("hashchange"));
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(location.hash));
  useEffect(() => {
    const on = () => setRoute(parseHash(location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}
