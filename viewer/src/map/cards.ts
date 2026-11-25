import { CHANGE_TYPES, CHEVRON, CLOSE, FACILITY_CLASS_LABEL, esc, facilityColor, facilityIcon, fmtArea, fmtInt, fmtKm2, fmtPct, ha, typeIcon, typeInfo } from "../domain";
import type { ChangeCollection, ChangeProps, Dataset, FacilityProps } from "../types";
import { changeTitle, changeWhere, listHtml, rankChanges } from "./changes";
import type { ListUi } from "./changes";
import type { LayerDef } from "./layers";
import { INTENSITY_RAMP } from "./layers";

const closeBtn = `<button type="button" class="btn ghost icon sm" data-act="close" aria-label="Close panel">${CLOSE}</button>`;

const sentence = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

export function featureCard(p: ChangeProps, facilityName: string | null): string {
  const t = typeInfo(p.change_type);
  const conf = Math.max(0, Math.min(1, p.confidence));
  const see = p.evidence?.trim() || p.explanation;
  const check = p.what_to_check?.trim();
  return `<header class="ins-head">${typeIcon(p.change_type, 28)}<div><h3>${esc(changeTitle(p))}</h3><p class="muted">${esc(sentence(changeWhere(p)))}</p></div>${closeBtn}</header>
  <p class="ins-meta"><b class="num">${fmtArea(p.area_m2)}</b><span>changed</span><span class="chip">${typeIcon(p.change_type, 14)}${esc(t.label)}</span></p>
  <div class="why"><span>What we see</span><p>${esc(sentence(see || "No evidence recorded."))}</p></div>
  ${check ? `<div class="why check"><span>What to check</span><p>${esc(check)}</p></div>` : ""}
  ${p.touches_aoi_edge ? '<p class="note">Touches the edge of the area of interest, so its shape may be clipped.</p>' : ""}
  <details class="tech"><summary>Technical details</summary>
  <dl class="facts">
    <div><dt>Change type</dt><dd>${esc(t.label)} <span class="muted">(${esc(p.brief_category || "change")})</span></dd></div>
    <div><dt>Confidence</dt><dd class="num">${p.confidence.toFixed(2)}<span class="meter" aria-hidden="true"><i style="width:${(conf * 100).toFixed(0)}%"></i></span></dd></div>
    <div><dt>Significance</dt><dd class="num">−log10 p = ${p.neglog10_p.toFixed(1)}</dd></div>
    <div><dt>Direction</dt><dd>${esc(p.change_direction)}</dd></div>
    <div class="wide"><dt>Rule explanation</dt><dd>${esc(sentence(p.explanation || "None recorded."))}</dd></div>
    <div class="wide"><dt>Facility</dt><dd>${p.facility_id ? `${esc(facilityName ?? p.facility_id)} <span class="muted">(${esc(p.facility_id)})</span>` : '<span class="muted">Outside the register</span>'}</dd></div>
  </dl></details>`;
}

export function facilityCard(p: FacilityProps): string {
  const types = Object.entries(p.change_types ?? {}).sort((a, b) => b[1] - a[1]);
  const max = types[0]?.[1] ?? 0;
  return `<header class="ins-head">${facilityIcon(p.class, 28, facilityColor(p.facility_id))}<div><h3>${esc(p.name)}</h3><p class="muted">${esc(FACILITY_CLASS_LABEL[p.class] ?? p.class)} · ${esc(p.facility_id)}</p></div>${closeBtn}</header>
  <div class="tiles"><div><b class="num">${fmtKm2(p.changed_ha)}</b><span>changed</span></div><div><b class="num">${fmtPct(p.changed_share, p.changed_share < 0.1 ? 1 : 0)}</b><span>of the facility</span></div><div><b class="num">${fmtKm2(p.area_ha)}</b><span>total</span></div></div>
  ${types.length ? `<h4>What changed</h4>${bars(types, max)}` : '<p class="note">No change detected inside this facility.</p>'}
  <p class="note">${esc(p.summary)}</p>`;
}

function bars(rows: [string, number][], max: number): string {
  return `<ul class="bars">${rows
    .map(([k, v]) => {
      const t = typeInfo(k);
      return `<li><span class="bar-l">${typeIcon(k, 16)}${esc(t.label)}</span><b class="num">${fmtKm2(v)}</b><span class="bar"><i style="width:${max ? Math.max(2, (v / max) * 100).toFixed(1) : 0}%;background:${t.color}"></i></span></li>`;
    })
    .join("")}</ul>`;
}

interface TypeTally {
  key: string;
  count: number;
  ha: number;
}

