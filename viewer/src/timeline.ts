import { MiniLine } from "./charts";
import { esc } from "./markdown";
import type { MonthlyLayers } from "./overlays";
import type { MonthEntry, MonthlyManifest, MonthStats } from "./types";

const INSUFFICIENT_NOTE = "Cloudy: insufficient clear observations";
const PLAY = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5Z" fill="currentColor"/></svg>`;
const PAUSE = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><rect x="6" y="5" width="4.5" height="14" rx="1.2" fill="currentColor"/><rect x="13.5" y="5" width="4.5" height="14" rx="1.2" fill="currentColor"/></svg>`;
const STEP_MS = 1000;

const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, minimumFractionDigits: 1 });

function monthLabel(month: string, long = false): string {
  const [y, m] = month.split("-").map(Number);
  if (!y || !m) return month;
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en-US", { month: long ? "long" : "short", year: "numeric", timeZone: "UTC" });
}

function q<T extends HTMLElement>(root: ParentNode, sel: string): T {
  const el = root.querySelector<T>(sel);
  if (!el) throw new Error(`missing ${sel}`);
  return el;
}

const ha = (v: number | null) => (v === null ? "—" : `${nf1.format(v)} ha`);
const idx = (v: number | null) => (v === null ? "—" : v.toFixed(3));

/** Shown on the Timeline tab when monthly/manifest.json is not available. */
export function mountTimelineUnavailable(panel: HTMLElement): void {
  panel.innerHTML = `
    <header>
      <div class="eyebrow">Monthly timeline</div>
      <h1>Timeline 2023–2026</h1>
    </header>
    <div class="callout">
      <b>Monthly data not available yet.</b>
      <p>Expected <code>monthly/manifest.json</code> next to the assignment data. Once the monthly pipeline has run, this tab shows a
      month-by-month RGB frame, a change-intensity overlay and KPI charts for the mine footprint, water and changed area.</p>
    </div>`;
}

export interface TimelineOptions {
  manifest: MonthlyManifest;
  base: URL;
  layers: MonthlyLayers;
  stage: HTMLElement;
  panel: HTMLElement;
  dock: HTMLElement;
}

export class Timeline {
  private readonly months: MonthEntry[];
  private index = 0;
  private timer: number | undefined;
  private active = false;
  private preloaded = false;
  private readonly slider: HTMLInputElement;
  private readonly playBtn: HTMLButtonElement;
  private readonly dockLabel: HTMLElement;
  private readonly badge: HTMLElement;
  private readonly card: HTMLElement;
  private readonly kv: HTMLElement;
  private readonly charts: MiniLine[] = [];
  private readonly rgbOn: HTMLInputElement;
  private readonly rgbOpacity: HTMLInputElement;
  private readonly chgOn: HTMLInputElement;
  private readonly chgOpacity: HTMLInputElement;

  constructor(private readonly o: TimelineOptions) {
    this.months = o.manifest.months;
    const first = this.months[0]!.month;
    const last = this.months[this.months.length - 1]!.month;
    const okCount = this.months.filter((m) => m.status === "ok").length;

    o.panel.innerHTML = `
      <header>
        <div class="eyebrow">Monthly timeline</div>
        <h1>Mine footprint, ${esc(first.slice(0, 4) === last.slice(0, 4) ? first.slice(0, 4) : `${first.slice(0, 4)}\u2013${last.slice(0, 4)}`)}</h1>
        <p class="dates">${esc(monthLabel(first))} &rarr; ${esc(monthLabel(last))} &middot; ${okCount} of ${this.months.length} months usable</p>
      </header>
      <section>
        <h2>Selected month</h2>
        <div id="tl-card"></div>
      </section>
      <section>
        <h2>Overlays</h2>
        <div class="layer">
          <label class="check"><input type="checkbox" id="tl-rgb-on" checked><span>Monthly RGB composite</span></label>
          <input type="range" id="tl-rgb-op" min="0" max="1" step="0.05" value="1" aria-label="RGB opacity">
        </div>
        <div class="layer">
          <label class="check"><input type="checkbox" id="tl-chg-on" checked><span>Change intensity vs previous month</span></label>
          <input type="range" id="tl-chg-op" min="0" max="1" step="0.05" value="0.8" aria-label="Change intensity opacity">
        </div>
        <div class="ramp"><span>low</span><i></i><span>high change</span></div>
      </section>
      <section>
        <h2>Month statistics</h2>
        <dl class="kv" id="tl-kv"></dl>
      </section>
      <section>
        <h2>How to read</h2>
        <p class="note">Each month is a cloud-filtered Sentinel-2 composite. Months without enough clear observations are greyed out on the
        slider, skipped during playback and left as gaps in the charts. Click a chart point to jump to that month.</p>
      </section>
      <footer>Basemap: Esri World Imagery. Contains modified Copernicus Sentinel-2 data.</footer>`;

    o.dock.innerHTML = `
      <div class="dock-row">
        <button type="button" class="play" id="tl-play" aria-label="Play timeline">${PLAY}</button>
        <div class="slider-wrap">
          <input type="range" id="tl-slider" min="0" max="${this.months.length - 1}" step="1" value="0" aria-label="Month">
          <div class="ticks" aria-hidden="true">${this.months
            .map((m) => `<i class="${m.status === "ok" ? "ok" : "bad"}" title="${esc(m.month)}${m.status === "ok" ? "" : ` &middot; ${INSUFFICIENT_NOTE.toLowerCase()}`}"></i>`)
            .join("")}</div>
        </div>
        <div class="dock-month" id="tl-label"></div>
      </div>
      <div class="dock-charts" id="tl-charts"></div>`;

    this.badge = document.createElement("div");
    this.badge.className = "month-badge";
    o.stage.append(this.badge);

    this.slider = q(o.dock, "#tl-slider");
    this.playBtn = q(o.dock, "#tl-play");
    this.dockLabel = q(o.dock, "#tl-label");
    this.card = q(o.panel, "#tl-card");
    this.kv = q(o.panel, "#tl-kv");
    this.rgbOn = q(o.panel, "#tl-rgb-on");
    this.rgbOpacity = q(o.panel, "#tl-rgb-op");
    this.chgOn = q(o.panel, "#tl-chg-on");
    this.chgOpacity = q(o.panel, "#tl-chg-op");

    const months = this.months.map((m) => m.month);
    const spec: [string, keyof MonthStats, string][] = [
      ["Mine footprint", "footprint_ha", "#a78bfa"],
      ["Water", "water_ha", "#38bdf8"],
      ["Changed area", "changed_ha", "#f59e0b"],
    ];
    const chartsRoot = q(o.dock, "#tl-charts");
    for (const [title, key, color] of spec) {
      this.charts.push(
        new MiniLine(chartsRoot, {
          title,
          unit: "ha",
          color,
          x: months,
          y: this.months.map((m) => (m.status === "ok" ? m.stats[key] : null)),
          xLabel: (x, i) => (x.endsWith("-01") ? x.slice(0, 4) : i === 0 ? x : null),
          onPick: (i) => this.setIndex(i, true),
        }),
      );
    }

    this.slider.addEventListener("input", () => {
      this.pause();
      this.setIndex(Number(this.slider.value));
    });
    this.playBtn.addEventListener("click", () => (this.timer === undefined ? this.play() : this.pause()));
    const overlays = () => o.layers.setState({
      rgb: { visible: this.rgbOn.checked, opacity: Number(this.rgbOpacity.value) },
      change: { visible: this.chgOn.checked, opacity: Number(this.chgOpacity.value) },
    });
    for (const el of [this.rgbOn, this.rgbOpacity, this.chgOn, this.chgOpacity]) el.addEventListener("input", overlays);
    overlays();

    // Start on the latest usable month: that is the most informative first frame.
    const lastOk = this.months.map((m) => m.status).lastIndexOf("ok");
    this.setIndex(lastOk >= 0 ? lastOk : this.months.length - 1);
  }

  get currentMonth(): string {
    return this.months[this.index]!.month;
  }

  hasMonth(month: string): boolean {
    return this.months.some((m) => m.month === month);
  }

  setMonth(month: string): boolean {
    const i = this.months.findIndex((m) => m.month === month);
    if (i < 0) return false;
    this.pause();
    this.setIndex(i);
    return true;
  }

  setActive(active: boolean): void {
    this.active = active;
    this.o.layers.setActive(active);
    this.badge.hidden = !active;
    if (active) {
      this.preload();
    } else {
      this.pause();
    }
  }

  private preload(): void {
    if (this.preloaded) return;
    this.preloaded = true;
    for (const m of this.months) for (const p of [m.rgb, m.change]) if (p) new Image().src = new URL(p, this.o.base).href;
  }

  private url(p: string | null): string | null {
    return p ? new URL(p, this.o.base).href : null;
  }

  private setIndex(i: number, pause = false): void {
    if (pause) this.pause();
    this.index = Math.min(this.months.length - 1, Math.max(0, i));
    const m = this.months[this.index]!;
    this.slider.value = String(this.index);
    this.dockLabel.textContent = monthLabel(m.month);
    this.o.layers.setFrames(m.status === "ok" ? this.url(m.rgb) : null, m.status === "ok" ? this.url(m.change) : null);
    this.renderCard(m);
    this.renderStats(m);
    for (const c of this.charts) c.setCursor(this.index);
  }

  private renderCard(m: MonthEntry): void {
    const ok = m.status === "ok";
    const chg = ok && m.change && m.prev_month ? `Change vs ${esc(monthLabel(m.prev_month))}` : ok ? "First usable month: no change layer" : "";
    this.card.innerHTML = `
      <div class="month-big">${esc(monthLabel(m.month, true))}</div>
      <div class="month-meta">${m.n_items} scene${m.n_items === 1 ? "" : "s"} &middot; ${nf0.format(m.clear_fraction * 100)}% clear${chg ? ` &middot; ${chg}` : ""}</div>
      ${ok ? "" : `<div class="callout warn"><b>${INSUFFICIENT_NOTE}</b> for this month, so no frame is shown and its statistics are left empty.</div>`}`;
    this.badge.className = `month-badge${ok ? "" : " bad"}`;
    this.badge.innerHTML = `<b>${esc(monthLabel(m.month))}</b>${ok ? "" : ` <span>${INSUFFICIENT_NOTE.toLowerCase()}</span>`}`;
    this.badge.hidden = !this.active;
  }

  private renderStats(m: MonthEntry): void {
    const s = m.stats;
    const rows: [string, string][] = [
      ["Mine footprint", ha(s.footprint_ha)],
      ["Water", ha(s.water_ha)],
      ["Bare ground", ha(s.bare_ha)],
      ["Changed", ha(s.changed_ha)],
      ["Changed in footprint", ha(s.changed_ha_in_footprint)],
      ["NDVI mean", idx(s.ndvi_mean)],
      ["NBR mean", idx(s.nbr_mean)],
      ["MNDWI mean", idx(s.mndwi_mean)],
      ["BSI mean", idx(s.bsi_mean)],
    ];
    this.kv.innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd class="${v === "—" ? "none" : ""}">${esc(v)}</dd>`).join("");
  }

  private nextPlayable(from: number): number {
    const n = this.months.length;
    for (let step = 1; step <= n; step++) {
      const i = (from + step) % n;
      if (this.months[i]!.status === "ok") return i;
    }
    return from;
  }

  private play(): void {
    if (!this.months.some((m) => m.status === "ok")) return;
    this.playBtn.innerHTML = PAUSE;
    this.playBtn.setAttribute("aria-label", "Pause timeline");
    const tick = () => {
      this.setIndex(this.nextPlayable(this.index));
      this.timer = window.setTimeout(tick, STEP_MS);
    };
    this.timer = window.setTimeout(tick, 250);
    if (this.months[this.index]!.status !== "ok") this.setIndex(this.nextPlayable(this.index));
  }

  private pause(): void {
    if (this.timer !== undefined) window.clearTimeout(this.timer);
    this.timer = undefined;
    this.playBtn.innerHTML = PLAY;
    this.playBtn.setAttribute("aria-label", "Play timeline");
  }
}
