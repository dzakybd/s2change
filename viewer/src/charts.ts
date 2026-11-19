import { esc } from "./markdown";
import type { AgentChart } from "./types";

/** Tiny hand-rolled SVG charts (no chart library): multi-series line with gaps, grouped bars, and a clickable mini line. */

export const SERIES_COLORS = ["#38bdf8", "#f59e0b", "#a855f7", "#34d399", "#f472b6", "#94a3b8"];

const GRID = "#22304a";
const AXIS_TEXT = "#94a3c0";

type Margin = { l: number; r: number; t: number; b: number };

function niceStep(raw: number): number {
  const exp = Math.floor(Math.log10(raw));
  const f = raw / 10 ** exp;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * 10 ** exp;
}

/** Round-number ticks covering [min, max]. */
export function niceTicks(min: number, max: number, count = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1;
    min -= pad;
    max += pad;
  }
  const step = niceStep((max - min) / count);
  const ticks: number[] = [];
  for (let v = Math.floor(min / step) * step; v < max + step * 0.999; v += step) ticks.push(Number(v.toPrecision(12)));
  return ticks;
}

export function fmtNum(v: number, step = 0): string {
  if (!Number.isFinite(v)) return "";
  const digits = step > 0 && step < 1 ? Math.min(4, Math.ceil(-Math.log10(step))) : Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2;
  return v.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: step > 0 && step < 1 ? digits : 0 });
}

function stepOf(ticks: number[]): number {
  return ticks.length > 1 ? (ticks[1] ?? 0) - (ticks[0] ?? 0) : 1;
}

function finite(values: (number | null)[]): number[] {
  return values.filter((v): v is number => typeof v === "number" && Number.isFinite(v));
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function yGrid(ticks: number[], m: Margin, w: number, y: (v: number) => number): string {
  const step = stepOf(ticks);
  return ticks
    .map((t) => {
      const py = y(t).toFixed(1);
      return `<line x1="${m.l}" x2="${w - m.r}" y1="${py}" y2="${py}" stroke="${GRID}" stroke-width="1"/><text x="${m.l - 6}" y="${py}" dy="3.5" text-anchor="end" fill="${AXIS_TEXT}" font-size="10">${esc(fmtNum(t, step))}</text>`;
    })
    .join("");
}

/** Which category indexes get an x label so labels do not collide. */
function labelIndexes(n: number, plotW: number, minGap = 48): Set<number> {
  const every = Math.max(1, Math.ceil((n * minGap) / Math.max(plotW, 1)));
  const set = new Set<number>();
  for (let i = 0; i < n; i += every) set.add(i);
  return set;
}

function legend(names: string[]): string {
  if (names.length < 2) return "";
  return `<div class="chart-legend">${names.map((n, i) => `<span><i style="background:${SERIES_COLORS[i % SERIES_COLORS.length]}"></i>${esc(n)}</span>`).join("")}</div>`;
}

function barSvg(spec: AgentChart, w: number, h: number): string {
  const m: Margin = { l: 46, r: 8, t: 14, b: 26 };
  const vals = finite(spec.series.flatMap((s) => s.y));
  const ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals));
  const lo = ticks[0] ?? 0;
  const hi = ticks[ticks.length - 1] ?? 1;
  const plotW = w - m.l - m.r;
  const y = (v: number) => h - m.b - ((v - lo) / (hi - lo || 1)) * (h - m.t - m.b);
  const n = spec.x.length;
  const group = plotW / Math.max(n, 1);
  const k = Math.max(spec.series.length, 1);
  const barW = Math.min(36, (group * 0.72) / k);
  const showValues = n * k <= 8;
  const labels = labelIndexes(n, plotW, 56);
  let body = yGrid(ticks, m, w, y);
  const zero = y(0).toFixed(1);
  spec.series.forEach((s, si) => {
    const color = SERIES_COLORS[si % SERIES_COLORS.length];
    s.y.forEach((v, i) => {
      if (v === null || !Number.isFinite(v)) return;
      const x0 = m.l + group * i + group / 2 - (barW * k) / 2 + barW * si;
      const top = y(v);
      const yy = Math.min(top, y(0));
      const hh = Math.max(Math.abs(y(0) - top), 1);
      body += `<rect x="${x0.toFixed(1)}" y="${yy.toFixed(1)}" width="${(barW - 2).toFixed(1)}" height="${hh.toFixed(1)}" rx="2" fill="${color}"><title>${esc(`${s.name}, ${spec.x[i] ?? ""}: ${fmtNum(v)}`)}</title></rect>`;
      if (showValues) body += `<text x="${(x0 + (barW - 2) / 2).toFixed(1)}" y="${(yy - 4).toFixed(1)}" text-anchor="middle" fill="#c5cee0" font-size="10">${esc(fmtNum(v))}</text>`;
    });
  });
  body += `<line x1="${m.l}" x2="${w - m.r}" y1="${zero}" y2="${zero}" stroke="#3a4a68"/>`;
  spec.x.forEach((label, i) => {
    if (!labels.has(i)) return;
    body += `<text x="${(m.l + group * i + group / 2).toFixed(1)}" y="${h - 9}" text-anchor="middle" fill="${AXIS_TEXT}" font-size="10"><title>${esc(String(label))}</title>${esc(truncate(String(label), Math.max(6, Math.floor(group / 6))))}</text>`;
  });
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(spec.title ?? "bar chart")}">${body}</svg>`;
}

