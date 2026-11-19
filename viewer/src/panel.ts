import type { Data } from "./data";
import type { Stats } from "./filters";
import type { Direction, Filters, LayerState, RasterKey } from "./types";
import { DIRECTION_COLORS, DIRECTIONS, RASTER_KEYS } from "./types";

export interface PanelHandlers {
  onRaster: (key: RasterKey, state: LayerState) => void;
  onSwipe: (on: boolean) => void;
  onChangesVisible: (visible: boolean) => void;
  onFilters: (filters: Filters) => void;
}

export interface PanelInitial {
  rasters: Record<RasterKey, LayerState>;
  swipe: boolean;
}

export interface Panel {
  filters: () => Filters;
  updateStats: (stats: Stats) => void;
}

const METHOD_LABELS: Record<string, string> = {
  robust_cva: "Robust CVA (shown)",
  euclidean_baseline: "Euclidean baseline",
  irmad: "Zone-wise IR-MAD",
};

const RGB_KEYS: readonly RasterKey[] = ["before", "after", "after_normalized"];
const fmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const ha = (v: number) => (v < 10 ? v.toFixed(2) : v < 100 ? v.toFixed(1) : fmt.format(v));

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

function q<T extends HTMLElement>(root: ParentNode, sel: string): T {
  const el = root.querySelector<T>(sel);
  if (!el) throw new Error(`missing ${sel}`);
  return el;
}

