import "../styles/overview.css";
import type { AppContext, View } from "../app";
import { logoSvg } from "../brand";
import { dataUrl, loadChanges, loadManifest, loadOverview } from "../data";
import { BLOB_URL, CHANGE_TYPES, EXTERNAL, LIVE_URL, REPO_URL, esc, fmtHa, fmtInt, fmtPct, q, typeInfo } from "../domain";
import { hrefFor } from "../router";
import type { Overview } from "../types";

const ext = (href: string, label: string, cls = "btn") => `<a class="${cls}" href="${href}" target="_blank" rel="noopener noreferrer">${label}${EXTERNAL}</a>`;

function days(a: string, b: string): number {
  return Math.round((Date.parse(b) - Date.parse(a)) / 86_400_000);
}

/** Gap between source scenes, from the acquisitions in run_summary.json: the shortest and longest before/after pair. */
function sceneGap(o: Overview): { min: number; max: number } {
  const acq = o.pair.acquisitions;
  const before = acq[o.pair.before]?.source_dates ?? [];
  const after = acq[o.pair.after]?.source_dates ?? [];
  const gaps = before.flatMap((b) => after.map((a) => days(b, a)));
  return { min: Math.min(...gaps), max: Math.max(...gaps) };
}

function findings(o: Overview): string {
  const h = o.headline;
  const sens = o.sensitivity.map((s) => s.changed_ha);
  const lo = Math.min(...sens);
  const hi = Math.max(...sens);
  const top = o.facilities[0];
  const real = o.pair.compared_gap_days;
  const gap = real && real.length ? { min: Math.min(...real), max: Math.max(...real) } : sceneGap(o);
  const acq = o.pair.acquisitions;
  const dates = (k: string) => (acq[k]?.source_dates ?? []).map((d) => d.slice(5)).join(" and ");
  const nominalGap = days(o.pair.before, o.pair.after);
  const ratio = h.baseline_ha / h.changed_ha;
  const cards = [
    { n: `${fmtInt(h.changed_ha)} ha`, t: "changed between the two dates", d: `${h.changed_pct_of_aoi}% of the ${fmtInt(h.aoi_ha)} ha area, in ${fmtInt(h.n_polygons)} polygons of at least 0.2 ha. Across 16 threshold settings the total stays between ${fmtInt(lo)} and ${fmtInt(hi)} ha.`, href: hrefFor("map"), cta: "Open the map" },
    { n: fmtPct(h.share_in_mine_footprint), t: "of the change is inside the mine footprint", d: top ? `${top.name} alone changed ${fmtInt(top.changed_ha)} ha (${fmtPct(top.changed_share)} of it), mostly ${typeInfo(Object.entries(top.change_types).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "").label.toLowerCase()}.` : "", href: hrefFor("map", { layer: "facilities" }), cta: "See the facility register" },
    { n: `${ratio.toFixed(0)}×`, t: "fewer hectares than the provided baseline", d: `A plain Euclidean difference flags ${fmtInt(h.baseline_ha)} ha, mostly mosaic seams and haze. The two maps overlap by only ${fmtPct(h.agreement_iou_with_baseline)} (IoU).`, href: hrefFor("report"), cta: "Read why" },
    { n: "2 + 2", t: "scenes behind the two “dates”", d: `Each date is a mosaic: ${dates(o.pair.before)} for the first, ${dates(o.pair.after)} for the second. The source scenes are ${gap.min} to ${gap.max} days apart, not the ${nominalGap} days the nominal dates suggest.`, href: hrefFor("report"), cta: "Data finding" },
  ];
  return cards
    .map(
      (c, i) => `<article class="finding card" style="--i:${i}"><div class="f-n num">${esc(c.n)}</div><h3>${esc(c.t)}</h3><p>${esc(c.d)}</p><a class="f-link" href="${c.href}">${esc(c.cta)} <span aria-hidden="true">→</span></a></article>`,
    )
    .join("");
}

function typesBar(o: Overview): string {
  const total = Object.values(o.change_type_ha).reduce((a, b) => a + b, 0);
  const rows = CHANGE_TYPES.map((t) => ({ t, v: o.change_type_ha[t.key] ?? 0 })).filter((r) => r.v > 0);
  return `<div class="tbar" role="img" aria-label="Changed hectares by type">${rows.map((r) => `<i style="flex:${r.v};background:${r.t.color}" title="${esc(r.t.label)}: ${fmtHa(r.v)} ha"></i>`).join("")}</div>
  <ul class="tleg">${rows.map((r) => `<li><i class="swatch" style="background:${r.t.color}"></i><span>${esc(r.t.label)}</span><b class="num">${fmtHa(r.v)} ha</b><em class="num">${fmtPct(r.v / total)}</em></li>`).join("")}</ul>`;
}