function tallyTypes(changes: ChangeCollection): { rows: TypeTally[]; count: number; ha: number } {
  const m = new Map<string, TypeTally>();
  let total = 0;
  for (const f of changes.features) {
    const p = f.properties;
    const e = m.get(p.change_type) ?? { key: p.change_type, count: 0, ha: 0 };
    e.count++;
    e.ha += ha(p.area_m2);
    total += ha(p.area_m2);
    m.set(p.change_type, e);
  }
  const order = new Map(CHANGE_TYPES.map((t, i) => [t.key, i]));
  const rows = [...m.values()].sort((a, b) => b.ha - a.ha || (order.get(a.key) ?? 99) - (order.get(b.key) ?? 99));
  return { rows, count: changes.features.length, ha: total };
}

export function legendHtml(def: LayerDef, ds: Dataset, hidden: ReadonlySet<string>, list: ListUi, selected: number | null, facilities: readonly FacilityProps[] = []): string {
  const tally = tallyTypes(ds.changes);
  let body = "";
  let title = def.label;
  let count = "";
  if (def.typed) {
    title = "What changed";
    const vis = rankChanges(ds.changes, hidden, list.noise);
    count = `${fmtInt(vis.length)} · ${fmtKm2(vis.reduce((a, p) => a + ha(p.area_m2), 0))}`;
    const seg = `<div class="seg" role="tablist" aria-label="Panel view">${(["list", "legend"] as const).map((k) => `<button type="button" role="tab" data-tab="${k}" aria-selected="${list.tab === k}">${k === "list" ? "List" : "Legend"}</button>`).join("")}</div>`;
    body = list.tab === "list" ? seg + listHtml(ds, hidden, list, selected) : seg + `<p class="lg-sub num">${fmtInt(tally.count)} ${tally.count === 1 ? "polygon" : "polygons"} · ${fmtKm2(tally.ha)}. Click a type to hide it.</p><ul class="lg-types">${tally.rows
      .map((r) => {
        const t = typeInfo(r.key);
        return `<li><button type="button" class="lg-row" data-type="${esc(r.key)}" aria-pressed="${!hidden.has(r.key)}" title="${esc(t.blurb)}">${typeIcon(r.key, 20)}<span>${esc(t.label)}</span><b class="num">${r.count}</b><em class="num">${fmtKm2(r.ha)}</em></button></li>`;
      })
      .join("")}</ul>`;
  } else if (def.id === "intensity") {
    body = `<div class="ramp" style="background:${INTENSITY_RAMP}"></div><div class="ramp-l"><span>no change</span><span>strong change</span></div><p class="note">log10 of the robust Mahalanobis distance d² per 10 m pixel, before thresholding.</p>`;
  } else if (def.baseline) {
    body = `<p class="lg-sub"><i class="swatch" style="background:#ef4444"></i> Provided baseline: 111.4 km², 42 % of the area</p><p class="lg-sub"><i class="swatch" style="background:transparent;box-shadow:inset 0 0 0 2px #0f172a"></i> This work, outlined in its type colours: 4.13 km²</p><p class="note">The straight diagonal bands are the seams between satellite passes inside each "date"; the baseline reads them, and haze, as change. Report section 1, "The data trap".</p>`;
  } else if (def.id === "facilities") {
    const rows = [...facilities].sort((a, b) => b.changed_ha - a.changed_ha);
    body = `<p class="lg-sub">${rows.length} facilities · area changed inside each. Click one for its breakdown.</p><ul class="lg-types">${rows
      .map((f) => `<li><button type="button" class="lg-row" data-fac="${esc(f.facility_id)}" title="${esc(FACILITY_CLASS_LABEL[f.class] ?? f.class)}">${facilityIcon(f.class, 20, facilityColor(f.facility_id))}<span>${esc(f.name)}</span><b class="num">${esc(f.facility_id)}</b><em class="num">${fmtKm2(f.changed_ha)}</em></button></li>`)
      .join("")}</ul><p class="note">Names, classes and rough boxes are drawn by an analyst on the 2023-08-12 image (not in the provided data). Each outline is computed from the provided bands: the bare or water ground inside its box.</p>`;
  } else {
    body = `<p class="note">${esc(def.hint)}.${ds.nominal ? " The two dates are nominal: each is a mosaic of two Sentinel-2 scenes (07-01/07-04 and 08-25/09-02)." : ""} Outlines are the change polygons, coloured by type.</p>`;
  }
  return `<button type="button" class="lg-head" aria-expanded="true"><b>${esc(title)}</b>${count ? `<span class="lg-count num">${count}</span>` : ""}${CHEVRON}</button><div class="lg-body">${body}</div>`;
}
