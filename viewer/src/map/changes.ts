import { esc, fmtArea, fmtInt, fmtKm2, ha, typeInfo } from "../domain";
import type { ChangeCollection, ChangeProps, Dataset } from "../types";

/** The plain-words name of a change; older comparisons without a headline fall back to the type label. */
export const changeTitle = (p: ChangeProps): string => p.headline?.trim() || typeInfo(p.change_type).label;
/** Where it is, in words; older comparisons fall back to the facility id or the rule's explanation. */
export const changeWhere = (p: ChangeProps): string => p.location?.trim() || (p.facility_id ? `at ${p.facility_id}` : p.explanation);

export const LIST_CAP = 50;
export const LABEL_COUNT = 8;

export interface ListUi {
  tab: "list" | "legend";
  showAll: boolean;
  /** Include the `noise_or_artifact` polygons. */
  noise: boolean;
}

const isNoise = (p: ChangeProps): boolean => p.change_type === "noise_or_artifact";

/** Changes ranked by area, largest first; `noise_or_artifact` and legend-hidden types are left out unless asked for. */
export function rankChanges(changes: ChangeCollection, hidden: ReadonlySet<string>, noise: boolean): ChangeProps[] {
  return changes.features
    .map((f) => f.properties)
    .filter((p) => !hidden.has(p.change_type) && (noise || !isNoise(p)))
    .sort((a, b) => b.area_m2 - a.area_m2);
}

/** The few largest real changes that get a name on the map. */
export function labelTargets(changes: ChangeCollection, hidden: ReadonlySet<string>): ChangeProps[] {
  return rankChanges(changes, hidden, false).slice(0, LABEL_COUNT);
}

export const labelText = (p: ChangeProps): string => `${changeTitle(p)} · ${fmtArea(p.area_m2)}`;

export function listHtml(ds: Dataset, hidden: ReadonlySet<string>, ui: ListUi, selected: number | null): string {
  const rows = rankChanges(ds.changes, hidden, ui.noise);
  const total = rows.reduce((s, p) => s + ha(p.area_m2), 0);
  const nNoise = ds.changes.features.filter((f) => isNoise(f.properties) && !hidden.has(f.properties.change_type)).length;
  const shown = ui.showAll ? rows : rows.slice(0, LIST_CAP);
  const items = shown
    .map((p) => {
      const t = typeInfo(p.change_type);
      const cls = `ch-row${isNoise(p) ? " is-noise" : ""}`;
      return `<li><button type="button" class="${cls}" data-fid="${p.fid}"${p.fid === selected ? ' aria-current="true"' : ""} title="${esc(t.label)}"><i class="swatch" style="background:${t.color}"></i><span class="ch-t"><b>${esc(changeTitle(p))}</b><small>${esc(changeWhere(p))}</small></span><em class="num">${fmtArea(p.area_m2)}</em></button></li>`;
    })
    .join("");
  const more = rows.length > LIST_CAP ? `<button type="button" class="linkbtn" data-list="all">${ui.showAll ? `Show top ${LIST_CAP}` : `Show all ${fmtInt(rows.length)}`}</button>` : "";
  const noise = nNoise ? `<button type="button" class="linkbtn" data-list="noise">${ui.noise ? "Hide" : "Show"} ${fmtInt(nNoise)} likely-not-real</button>` : "";
  return `<p class="lg-sub num">${fmtInt(rows.length)} ${rows.length === 1 ? "change" : "changes"} · ${fmtKm2(total)}. Largest first.</p>${
    rows.length ? `<ul class="ch-list">${items}</ul>` : '<p class="note">No changes to list.</p>'
  }${more || noise ? `<div class="ch-foot">${more}${noise}</div>` : ""}`;
}