const BRIEF = [
  { n: 1, t: "Work with real satellite data", d: "Every provided pixel matched to a Planetary Computer scene; the mosaic seams found and explained.", links: [["Provenance in the report", hrefFor("report")], ["Stacks to download", hrefFor("downloads")]] },
  { n: 2, t: "Design the pipeline", d: "Seven stages from provenance to PostGIS; thresholds are false-alarm rates, not tuned constants.", links: [["Method diagram", "#method"], ["Report, method", hrefFor("report")]] },
  { n: 3, t: "Implement change detection", d: "Robust CVA against a per-zone noise model, hysteresis and a 0.2 ha minimum mapping unit.", links: [["Change intensity", hrefFor("map", { layer: "intensity" })], ["Change binary", hrefFor("map", { layer: "binary" })]] },
  { n: 4, t: "Store and query geospatial analytics", d: "A PostGIS database with spatial indexes, a read-only SQL console and an HTTP API.", links: [["SQL console", hrefFor("database")], ["API reference", `${BLOB_URL}docs/api.md`]] },
  { n: 5, t: "Visualize and interpret", d: "Typed change polygons with a stated reason each, a facility register, an oblique view and an analyst agent.", links: [["Change types", hrefFor("map")], ["Facilities", hrefFor("map", { layer: "facilities" })], ["Analyst", hrefFor("analyst")]] },
];

function brief(): string {
  return `<ol class="brief">${BRIEF.map(
    (b) => `<li class="card"><span class="b-n num">${b.n}</span><div><h3>${esc(b.t)}</h3><p>${esc(b.d)}</p><div class="b-links">${b.links.map(([l, h]) => (h?.startsWith("http") ? ext(h, esc(l ?? ""), "chip") : `<a class="chip" href="${h}">${esc(l ?? "")}</a>`)).join("")}</div></div></li>`,
  ).join("")}</ol>`;
}

const STEPS: [string, string, string][] = [
  ["Provenance", "Match pixels to", "STAC scenes"],
  ["Zones", "Seams become", "radiometric zones"],
  ["Normalise", "Stable pixels,", "fit per zone"],
  ["Robust CVA", "Mahalanobis d²,", "χ² noise model"],
  ["Hysteresis + MMU", "False-alarm rates,", "≥ 0.2 ha"],
  ["Polygons + types", "Rules label and", "explain each one"],
  ["PostGIS", "SQL, API and", "analyst agent"],
];