function lineSvg(spec: AgentChart, w: number, h: number): string {
  const m: Margin = { l: 46, r: 12, t: 10, b: 26 };
  const vals = finite(spec.series.flatMap((s) => s.y));
  const ticks = niceTicks(Math.min(...vals), Math.max(...vals));
  const lo = ticks[0] ?? 0;
  const hi = ticks[ticks.length - 1] ?? 1;
  const n = spec.x.length;
  const plotW = w - m.l - m.r;
  const x = (i: number) => (n <= 1 ? m.l + plotW / 2 : m.l + (plotW * i) / (n - 1));
  const y = (v: number) => h - m.b - ((v - lo) / (hi - lo || 1)) * (h - m.t - m.b);
  const labels = labelIndexes(n, plotW, 52);
  let body = yGrid(ticks, m, w, y);
  spec.x.forEach((label, i) => {
    if (labels.has(i)) body += `<text x="${x(i).toFixed(1)}" y="${h - 9}" text-anchor="middle" fill="${AXIS_TEXT}" font-size="10">${esc(truncate(String(label), 8))}</text>`;
  });
  spec.series.forEach((s, si) => {
    const color = SERIES_COLORS[si % SERIES_COLORS.length];
    let d = "";
    let pen = false;
    s.y.forEach((v, i) => {
      if (v === null || !Number.isFinite(v)) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`;
      pen = true;
    });
    body += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    s.y.forEach((v, i) => {
      if (v === null || !Number.isFinite(v)) return;
      body += `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="3" fill="${color}" stroke="#111a2c" stroke-width="1.5"><title>${esc(`${s.name}, ${spec.x[i] ?? ""}: ${fmtNum(v)}`)}</title></circle>`;
    });
  });
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(spec.title ?? "line chart")}">${body}</svg>`;
}

/** Inline chart for chat answers. Scales to the width of its container. */
export function chartElement(spec: AgentChart): HTMLElement {
  const root = document.createElement("figure");
  root.className = "chart";
  const hasData = spec.x.length > 0 && finite(spec.series.flatMap((s) => s.y)).length > 0;
  const svg = !hasData ? `<p class="note">No data to plot.</p>` : spec.type === "bar" ? barSvg(spec, 380, 190) : lineSvg(spec, 380, 190);
  root.innerHTML = `${spec.title ? `<figcaption>${esc(spec.title)}</figcaption>` : ""}${svg}${legend(spec.series.map((s) => s.name))}`;
  return root;
}

export interface MiniLineOptions {
  title: string;
  unit: string;
  color: string;
  /** Category labels (months); one per y value. */
  x: string[];
  /** Null values render as gaps. */
  y: (number | null)[];
  /** Label for an x tick, or null to skip it. */
  xLabel: (x: string, i: number) => string | null;
  onPick: (index: number) => void;
}

/** Small line chart with a cursor synced from outside and click-to-pick on the nearest category. */
export class MiniLine {
  private readonly plot: HTMLElement;
  private readonly valueEl: HTMLElement;
  private cursor = -1;
  private hover = -1;
  private width = 0;
  private readonly H = 96;
  private readonly m: Margin = { l: 40, r: 8, t: 8, b: 20 };

  constructor(parent: HTMLElement, private readonly o: MiniLineOptions) {
    const root = document.createElement("div");
    root.className = "mini";
    root.innerHTML = `<div class="mini-head"><span class="mini-title"><i style="background:${o.color}"></i>${esc(o.title)}</span><b class="mini-val"></b></div><div class="mini-plot"></div>`;
    parent.append(root);
    this.plot = root.querySelector<HTMLElement>(".mini-plot")!;
    this.valueEl = root.querySelector<HTMLElement>(".mini-val")!;
    this.plot.addEventListener("pointermove", (e) => this.setHover(this.indexAt(e)));
    this.plot.addEventListener("pointerleave", () => this.setHover(-1));
    this.plot.addEventListener("click", (e) => o.onPick(this.indexAt(e)));
    new ResizeObserver(() => this.render()).observe(this.plot);
  }

  private x(i: number): number {
    const n = this.o.x.length;
    const plotW = this.width - this.m.l - this.m.r;
    return n <= 1 ? this.m.l + plotW / 2 : this.m.l + (plotW * i) / (n - 1);
  }

  private indexAt(e: MouseEvent): number {
    const r = this.plot.getBoundingClientRect();
    const n = this.o.x.length;
    const t = (e.clientX - r.left - this.m.l) / Math.max(this.width - this.m.l - this.m.r, 1);
    return Math.min(n - 1, Math.max(0, Math.round(t * (n - 1))));
  }

  setCursor(i: number): void {
    this.cursor = i;
    const v = this.o.y[i];
    this.valueEl.textContent = typeof v === "number" ? `${fmtNum(v)} ${this.o.unit}` : "no data";
    this.valueEl.classList.toggle("none", typeof v !== "number");
    this.render();
  }

  private setHover(i: number): void {
    if (i === this.hover) return;
    this.hover = i;
    this.render();
  }

  private render(): void {
    const w = this.plot.clientWidth;
    if (w < 40) return;
    this.width = w;
    const { m, H: h } = this;
    const { x: cats, y: ys, color } = this.o;
    const vals = finite(ys);
    const ticks = niceTicks(Math.min(...vals), Math.max(...vals), 3);
    const lo = ticks[0] ?? 0;
    const hi = ticks[ticks.length - 1] ?? 1;
    const y = (v: number) => h - m.b - ((v - lo) / (hi - lo || 1)) * (h - m.t - m.b);
    const n = cats.length;
    const band = Math.max((w - m.l - m.r) / Math.max(n - 1, 1), 2);
    let body = yGrid(ticks, m, w, y);
    // Insufficient months: faint band so a gap reads as "no data", not "zero".
    ys.forEach((v, i) => {
      if (typeof v !== "number") body += `<rect x="${(this.x(i) - band / 2).toFixed(1)}" y="${m.t}" width="${band.toFixed(1)}" height="${h - m.t - m.b}" fill="#94a3c0" opacity="0.1"/>`;
    });
    cats.forEach((c, i) => {
      const label = this.o.xLabel(c, i);
      if (label) body += `<text x="${this.x(i).toFixed(1)}" y="${h - 6}" text-anchor="${i === 0 ? "start" : "middle"}" fill="${AXIS_TEXT}" font-size="10">${esc(label)}</text>`;
    });
    let d = "";
    let pen = false;
    ys.forEach((v, i) => {
      if (typeof v !== "number") {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${this.x(i).toFixed(1)} ${y(v).toFixed(1)}`;
      pen = true;
    });
    body += `<path d="${d}" fill="none" stroke="${color}" stroke-width="1.75" stroke-linejoin="round" stroke-linecap="round"/>`;
    ys.forEach((v, i) => {
      if (typeof v === "number") body += `<circle cx="${this.x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="2.6" fill="${color}"><title>${esc(`${cats[i] ?? ""}: ${fmtNum(v)} ${this.o.unit}`)}</title></circle>`;
    });
    if (this.hover >= 0 && this.hover !== this.cursor) body += `<line x1="${this.x(this.hover).toFixed(1)}" x2="${this.x(this.hover).toFixed(1)}" y1="${m.t}" y2="${h - m.b}" stroke="#94a3c0" stroke-width="1" stroke-dasharray="2 3"/>`;
    if (this.cursor >= 0) {
      const cx = this.x(this.cursor).toFixed(1);
      body += `<line x1="${cx}" x2="${cx}" y1="${m.t}" y2="${h - m.b}" stroke="#e6ebf5" stroke-width="1.25"/>`;
      const cv = ys[this.cursor];
      if (typeof cv === "number") body += `<circle cx="${cx}" cy="${y(cv).toFixed(1)}" r="4.5" fill="${color}" stroke="#e6ebf5" stroke-width="1.5"/>`;
    }
    this.plot.innerHTML = `<svg width="${w}" height="${h}" role="img" aria-label="${esc(this.o.title)} by month">${body}</svg>`;
  }
}
