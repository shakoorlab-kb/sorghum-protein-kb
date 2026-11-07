import cytoscape from "cytoscape";
import type { Core, ElementDefinition, EventObject } from "cytoscape";
import fcose from "cytoscape-fcose";
import { useEffect, useMemo, useRef } from "react";

import { NEGATIVE_RELS, POSITIVE_RELS, relLabel, typeColor } from "../palette";
import type { GraphEdge, GraphNode, GraphView } from "../types";
import { Icon } from "./Icon";

cytoscape.use(fcose);

function cssVar(name: string, fallback: string) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function nodeSize(n: GraphNode): number {
  if (n.kind === "document") return 22;
  const w = (n.n_docs ?? 0) + (n.degree ?? 0) / 4;
  return Math.min(64, 16 + 7 * Math.sqrt(Math.max(w, 0)));
}

function edgeColor(e: GraphEdge, neutral: string): string {
  if (POSITIVE_RELS.has(e.type)) return "#16a34a";
  if (NEGATIVE_RELS.has(e.type)) return "#dc2626";
  if (e.type === "DISCUSSES" || e.type === "CITES") return neutral;
  return neutral;
}

function label(s: string, max = 26) {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

export function GraphCanvas({
  view, height = 420, onNodeClick, onEdgeClick, onBackground, highlightPath, selectedId, theme, emptyText,
}: {
  view: GraphView | null;
  height?: number | string;
  onNodeClick?: (n: GraphNode) => void;
  onEdgeClick?: (e: GraphEdge) => void;
  onBackground?: () => void;
  highlightPath?: string[] | null;
  selectedId?: string | null;
  theme?: string;
  emptyText?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const handlers = useRef({ onNodeClick, onEdgeClick, onBackground });
  handlers.current = { onNodeClick, onEdgeClick, onBackground };

  const elements = useMemo<ElementDefinition[]>(() => {
    if (!view) return [];
    const ids = new Set(view.nodes.map((n) => n.id));
    const neutral = "#94a3b8";
    const nodes: ElementDefinition[] = view.nodes.map((n) => ({
      group: "nodes",
      data: {
        id: n.id,
        label: label(n.name || n.title || n.id, n.kind === "document" ? 30 : 26),
        color: typeColor(n.kind === "document" ? "DOCUMENT" : n.type),
        size: nodeSize(n),
        raw: n,
      },
      classes: [n.kind === "document" ? "doc" : "ent", n.focus || n.id === view.center ? "focus" : ""].join(" "),
    }));
    const seen = new Set<string>();
    const edges: ElementDefinition[] = [];
    for (const e of view.edges) {
      if (!ids.has(e.source) || !ids.has(e.target)) continue;
      const id = `${e.source}|${e.type}|${e.target}`;
      if (seen.has(id)) continue;
      seen.add(id);
      const unverified = e.verified !== undefined && e.verified !== null && Number(e.verified) === 0;
      edges.push({
        group: "edges",
        data: {
          id, source: e.source, target: e.target,
          label: e.type === "DISCUSSES" ? "" : relLabel(e.type),
          color: edgeColor(e, neutral),
          w: Math.min(7, 1 + Math.sqrt(e.n_docs ?? e.n ?? 1)),
          raw: e,
        },
        classes: [e.type === "DISCUSSES" || e.type === "CITES" ? "meta" : "rel", unverified ? "unverified" : ""].join(" "),
      });
    }
    return [...nodes, ...edges];
  }, [view]);

  // Create once.
  useEffect(() => {
    if (!box.current) return;
    const inst = cytoscape({
      container: box.current,
      elements: [],
      minZoom: 0.15,
      maxZoom: 3.5,
      wheelSensitivity: 0.25,
      boxSelectionEnabled: false,
    });
    inst.on("tap", "node", (ev: EventObject) => handlers.current.onNodeClick?.(ev.target.data("raw")));
    inst.on("tap", "edge", (ev: EventObject) => handlers.current.onEdgeClick?.(ev.target.data("raw")));
    inst.on("tap", (ev: EventObject) => {
      if (ev.target === inst) handlers.current.onBackground?.();
    });
    inst.on("mouseover", "node", (ev: EventObject) => {
      const n = ev.target;
      inst.elements().addClass("dim");
      n.closedNeighborhood().removeClass("dim");
      if (box.current) box.current.style.cursor = "pointer";
    });
    inst.on("mouseout", "node", () => {
      inst.elements().removeClass("dim");
      if (box.current) box.current.style.cursor = "";
    });
    inst.on("mouseover", "edge", () => box.current && (box.current.style.cursor = "pointer"));
    inst.on("mouseout", "edge", () => box.current && (box.current.style.cursor = ""));
    cy.current = inst;
    return () => {
      inst.destroy();
      cy.current = null;
    };
  }, []);

  // Style follows the theme.
  useEffect(() => {
    const inst = cy.current;
    if (!inst) return;
    const text = cssVar("--text", "#1f2328");
    const bg = cssVar("--surface", "#ffffff");
    const muted = cssVar("--text-muted", "#6b7280");
    const accent = cssVar("--accent", "#b45309");
    inst.style([
      {
        selector: "node",
        style: {
          "background-color": "data(color)",
          width: "data(size)",
          height: "data(size)",
          label: "data(label)",
          color: text,
          // Bigger nodes get bigger labels, so hubs stay readable when zoomed out.
          "font-size": "mapData(size, 16, 64, 11, 20)",
          "font-family": "Inter, system-ui, sans-serif",
          "font-weight": 500,
          "text-valign": "bottom",
          "text-margin-y": 4,
          "text-outline-color": bg,
          "text-outline-width": 2.5,
          "border-width": 2,
          "border-color": bg,
          "min-zoomed-font-size": 6,
          "transition-property": "opacity",
          "transition-duration": 150,
        },
      },
      { selector: "node.doc", style: { shape: "round-rectangle", width: 26, height: 18, "font-size": 10, color: muted } },
      { selector: "node.focus", style: { "border-width": 4, "border-color": accent, "font-weight": 700, "font-size": 12.5 } },
      { selector: "node:selected, node.selected", style: { "border-width": 4, "border-color": accent, "overlay-opacity": 0 } },
      {
        selector: "edge",
        style: {
          width: "data(w)",
          "line-color": "data(color)",
          "target-arrow-color": "data(color)",
          "target-arrow-shape": "triangle",
          "arrow-scale": 0.8,
          "curve-style": "bezier",
          opacity: 0.7,
          label: "data(label)",
          "font-size": 9,
          color: muted,
          "text-rotation": "autorotate",
          "text-background-color": bg,
          "text-background-opacity": 0.85,
          "text-background-padding": "1.5px",
          "min-zoomed-font-size": 9,
          "transition-property": "opacity",
          "transition-duration": 150,
        },
      },
      { selector: "edge.meta", style: { "line-style": "dotted", opacity: 0.45, "target-arrow-shape": "none", width: 1.2 } },
      { selector: "edge.unverified", style: { "line-style": "dashed" } },
      { selector: ".dim", style: { opacity: 0.12 } },
      { selector: "edge.path", style: { "line-color": accent, "target-arrow-color": accent, width: 6, opacity: 1, "z-index": 10 } },
      { selector: "node.path", style: { "border-width": 4, "border-color": accent } },
      { selector: ".faded", style: { opacity: 0.15 } },
    ]);
  }, [theme]);

  // Data.
  useEffect(() => {
    const inst = cy.current;
    if (!inst) return;
    inst.elements().remove();
    if (!elements.length) return;
    inst.add(elements);
    const n = inst.nodes().length;
    inst.layout({
      name: "fcose",
      quality: n > 120 ? "draft" : "default",
      randomize: true,
      animate: false,
      nodeRepulsion: () => (n > 50 ? 26000 : n > 30 ? 9000 : 4500),
      idealEdgeLength: () => (n > 50 ? 130 : n > 30 ? 100 : 70),
      edgeElasticity: () => 0.3,
      gravity: n > 50 ? 0.12 : 0.3,
      gravityRangeCompound: 1.5,
      nodeSeparation: 80,
      numIter: 3500,
      padding: 24,
    } as cytoscape.LayoutOptions).run();
    inst.fit(undefined, 24);
    // Small maps in narrow panels: keep labels legible rather than fitting everything.
    if (n <= 40 && inst.zoom() < 0.75) {
      inst.zoom(0.75);
      const focus = inst.nodes(".focus");
      inst.center(focus.length ? focus : undefined);
    }
  }, [elements]);

  // Selection + path highlight.
  useEffect(() => {
    const inst = cy.current;
    if (!inst) return;
    inst.elements().removeClass("selected path faded");
    if (selectedId) inst.getElementById(selectedId).addClass("selected");
    if (highlightPath && highlightPath.length > 1) {
      const keep = inst.collection();
      highlightPath.forEach((id, i) => {
        const node = inst.getElementById(id);
        keep.merge(node);
        node.addClass("path");
        const next = highlightPath[i + 1];
        if (next) {
          const e = inst.edges().filter((ed) =>
            (ed.data("source") === id && ed.data("target") === next) || (ed.data("source") === next && ed.data("target") === id));
          e.addClass("path");
          keep.merge(e);
        }
      });
      inst.elements().not(keep).addClass("faded");
    }
  }, [highlightPath, selectedId, elements]);

  const zoom = (f: number) => {
    const inst = cy.current;
    if (!inst) return;
    inst.zoom({ level: inst.zoom() * f, renderedPosition: { x: inst.width() / 2, y: inst.height() / 2 } });
  };

  const empty = !view || view.nodes.length === 0;
  return (
    <div className="graph-canvas" style={{ height }}>
      <div ref={box} className="graph-cy" />
      {empty ? <div className="graph-empty">{emptyText ?? "Nothing to show yet."}</div> : null}
      <div className="graph-controls">
        <button type="button" className="icon-btn" onClick={() => zoom(1.25)} aria-label="Zoom in" title="Zoom in"><Icon name="zoomIn" /></button>
        <button type="button" className="icon-btn" onClick={() => zoom(0.8)} aria-label="Zoom out" title="Zoom out"><Icon name="zoomOut" /></button>
        <button type="button" className="icon-btn" onClick={() => cy.current?.fit(undefined, 24)} aria-label="Fit to view" title="Fit"><Icon name="fit" /></button>
      </div>
    </div>
  );
}

export function GraphLegend({ view }: { view: GraphView | null }) {
  if (!view) return null;
  const types = [...new Set(view.nodes.map((n) => (n.kind === "document" ? "DOCUMENT" : n.type)))];
  return (
    <div className="graph-legend">
      {types.map((t) => (
        <span key={t} className="legend-item">
          <span className="type-dot" style={{ background: typeColor(t) }} />
          {t.charAt(0) + t.slice(1).toLowerCase()}
        </span>
      ))}
      <span className="legend-item"><span className="legend-line pos" />increases / improves</span>
      <span className="legend-item"><span className="legend-line neg" />decreases / impairs</span>
      <span className="legend-item"><span className="legend-line dashed" />unverified only</span>
    </div>
  );
}