function methodSvg(vertical: boolean): string {
  const w = vertical ? 300 : 132;
  const h = vertical ? 58 : 96;
  const gap = vertical ? 22 : 26;
  const W = vertical ? w : STEPS.length * w + (STEPS.length - 1) * gap;
  const H = vertical ? STEPS.length * h + (STEPS.length - 1) * gap : h;
  const nodes = STEPS.map(([title, l1, l2], i) => {
    const x = vertical ? 0 : i * (w + gap);
    const y = vertical ? i * (h + gap) : 0;
    const last = i === STEPS.length - 1;
    const arrow = i < STEPS.length - 1 ? (vertical ? `<path d="M${w / 2} ${y + h + 4}v${gap - 10}m-4-4 4 4 4-4" fill="none" stroke="#5a6678" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>` : `<path d="M${x + w + 5} ${h / 2}h${gap - 10}m-4-4 4 4-4 4" fill="none" stroke="#5a6678" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>`) : "";
    return `<g transform="translate(${x} ${y})"><rect width="${w}" height="${h}" rx="12" fill="${last ? "rgb(240 163 94 / 0.14)" : "#1a222c"}" stroke="${last ? "rgb(240 163 94 / 0.5)" : "rgb(255 255 255 / 0.08)"}"/><text x="14" y="${vertical ? 23 : 26}" fill="${last ? "#f6b87d" : "#8b97a7"}" font-size="11" font-weight="700" letter-spacing=".06em">${String(i + 1).padStart(2, "0")}</text><text x="${vertical ? 40 : 14}" y="${vertical ? 23 : 47}" fill="#eaeff5" font-size="14" font-weight="650">${esc(title)}</text><text x="${vertical ? 14 : 14}" y="${vertical ? 41 : 66}" fill="#b4c0cf" font-size="12">${esc(l1)}</text><text x="14" y="${vertical ? 41 : 66}" fill="#b4c0cf" font-size="12">${esc(vertical ? `${l1} ${l2}` : l1)}</text>${vertical ? "" : `<text x="14" y="82" fill="#b4c0cf" font-size="12">${esc(l2)}</text>`}</g>${arrow}`;
  }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="${vertical ? "m-v" : "m-h"}" role="img" aria-label="Pipeline: ${STEPS.map((s) => s[0]).join(", then ")}">${nodes}</svg>`;
}

function sensitivity(o: Overview): string {
  const fars = [...new Set(o.sensitivity.map((s) => s.far_low))].sort((a, b) => b - a);
  const mmus = [...new Set(o.sensitivity.map((s) => s.mmu_ha))].sort((a, b) => a - b);
  const vals = o.sensitivity.map((s) => s.changed_ha);
  const [lo, hi] = [Math.min(...vals), Math.max(...vals)];
  const cell = (far: number, mmu: number) => {
    const s = o.sensitivity.find((x) => x.far_low === far && x.mmu_ha === mmu);
    if (!s) return "<td></td>";
    const t = (s.changed_ha - lo) / (hi - lo || 1);
    return `<td class="num${s.is_default ? " dflt" : ""}" style="--t:${t.toFixed(2)}" ${s.is_default ? 'aria-label="default setting"' : ""}>${fmtInt(s.changed_ha)}</td>`;
  };
  return `<div class="table-wrap sens"><table><thead><tr><th>False-alarm rate ↓ · MMU (ha) →</th>${mmus.map((m) => `<th class="num">${m}</th>`).join("")}</tr></thead><tbody>${fars.map((f) => `<tr><th scope="row">${f.toExponential(0).replace("e-", "e−")}</th>${mmus.map((m) => cell(f, m)).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function facilityRows(o: Overview): string {
  const max = Math.max(...o.facilities.map((f) => f.changed_ha), 1);
  return `<ul class="fac">${o.facilities
    .map((f) => {
      const main = Object.entries(f.change_types).sort((a, b) => b[1] - a[1])[0]?.[0];
      return `<li><a href="${hrefFor("map", { layer: "facilities" })}"><span class="fac-id">${esc(f.facility_id)}</span><span class="fac-n"><b>${esc(f.name)}</b><small>${main ? esc(typeInfo(main).label) : "no change detected"}</small></span><span class="fac-v num">${fmtHa(f.changed_ha)} ha</span><span class="bar"><i style="width:${Math.max(f.changed_ha > 0 ? 2 : 0, (f.changed_ha / max) * 100).toFixed(1)}%;background:${main ? typeInfo(main).color : "#5a6678"}"></i></span></a></li>`;
    })
    .join("")}</ul>`;
}

/** The change polygons drawn as a small SVG, coloured by type: the landing page's picture of the result. */
async function footprintArt(host: HTMLElement): Promise<void> {
  const [manifest, changes] = await Promise.all([loadManifest(), loadChanges()]);
  const [[s, w], [n, e]] = manifest.rasters.before.bounds;
  const W = 400;
  const H = Math.round((W * (n - s)) / ((e - w) * Math.cos((((n + s) / 2) * Math.PI) / 180)));
  const px = ([x, y]: number[]) => `${(((x as number) - w) / (e - w) * W).toFixed(1)},${(((n - (y as number)) / (n - s)) * H).toFixed(1)}`;
  const byType = new Map<string, string[]>();
  for (const f of changes.features) {
    const polys = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.type === "MultiPolygon" ? f.geometry.coordinates : [];
    const d = polys.map((rings) => rings.map((r) => `M${r.map(px).join("L")}Z`).join("")).join("");
    byType.set(f.properties.change_type, [...(byType.get(f.properties.change_type) ?? []), d]);
  }
  const paths = CHANGE_TYPES.filter((t) => byType.has(t.key)).map((t) => `<path d="${(byType.get(t.key) ?? []).join("")}" fill="${t.color}" fill-opacity=".85" stroke="${t.color}" stroke-width=".6" stroke-linejoin="round"/>`);
  const grid = [1, 2, 3].map((i) => `<path d="M${(W * i) / 4} 0V${H}M0 ${(H * i) / 4}H${W}" />`).join("");
  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Map of the ${changes.features.length} change polygons coloured by type"><g stroke="rgb(255 255 255 / 0.06)" fill="none">${grid}</g><rect x=".5" y=".5" width="${W - 1}" height="${H - 1}" rx="6" fill="none" stroke="rgb(255 255 255 / 0.14)" stroke-dasharray="3 4"/>${paths.join("")}</svg>`;
  host.classList.add("ready");
}

export async function createOverview(_ctx: AppContext): Promise<View> {
  const el = document.createElement("div");
  el.className = "view";
  el.innerHTML = `<div class="page ov"><div class="ov-loading muted"><span class="spinner"></span> Loading results…</div></div>`;
  const page = q(el, ".page");
  let rendered = false;

  async function render(): Promise<void> {
    let o: Overview;
    try {
      o = await loadOverview();
    } catch (err) {
      page.innerHTML = `<div class="callout error"><b>Could not load the overview numbers</b><p>${esc(err instanceof Error ? err.message : String(err))}. They are exported by <code>python -m s2cd.site</code> to <code>${esc(dataUrl("overview.json").pathname)}</code>.</p></div>`;
      return;
    }
    const h = o.headline;
    page.innerHTML = `
    <section class="hero">
      <div class="hero-copy">
        <div class="hero-brand">${logoSvg(44, "hero")}<span class="eyebrow">Sentinel-2 · B02 B03 B04 · 10 m</span></div>
        <h1>What changed at an open-pit copper mine in Zambia?</h1>
        <p class="lede">s2change finds, types and explains change between two Sentinel-2 dates using only the visible bands, then lets you query it. Default pair: <b class="num">${esc(o.pair.before)} → ${esc(o.pair.after)}</b>, nominal dates (each is a mosaic of two scenes).</p>
        <div class="cta"><a class="btn primary" href="${hrefFor("map")}">Open the map</a><a class="btn" href="${hrefFor("report")}">Read the report</a></div>
        <dl class="hero-stats"><div><dt>Changed</dt><dd class="num">${fmtInt(h.changed_ha)} ha</dd></div><div><dt>Polygons</dt><dd class="num">${fmtInt(h.n_polygons)}</dd></div><div><dt>Run time</dt><dd class="num">${Math.round(h.runtime_s)} s</dd></div></dl>
      </div>
      <a class="hero-art card" href="${hrefFor("map")}" aria-label="Open the map"><div class="art" id="art"><span class="spinner"></span></div><span class="art-cap">${fmtInt(h.n_polygons)} change polygons, coloured by type</span></a>
    </section>

    <section aria-labelledby="h-find"><h2 id="h-find">Headline findings</h2><div class="findings">${findings(o)}</div></section>

    <section aria-labelledby="h-types"><h2 id="h-types">What changed</h2><div class="card types">${typesBar(o)}</div></section>

    <section aria-labelledby="h-brief"><h2 id="h-brief">The brief, and where to see it</h2>${brief()}</section>

    <section aria-labelledby="h-method" id="method"><h2 id="h-method">Method at a glance</h2><div class="card method">${methodSvg(false)}${methodSvg(true)}<p class="note">Thresholds are false-alarm rates under a measured noise model, so they do not depend on tuned constants. Full reasoning, with the dead ends, is in the <a href="${hrefFor("report")}">report</a>.</p></div></section>

    <section class="two" aria-labelledby="h-sens"><div><h2 id="h-sens">How stable is ${fmtInt(h.changed_ha)} ha?</h2><p class="sub">Changed hectares for four false-alarm rates and four minimum mapping units. The default is outlined.</p>${sensitivity(o)}</div><div><h2>Facilities</h2><p class="sub">Hectares changed inside each register facility.</p>${facilityRows(o)}</div></section>

    <footer class="ov-foot">
      <div class="links">${ext(REPO_URL, "GitHub")}${ext(`${BLOB_URL}report.md`, "Report source")}${ext(`${BLOB_URL}docs/api.md`, "API docs")}${ext(LIVE_URL, "Live site")}<a class="btn" href="${hrefFor("downloads")}">Data downloads</a></div>
      <p class="note">Contains modified Copernicus Sentinel-2 data (2023). Basemap: Esri World Imagery. Built by Dzaky Zakiyal Fawwaz for the Solafune geospatial take-home.</p>
    </footer>`;
    void footprintArt(q(page, "#art")).catch(() => (q(page, "#art").textContent = ""));
  }

  el.addEventListener("click", (e) => {
    const a = (e.target as HTMLElement).closest<HTMLAnchorElement>('a[href="#method"]');
    if (!a) return;
    e.preventDefault();
    q(el, "#method").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  return {
    el,
    show() {
      if (rendered) return;
      rendered = true;
      void render();
    },
    hide() {},
  };
}
