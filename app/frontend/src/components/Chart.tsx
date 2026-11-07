import { useLayoutEffect, useMemo, useRef, useState } from "react";

import type { ChartPoint, ChartSpec } from "../types";
import { Icon } from "./Icon";

/**
 * A chart in a chat answer. Every value comes from a cited source (a supplementary table or a
 * number found in the evidence); the source line under the chart opens it.
 *
 * Colors: the validated reference categorical palette in fixed slot order, its own steps per
 * theme. Scatter and line keep to three series (the all-pairs safe set) plus "Other"; bars to
 * eight. Three light slots sit under 3:1 contrast, so the legend and the table view are always
 * available and identity never rests on color alone.
 */

const SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
const OTHER = { light: "#a8a6a0", dark: "#6b6a66" };

const H = 320;
const PAD = { top: 12, right: 18, bottom: 46, left: 56 };

function fmt(v: number): string {
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e5 || a < 1e-3)) return v.toExponential(2);
  return Number(v.toPrecision(4)).toLocaleString(undefined, { maximumFractionDigits: 3 });
}

function niceTicks(min: number, max: number, count = 5): number[] {
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) ?? 10 * mag;
  // The axis always extends to the tick at or beyond the largest value, so no mark overruns it.
  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let t = start; t <= end + step * 1e-9; t += step) ticks.push(Number(t.toPrecision(12)));
  return ticks;
}

function seriesOrder(points: ChartPoint[]): string[] {
  const seen: string[] = [];
  for (const p of points) if (!seen.includes(p.series)) seen.push(p.series);
  return [...seen.filter((s) => s !== "Other"), ...(seen.includes("Other") ? ["Other"] : [])];
}

/** Rectangle with a 4px rounded end on the data side only (right end for horizontal bars). */
function barPath(x0: number, x1: number, y: number, h: number): string {
  const r = Math.min(4, Math.abs(x1 - x0) / 2, h / 2);
  if (x1 >= x0) {
    return `M${x0},${y} H${x1 - r} Q${x1},${y} ${x1},${y + r} V${y + h - r} Q${x1},${y + h} ${x1 - r},${y + h} H${x0} Z`;
  }
  return `M${x0},${y} H${x1 + r} Q${x1},${y} ${x1},${y + r} V${y + h - r} Q${x1},${y + h} ${x1 + r},${y + h} H${x0} Z`;
}

interface Tip { x: number; y: number; lines: string[] }

/** Some stored titles carry escaped HTML ("&lt;i&gt;in vitro&lt;/i&gt;"); show plain text. */
function plainTitle(t: string): string {
  const el = document.createElement("textarea");
  el.innerHTML = t;
  el.innerHTML = el.value;
  return el.value.replace(/<[^>]+>/g, "");
}