export function buildPanel(root: HTMLElement, data: Data, initial: PanelInitial, h: PanelHandlers): Panel {
  const { manifest, changes } = data;
  const maxArea = Math.max(...changes.features.map((f) => f.properties.area_m2 / 10_000));
  const maxConf = Math.ceil(Math.max(...changes.features.map((f) => f.properties.confidence)) * 100) / 100;
  const summary = Object.entries(manifest.summary);
  const maxMethod = Math.max(...summary.map(([, v]) => v));
  const total = changes.features.length;

  const rasterRows = RASTER_KEYS.map((k) => {
    const s = initial.rasters[k];
    return `<div class="layer" data-key="${k}">
      <label class="check"><input type="checkbox" data-role="vis" ${s.visible ? "checked" : ""}><span>${esc(manifest.rasters[k].label)}</span></label>
      <input type="range" data-role="opacity" min="0" max="1" step="0.05" value="${s.opacity}" aria-label="Opacity">
    </div>`;
  }).join("");

  const dirRows = DIRECTIONS.map(
    (d) => `<label class="check dir"><input type="checkbox" data-dir="${d}" checked>
      <i class="swatch" style="background:${DIRECTION_COLORS[d]}"></i><span>${d}</span><b data-count="${d}">0</b></label>`,
  ).join("");

  const methodRows = summary
    .map(([k, v]) => {
      const main = k === "robust_cva";
      return `<div class="method${main ? " main" : ""}">
        <div class="method-head"><span>${esc(METHOD_LABELS[k] ?? k)}</span><b>${fmt.format(v)} ha</b></div>
        <div class="bar"><i style="width:${((v / maxMethod) * 100).toFixed(1)}%"></i></div></div>`;
    })
    .join("");

  root.innerHTML = `
    <header>
      <div class="eyebrow">Change detection viewer</div>
      <h1>Lumwana-area open-pit mine, Zambia</h1>
      <p class="dates">Sentinel-2 change <time>${esc(manifest.date_before)}</time> &rarr; <time>${esc(manifest.date_after)}</time></p>
    </header>

    <section>
      <h2>Method</h2>
      <p>Each provided "date" is a mosaic of two Sentinel-2 scenes from different orbits (verified pixel-exact via STAC),
      so radiometry steps at the seams. The later image is normalized to the earlier one <em>per zone</em> using stable
      pixels only. Change vector analysis then scores each pixel's spectral change against a per-zone robust noise model,
      and the confident pixels are vectorized into polygons.</p>
    </section>

    <section>
      <h2>Changed area by method</h2>
      ${methodRows}
      <p class="note">The baseline reads mosaic seams and haze as change; IR-MAD over-detects with only three visible bands.</p>
    </section>

    <section>
      <h2>Layers</h2>
      <label class="check swipe-toggle"><input type="checkbox" id="swipe-toggle" ${initial.swipe ? "checked" : ""}><span>Swipe compare: before | after (normalized)</span></label>
      <p class="note" id="swipe-note" hidden>Swipe pins the RGB layers; their toggles are disabled while it is on.</p>
      ${rasterRows}
      <label class="check"><input type="checkbox" id="changes-toggle" checked><span>Change polygons</span></label>
    </section>

    <section>
      <h2>Filter polygons</h2>
      <div class="live"><b id="live-count">0</b> of ${total} polygons <span class="sep">&middot;</span> <b id="live-area">0</b> ha</div>
      <div class="slider"><label for="min-conf">Min confidence <output id="min-conf-out"></output></label>
        <input id="min-conf" type="range" min="0" max="${maxConf}" step="0.01" value="0"></div>
      <div class="slider"><label for="min-area">Min area <output id="min-area-out"></output></label>
        <input id="min-area" type="range" min="0" max="1" step="0.002" value="0"></div>
      ${dirRows}
    </section>

    <section>
      <h2>Legend</h2>
      <ul class="legend">
        ${DIRECTIONS.map((d) => `<li><i class="swatch" style="background:${DIRECTION_COLORS[d]}"></i>${d}</li>`).join("")}
      </ul>
      <div class="ramp"><span>low</span><i></i><span>high change intensity</span></div>
      <p class="note">Hover a polygon to highlight it, click for details.</p>
    </section>

    <footer>
      Basemap: Esri World Imagery. Contains modified Copernicus Sentinel-2 data (2023).
    </footer>`;

  // Layers
  for (const row of root.querySelectorAll<HTMLElement>(".layer")) {
    const key = row.dataset["key"] as RasterKey;
    const vis = q<HTMLInputElement>(row, '[data-role="vis"]');
    const op = q<HTMLInputElement>(row, '[data-role="opacity"]');
    const push = () => h.onRaster(key, { visible: vis.checked, opacity: Number(op.value) });
    vis.addEventListener("change", push);
    op.addEventListener("input", push);
  }
  const swipeToggle = q<HTMLInputElement>(root, "#swipe-toggle");
  const swipeNote = q(root, "#swipe-note");
  const syncSwipeUi = () => {
    for (const k of RGB_KEYS) {
      for (const el of root.querySelectorAll<HTMLInputElement>(`.layer[data-key="${k}"] input`)) el.disabled = swipeToggle.checked;
    }
    swipeNote.hidden = !swipeToggle.checked;
  };
  swipeToggle.addEventListener("change", () => {
    syncSwipeUi();
    h.onSwipe(swipeToggle.checked);
  });
  syncSwipeUi();
  q<HTMLInputElement>(root, "#changes-toggle").addEventListener("change", (e) => h.onChangesVisible((e.target as HTMLInputElement).checked));

  // Filters
  const conf = q<HTMLInputElement>(root, "#min-conf");
  const area = q<HTMLInputElement>(root, "#min-area");
  const dirs = [...root.querySelectorAll<HTMLInputElement>("[data-dir]")];
  const readFilters = (): Filters => ({
    minConfidence: Number(conf.value),
    // Quadratic slider: fine control over the many small polygons, reach for the few large ones.
    minAreaHa: Number(area.value) ** 2 * maxArea,
    directions: new Set(dirs.filter((d) => d.checked).map((d) => d.dataset["dir"] as Direction)),
  });
  const refreshOutputs = () => {
    const f = readFilters();
    q(root, "#min-conf-out").textContent = f.minConfidence.toFixed(2);
    q(root, "#min-area-out").textContent = `${ha(f.minAreaHa)} ha`;
    return f;
  };
  for (const el of [conf, area, ...dirs]) el.addEventListener("input", () => h.onFilters(refreshOutputs()));
  refreshOutputs();

  return {
    filters: readFilters,
    updateStats(stats) {
      q(root, "#live-count").textContent = fmt.format(stats.count);
      q(root, "#live-area").textContent = ha(stats.areaHa);
      for (const d of DIRECTIONS) q(root, `[data-count="${d}"]`).textContent = String(stats.byDirection[d]);
    },
  };
}