export function ChartCard({ chart, theme, onSource }: {
  chart: ChartSpec;
  theme: string;
  onSource: (s: ChartSpec["sources"][number]) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [showTable, setShowTable] = useState(false);
  const [tip, setTip] = useState<Tip | null>(null);

  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.max(320, el.clientWidth)));
    ro.observe(el);
    setWidth(Math.max(320, el.clientWidth));
    return () => ro.disconnect();
  }, []);

  const dark = theme === "dark";
  const palette = dark ? SERIES_DARK : SERIES_LIGHT;
  const series = useMemo(() => seriesOrder(chart.points), [chart.points]);
  const color = (s: string) => (s === "Other" ? (dark ? OTHER.dark : OTHER.light) : palette[series.indexOf(s) % palette.length]);
  const multi = series.length > 1;
  const yName = chart.y_label + (chart.unit && !chart.y_label.includes(chart.unit) ? ` (${chart.unit})` : "");

  const csv = () => {
    const head = ["series", "x", "y", "label", "evidence"];
    const rows = chart.points.map((p) => [p.series, p.x, p.y, p.label ?? "", p.n ?? ""]);
    const body = [head, ...rows].map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([body], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(chart.title || "chart").replace(/[^\w]+/g, "_").slice(0, 60)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <figure className="chart-card">
      <div className="chart-head">
        <figcaption className="chart-title">{chart.title}</figcaption>
        <div className="chart-tools">
          <button type="button" className="btn btn-ghost small" onClick={() => setShowTable((v) => !v)} aria-pressed={showTable}>
            <Icon name={showTable ? "chart" : "table"} size={14} /> {showTable ? "Chart" : "Table"}
          </button>
          <button type="button" className="btn btn-ghost small" onClick={csv}><Icon name="download" size={14} /> CSV</button>
        </div>
      </div>
      {multi ? (
        <div className="chart-legend" aria-label="Legend">
          {series.map((s) => (
            <span key={s} className="chart-legend-item"><i style={{ background: color(s) }} />{s}</span>
          ))}
        </div>
      ) : null}
      <div ref={wrap} className="chart-plot" onMouseLeave={() => setTip(null)}>
        {showTable ? (
          <div className="chart-table-wrap">
            <table className="chart-table">
              <thead><tr>{multi ? <th>Series</th> : null}<th>{chart.x_label || "x"}</th><th className="num">{yName || "y"}</th><th>Label</th></tr></thead>
              <tbody>
                {chart.points.map((p, i) => (
                  <tr key={i}>{multi ? <td>{p.series}</td> : null}<td>{typeof p.x === "number" ? fmt(p.x) : p.x}</td><td className="num">{fmt(p.y)}</td><td>{p.label}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : chart.type === "scatter" || chart.type === "line" ? (
          <XYPlot chart={chart} width={width} color={color} series={series} setTip={setTip} dark={dark} yName={yName} />
        ) : (
          <BarPlot chart={chart} width={width} color={color} series={series} setTip={setTip} dark={dark} yName={yName} />
        )}
        {tip && !showTable ? (
          <div className="chart-tip" style={{ left: Math.min(tip.x + 14, width - 220), top: Math.max(0, tip.y - 12) }}>
            {tip.lines.map((l, i) => <div key={i} className={i === 0 ? "chart-tip-head" : undefined}>{l}</div>)}
          </div>
        ) : null}
      </div>
      <div className="chart-foot">
        <div className="chart-foot-label">Data</div>
        <ul className="chart-sources">
          {chart.sources.map((s) => (
            <li key={s.doc_id}>
              <button type="button" className="chart-source" onClick={() => onSource(s)}>
                {s.n ? <span className="chart-source-n">[{s.n}]</span> : null}
                {plainTitle(s.title)}{s.year ? ` (${s.year})` : ""}
              </button>
            </li>
          ))}
        </ul>
        <div className="muted">
          {chart.source === "table"
            ? `${chart.points.length} rows plotted straight from the supplementary table${chart.n_rows ? ` (${chart.n_rows} rows in it)` : ""}`
            : `Every value checked against its cited evidence${chart.dropped ? `; ${chart.dropped} unverifiable value(s) left out` : ""}`}
          {chart.truncated ? " · showing the 30 highest values" : ""}
        </div>
      </div>
    </figure>
  );
}

type PlotProps = {
  chart: ChartSpec; width: number; series: string[]; dark: boolean; yName: string;
  color: (s: string) => string; setTip: (t: Tip | null) => void;
};

function BarPlot({ chart, width, color, series, setTip, yName }: PlotProps) {
  // Horizontal bars: categories down the side, so long genotype or treatment names stay readable.
  const cats = useMemo(() => {
    const out: string[] = [];
    for (const p of chart.points) {
      const c = String(p.label || p.x);
      if (!out.includes(c)) out.push(c);
    }
    return out;
  }, [chart.points]);
  const grouped = chart.type === "grouped_bar" && series.length > 1;
  const barH = grouped ? 10 : 16;
  const rowH = grouped ? series.length * (barH + 2) + 10 : barH + 8;
  const labelW = Math.min(Math.round(width * 0.42), 320, Math.max(70, ...cats.map((c) => c.length * 6.3)));
  const maxChars = Math.floor(labelW / 6.3);
  const height = Math.max(120, cats.length * rowH + PAD.top + PAD.bottom);
  const left = labelW + 10;
  const plotW = width - left - PAD.right;
  const vals = chart.points.map((p) => p.y);
  const ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals));
  const t0 = ticks[0], t1 = ticks[ticks.length - 1];
  const sx = (v: number) => left + ((v - t0) / (t1 - t0)) * plotW;

  return (
    <svg width={width} height={height} role="img" aria-label={chart.title}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={sx(t)} x2={sx(t)} y1={PAD.top} y2={height - PAD.bottom} className={t === 0 ? "chart-baseline" : "chart-grid"} />
          <text x={sx(t)} y={height - PAD.bottom + 16} className="chart-tick" textAnchor="middle">{fmt(t)}</text>
        </g>
      ))}
      <text x={left + plotW / 2} y={height - 8} className="chart-axis-label" textAnchor="middle">{yName}</text>
      {cats.map((c, ci) => {
        const y0 = PAD.top + ci * rowH + 4;
        const pts = chart.points.filter((p) => String(p.label || p.x) === c);
        return (
          <g key={c}>
            <text x={left - 8} y={y0 + (grouped ? (rowH - 10) / 2 : barH / 2) + 4} className="chart-cat" textAnchor="end">
              {c.length > maxChars ? `${c.slice(0, maxChars - 1)}…` : c}
            </text>
            {pts.map((p) => {
              const si = grouped ? series.indexOf(p.series) : 0;
              const y = y0 + si * (barH + 2);
              return (
                <g key={`${p.series}-${p.y}`}>
                  <path d={barPath(sx(0), sx(p.y), y, barH)} fill={color(p.series)} />
                  {/* hit target larger than the mark */}
                  <rect x={left} y={y - 2} width={plotW} height={barH + 4} fill="transparent"
                    onMouseMove={(e) => {
                      const r = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                      setTip({ x: e.clientX - r.left, y: e.clientY - r.top,
                        lines: [c, ...(series.length > 1 ? [p.series] : []), `${yName || "value"}: ${fmt(p.y)}`, ...(p.n ? [`evidence [${p.n}]`] : [])] });
                    }} />
                </g>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}

function XYPlot({ chart, width, color, series, setTip, dark, yName }: PlotProps) {
  const pts = chart.points.filter((p) => typeof p.x === "number") as (ChartPoint & { x: number })[];
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const xt = niceTicks(Math.min(...xs), Math.max(...xs));
  const yt = niceTicks(Math.min(...ys), Math.max(...ys));
  const plotW = width - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const sx = (v: number) => PAD.left + ((v - xt[0]) / (xt[xt.length - 1] - xt[0])) * plotW;
  const sy = (v: number) => PAD.top + plotH - ((v - yt[0]) / (yt[yt.length - 1] - yt[0])) * plotH;
  const ring = dark ? "#1a1a1b" : "#ffffff";

  const nearest = (mx: number, my: number) => {
    let best: (typeof pts)[number] | null = null, bd = 18 * 18;  // hit radius larger than the dot
    for (const p of pts) {
      const d = (sx(p.x) - mx) ** 2 + (sy(p.y) - my) ** 2;
      if (d < bd) { bd = d; best = p; }
    }
    return best;
  };

  return (
    <svg width={width} height={H} role="img" aria-label={chart.title}
      onMouseMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        const mx = e.clientX - r.left, my = e.clientY - r.top;
        const p = nearest(mx, my);
        setTip(p ? { x: sx(p.x), y: sy(p.y), lines: [p.label || (series.length > 1 ? p.series : "point"),
          ...(p.label && series.length > 1 ? [p.series] : []),
          `${chart.x_label || "x"}: ${fmt(p.x)}`, `${yName || "y"}: ${fmt(p.y)}`, ...(p.n ? [`evidence [${p.n}]`] : [])] } : null);
      }}>
      {yt.map((t) => (
        <g key={`y${t}`}>
          <line x1={PAD.left} x2={width - PAD.right} y1={sy(t)} y2={sy(t)} className="chart-grid" />
          <text x={PAD.left - 8} y={sy(t) + 4} className="chart-tick" textAnchor="end">{fmt(t)}</text>
        </g>
      ))}
      {xt.map((t) => (
        <text key={`x${t}`} x={sx(t)} y={H - PAD.bottom + 16} className="chart-tick" textAnchor="middle">{fmt(t)}</text>
      ))}
      <line x1={PAD.left} x2={width - PAD.right} y1={PAD.top + plotH} y2={PAD.top + plotH} className="chart-baseline" />
      <text x={PAD.left + plotW / 2} y={H - 8} className="chart-axis-label" textAnchor="middle">{chart.x_label}</text>
      <text transform={`translate(14 ${PAD.top + plotH / 2}) rotate(-90)`} className="chart-axis-label" textAnchor="middle">{yName}</text>
      {chart.type === "line"
        ? series.map((s) => {
            const sp = pts.filter((p) => p.series === s).sort((a, b) => a.x - b.x);
            return (
              <polyline key={s} fill="none" stroke={color(s)} strokeWidth={2} strokeLinejoin="round"
                points={sp.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ")} />
            );
          })
        : null}
      {pts.map((p, i) => (
        <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r={4} fill={color(p.series)} stroke={ring} strokeWidth={2} />
      ))}
    </svg>
  );
}
