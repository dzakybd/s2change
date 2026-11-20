import "maplibre-gl/dist/maplibre-gl.css";
import "../styles/map.css";
import type { Feature, Polygon, Position } from "geojson";
import type { MapGeoJSONFeature } from "maplibre-gl";
import type { AppContext, View } from "../app";
import { aoiStats, fetchRun, STATIC_RUN_IDS } from "../api";
import { loadAoi, loadDefaultDataset, loadFacilities, loadMinerals, dataUrl } from "../data";
import { q } from "../domain";
import { aoiStatsFromPolygons } from "../map/geo";
import { DEFAULT_LAYER, LAYERS, layerById } from "../map/layers";
import type { LayerDef } from "../map/layers";
import { Menu } from "../map/menu";
import { DatePicker } from "../map/picker";
import { MapStage } from "../map/stage";
import { Swipe } from "../map/swipe";
import { aoiCard, facilityCard, featureCard, legendHtml } from "../map/cards";
import { featureBounds, RunOverlay } from "../overlays";
import type { Route } from "../router";
import { replaceParams } from "../router";
import type { AoiStats, ChangeProps, Dataset, FacilityProps } from "../types";
import type { AgentHost } from "./analyst";

const ICON_DRAW = `<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="M4 14.5 3 6l5-2.5 7 3 2 7-6 3z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><circle cx="3" cy="6" r="1.6" fill="currentColor"/><circle cx="8" cy="3.5" r="1.6" fill="currentColor"/><circle cx="15" cy="6.5" r="1.6" fill="currentColor"/></svg>`;
const ICON_OBLIQUE = `<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="m10 3 7 3.5-7 3.5-7-3.5zM3 10.2l7 3.5 7-3.5M3 13.7 10 17.2l7-3.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
const ICON_SWIPE = `<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="M10 3v14M6.5 7 3 10l3.5 3M13.5 7 17 10l-3.5 3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

const TEMPLATE = `
<div class="ws" data-mode="map">
  <aside class="ws-chat" id="chat-panel" aria-label="Analyst agent" hidden></aside>
  <div class="ws-stage">
    <div class="map-box">
      <div id="map" class="map"></div>
      <div id="map-compare" class="map"></div>
      <div class="swipe" hidden>
        <div class="swipe-handle" role="slider" tabindex="0" aria-label="Before and after swipe" aria-valuemin="0" aria-valuemax="100" aria-valuenow="50">
          <span class="knob"></span><span class="swipe-label l"></span><span class="swipe-label r"></span>
        </div>
      </div>
    </div>
    <div class="hud hud-tl"><div class="hud-row" id="hud-layer"></div><div id="hud-cmp"></div></div>
    <div class="hud hud-tr">
      <div class="tools glass">
        <button type="button" class="tool" id="t-swipe" aria-pressed="true" title="Compare with the before image">${ICON_SWIPE}<span>Swipe</span></button>
        <button type="button" class="tool" id="t-draw" aria-pressed="false" title="Draw an area to measure change inside it">${ICON_DRAW}<span>Draw area</span></button>
        <button type="button" class="tool" id="t-oblique" aria-pressed="false" title="Tilt the camera">${ICON_OBLIQUE}<span>Oblique</span></button>
      </div>
      <div class="oblique-panel glass" hidden>
        <label class="rng"><span>Bearing <output id="o-bearing-v">24°</output></span><input id="o-bearing" type="range" min="0" max="360" step="1" value="24" aria-label="Bearing in degrees"></label>
        <label class="chk"><input id="o-extrude" type="checkbox"><span>Extrude by magnitude<small>Height follows confidence</small></span></label>
      </div>
      <aside class="inspector glass" hidden aria-live="polite"></aside>
    </div>
    <div class="draw-bar glass" hidden role="status"><span class="draw-msg"></span><button type="button" class="btn sm" data-draw="undo">Undo</button><button type="button" class="btn sm primary" data-draw="finish">Finish</button><button type="button" class="btn sm ghost" data-draw="cancel">Cancel</button></div>
    <div class="result-chip glass" hidden><span></span><button type="button" class="btn ghost icon sm" aria-label="Remove query result from the map">×</button></div>
    <section class="legend glass" aria-label="Legend"></section>
    <div class="ws-loading"><span class="spinner"></span><span>Loading the map…</span></div>
  </div>
</div>`;

interface UiState {
  layer: string;
  swipe: boolean;
  oblique: boolean;
  bearing: number;
  extrude: boolean;
}

export async function createWorkspace(ctx: AppContext): Promise<View> {
  const el = document.createElement("div");
  el.className = "view fill";
  el.innerHTML = TEMPLATE;
  const ws = q(el, ".ws");

  let stage: MapStage | null = null;
  let mode: "map" | "analyst" = "map";
  let pending: Route | null = null;
  let analystLoaded = false;
  const state: UiState = { layer: DEFAULT_LAYER, swipe: true, oblique: false, bearing: 24, extrude: false };

  let applyRoute: (route: Route) => void = () => {};

  async function init(): Promise<void> {
    const [dataset, aoi, facilities, minerals] = await Promise.all([loadDefaultDataset(), loadAoi(), loadFacilities(), loadMinerals()]);
    const provided = dataset;
    let ds: Dataset = dataset;
    const facName = new Map(facilities.features.map((f) => [f.properties.facility_id, f.properties.name]));
    const hidden = new Set<string>();
    let aoiShape: Polygon | null = null;
    let obliqueUi = false;

    const swipe = new Swipe(q(el, ".swipe"), q(el, ".swipe-handle"), q(el, "#map-compare"));
    const inspector = q(el, ".inspector");
    const legend = q(el, ".legend");

    /* ---------- inspector ---------- */
    let inspecting: "feature" | "facility" | "aoi" | null = null;
    const showInspector = (html: string, kind: "feature" | "facility" | "aoi") => {
      inspecting = kind;
      inspector.innerHTML = html;
      inspector.hidden = false;
      inspector.classList.remove("enter");
      void inspector.offsetWidth; // restart the entrance
      inspector.classList.add("enter");
    };
    const closeInspector = () => {
      inspector.hidden = true;
      inspecting = null;
      stage?.select(null);
      if (aoiShape) {
        aoiShape = null;
        stage?.setAoiShape(null);
      }
    };

    const onAoi = async (ring: Position[]) => {
      aoiShape = { type: "Polygon", coordinates: [ring] };
      showInspector(`<div class="ins-wait"><span class="spinner"></span> Measuring change inside the area…</div>`, "aoi");
      const api = await ctx.api;
      let stats: AoiStats | null = null;
      if (api.kind === "ready" && !api.mock && ds.id === provided.id) {
        try {
          stats = { ...(await aoiStats(api, aoiShape)), source: "api" };
        } catch {
          stats = null; // fall back to the browser estimate
        }
      }
      stats ??= aoiStatsFromPolygons(aoiShape, ds.changes);
      if (inspecting === "aoi") showInspector(aoiCard(stats), "aoi");
    };

    /* ---------- stage ---------- */
    const initialLayer = layerById(state.layer);
    stage = new MapStage({
      main: q(el, "#map"),
      compare: q(el, "#map-compare"),
      aoi,
      facilities,
      dataset: ds,
      minerals,
      mineralsBase: dataUrl("minerals/"),
      initialLayer,
      initialSwipe: state.swipe,
      onFeatureClick: (f: MapGeoJSONFeature | null) => {
        if (!f) return closeInspector();
        const p = f.properties as unknown as ChangeProps;
        stage?.select(p.fid);
        showInspector(featureCard(p, p.facility_id ? (facName.get(p.facility_id) ?? null) : null), "feature");
      },
      onFacilityClick: (p: FacilityProps) => {
        stage?.select(null);
        // MapLibre serialises nested objects in feature properties; parse them back.
        const raw = p.change_types as unknown;
        const types = typeof raw === "string" ? (JSON.parse(raw) as Record<string, number> | null) : p.change_types;
        showInspector(facilityCard({ ...p, change_types: types }), "facility");
      },
      onDrawChange: ({ points, active }) => {
        const bar = q(el, ".draw-bar");
        bar.hidden = !active;
        q(el, "#t-draw").setAttribute("aria-pressed", String(active));
        q(el, ".ws-stage").toggleAttribute("data-drawing", active);
        if (active) {
          q(bar, ".draw-msg").textContent = points < 3 ? `Click to add points (${points}/3 minimum)` : `${points} points. Double-click or Finish to close the shape`;
          q<HTMLButtonElement>(bar, '[data-draw="finish"]').disabled = points < 3;
          q<HTMLButtonElement>(bar, '[data-draw="undo"]').disabled = points < 1;
        }
      },
      onDrawDone: (ring) => void onAoi(ring),
    });
    await stage.init();
    const st = stage;

    /* ---------- layer menu ---------- */
    const menu = new Menu("Layer", (id) => setLayer(id));
    q(el, "#hud-layer").prepend(menu.root);
    const menuItems = () =>
      LAYERS.map((l) => {
        const unavailable = (l.needsRedness && !minerals) || (l.providedOnly && !ds.nominal);
        return { id: l.id, label: l.label, hint: l.hint, disabled: !!unavailable, disabledReason: l.needsRedness && !minerals ? "Not in this export" : "Only for the provided pair" };
      });

    const labels = () => {
      const def = layerById(state.layer);
      const redness = def.id === "redness";
      q(el, ".swipe-label.l").textContent = `${redness ? "Redness · " : ""}Before · ${ds.dates.before}`;
      q(el, ".swipe-label.r").textContent = `${redness ? "Redness · " : ""}After · ${ds.dates.after}`;
    };

    const renderLegend = () => {
      legend.innerHTML = legendHtml(layerById(state.layer), ds, hidden, minerals, state.oblique, state.extrude);
    };

    const syncSwipeUi = () => {
      const def = layerById(state.layer);
      const btn = q<HTMLButtonElement>(el, "#t-swipe");
      btn.disabled = !def.swipe;
      btn.setAttribute("aria-pressed", String(def.swipe && state.swipe));
      swipe.setEnabled(st.swipeActive);
      labels();
    };

    const syncHash = () => {
      if (mode !== "map") return;
      replaceParams("map", {
        layer: state.layer === DEFAULT_LAYER ? undefined : state.layer,
        swipe: state.swipe ? undefined : "0",
        oblique: state.oblique ? "1" : undefined,
        bearing: state.oblique && state.bearing !== 24 ? String(Math.round(state.bearing)) : undefined,
        extrude: state.oblique && state.extrude ? "1" : undefined,
      });
    };

    function setLayer(id: string): void {
      let def: LayerDef = layerById(id);
      if ((def.needsRedness && !minerals) || (def.providedOnly && !ds.nominal)) def = layerById(DEFAULT_LAYER);
      state.layer = def.id;
      st.setLayer(def);
      menu.setValue(def.id);
      syncSwipeUi();
      renderLegend();
      syncHash();
    }

    menu.setItems(menuItems(), state.layer);

    q(el, "#t-swipe").addEventListener("click", () => {
      state.swipe = !state.swipe;
      st.setSwipe(state.swipe);
      syncSwipeUi();
      syncHash();
    });

    /* ---------- oblique ---------- */
    const bearingIn = q<HTMLInputElement>(el, "#o-bearing");
    const extrudeIn = q<HTMLInputElement>(el, "#o-extrude");
    const setOblique = (on: boolean) => {
      state.oblique = on;
      q(el, "#t-oblique").setAttribute("aria-pressed", String(on));
      q(el, ".oblique-panel").hidden = !on;
      obliqueUi = on;
      st.setOblique(on, state.bearing);
      renderLegend();
      syncHash();
    };
    q(el, "#t-oblique").addEventListener("click", () => setOblique(!obliqueUi));
    bearingIn.addEventListener("input", () => {
      state.bearing = Number(bearingIn.value);
      q(el, "#o-bearing-v").textContent = `${state.bearing}°`;
      st.setBearing(state.bearing);
    });
    bearingIn.addEventListener("change", syncHash);
    st.main.on("rotateend", () => {
      if (!state.oblique) return;
      state.bearing = Math.round((st.main.getBearing() + 360) % 360);
      bearingIn.value = String(state.bearing);
      q(el, "#o-bearing-v").textContent = `${state.bearing}°`;
      syncHash();
    });
    extrudeIn.addEventListener("change", () => {
      state.extrude = extrudeIn.checked;
      st.setExtrude(state.extrude);
      renderLegend();
      syncHash();
    });

    /* ---------- legend ---------- */
    legend.addEventListener("click", (e) => {
      const t = e.target as HTMLElement;
      const row = t.closest<HTMLElement>("[data-type]");
      if (row?.dataset["type"]) {
        const k = row.dataset["type"];
        if (hidden.has(k)) hidden.delete(k);
        else hidden.add(k);
        st.setHiddenTypes(hidden);
        row.setAttribute("aria-pressed", String(!hidden.has(k)));
        return;
      }
      const head = t.closest<HTMLElement>(".lg-head");
      if (head) {
        const open = head.getAttribute("aria-expanded") !== "true";
        head.setAttribute("aria-expanded", String(open));
        legend.classList.toggle("collapsed", !open);
      }
    });
    if (window.matchMedia("(max-width: 720px)").matches) legend.classList.add("collapsed");

    /* ---------- date picker ---------- */
    const picker = new DatePicker({
      api: ctx.api,
      provided,
      onDataset: (next) => {
        ds = next;
        st.setDataset(next);
        picker.setDataset(next);
        closeInspector();
        hidden.clear();
        st.setHiddenTypes(hidden);
        menu.setItems(menuItems(), state.layer);
        if (layerById(state.layer).providedOnly && !next.nominal) setLayer(DEFAULT_LAYER);
        else setLayer(state.layer);
        labels();
      },
    });
    q(el, "#hud-cmp").append(picker.root);

    /* ---------- draw tool ---------- */
    q(el, "#t-draw").addEventListener("click", () => {
      if (st.drawing) return st.cancelDraw();
      closeInspector();
      st.startDraw();
    });
    q(el, ".draw-bar").addEventListener("click", (e) => {
      const act = (e.target as HTMLElement).closest<HTMLElement>("[data-draw]")?.dataset["draw"];
      if (act === "undo") st.undoPoint();
      else if (act === "finish") st.finishDraw();
      else if (act === "cancel") st.cancelDraw();
    });

    inspector.addEventListener("click", (e) => {
      const act = (e.target as HTMLElement).closest<HTMLElement>("[data-act]")?.dataset["act"];
      if (act === "close") closeInspector();
      else if (act === "redraw") {
        closeInspector();
        st.startDraw();
      }
    });
    document.addEventListener("keydown", (e) => {
      if (el.hidden || e.key !== "Escape") return;
      if (st.drawing) st.cancelDraw();
      else if (!inspector.hidden) closeInspector();
    });

    /* ---------- query result from the Database tab ---------- */
    const chip = q(el, ".result-chip");
    let shownResult: unknown = null;
    const syncQueryResult = () => {
      const r = ctx.queryResult;
      if (r === shownResult) return;
      shownResult = r;
      st.setQueryResult(r?.geojson ?? null);
      chip.hidden = !r;
      if (r) {
        q(chip, "span").textContent = `${r.label} · ${r.geojson.features.length} feature${r.geojson.features.length === 1 ? "" : "s"}`;
        const b = featureBounds(r.geojson.features as Feature[]);
        if (b) st.fit(b, { padding: 110, maxZoom: 16 });
      }
    };
    q(chip, "button").addEventListener("click", () => {
      ctx.queryResult = null;
      syncQueryResult();
    });

    /* ---------- analyst host ---------- */
    const runs = new RunOverlay(st.main, ds.changes);
    const fit = (features: Feature[], maxZoom: number) => {
      const b = featureBounds(features);
      if (b) st.fit(b, { padding: 80, maxZoom });
    };
    const host: AgentHost = {
      flyTo(bbox) {
        const [w, s, e, n] = bbox;
        if (![w, s, e, n].every(Number.isFinite) || w >= e || s >= n) return "The agent sent an invalid area.";
        st.main.fitBounds([[w, s], [e, n]], { padding: 48, duration: 900, maxZoom: 17 });
        return null;
      },
      async highlight(runId, ids, fitView) {
        const wanted = ids.filter((i) => Number.isInteger(i));
        let features: Feature[];
        if (STATIC_RUN_IDS.has(runId)) features = runs.assignmentFeatures(wanted);
        else {
          const api = await ctx.api;
          if (api.kind !== "ready") return "Loading another run needs the live backend.";
          features = runs.selectIds(await runs.loadRun(runId, (id) => fetchRun(api, id)), wanted);
        }
        if (!features.length) return `No polygons with those ids in run ${runId}.`;
        runs.setHighlight(features);
        if (fitView) fit(features, 16);
        return null;
      },
      async showRun(runId) {
        if (STATIC_RUN_IDS.has(runId)) {
          runs.showRun(null);
          if (layerById(state.layer).polygons === "none") setLayer(DEFAULT_LAYER);
          return null;
        }
        const api = await ctx.api;
        if (api.kind !== "ready") return "Loading another run needs the live backend.";
        const fc = await runs.loadRun(runId, (id) => fetchRun(api, id));
        runs.showRun(fc);
        fit(fc.features, 15);
        return null;
      },
      setLayer(id) {
        setLayer(id);
        syncHash();
        return state.layer === id ? null : `The ${id} layer is not available for this comparison.`;
      },
      setView({ swipe, oblique }) {
        if (swipe !== undefined && swipe !== state.swipe) q<HTMLButtonElement>(el, "#t-swipe").click();
        if (oblique !== undefined && oblique !== obliqueUi) setOblique(oblique);
        return null;
      },
      showFacility(facilityId) {
        const f = facilities.features.find((x) => x.properties.facility_id === facilityId);
        if (!f) return `No facility ${facilityId}.`;
        if (!ds.nominal) return "Facilities are shown for the provided pair only; reset the dates first.";
        setLayer("facilities");
        syncHash();
        fit([f as Feature], 15);
        stage?.select(null);
        showInspector(facilityCard(f.properties), "facility");
        return null;
      },
      async compareDates(before, after) {
        await picker.runFromLink(before, after);
        return null;
      },
      reset: () => runs.setHighlight([]),
    };

    const resizer = new ResizeObserver(() => st.resize());
    resizer.observe(q(el, ".ws-stage"));

    /* ---------- apply route ---------- */
    applyRoute = (route: Route) => {
      mode = route.name === "analyst" ? "analyst" : "map";
      ws.dataset["mode"] = mode;
      q(el, ".ws-chat").hidden = mode !== "analyst";
      if (mode === "analyst" && !analystLoaded) {
        analystLoaded = true;
        void import("./analyst").then((m) => m.mountAnalyst(q(el, ".ws-chat"), host, ctx.api));
      }
      if (mode === "map") {
        const p = route.params;
        const layer = layerById(p.get("layer"));
        state.swipe = p.get("swipe") !== "0";
        st.setSwipe(state.swipe);
        if (layer.id !== state.layer || !el.dataset["init"]) setLayer(layer.id);
        else syncSwipeUi();
        const wantOblique = p.get("oblique") === "1";
        const bearing = Number(p.get("bearing") ?? 24);
        state.bearing = Number.isFinite(bearing) ? bearing : 24;
        bearingIn.value = String(state.bearing);
        q(el, "#o-bearing-v").textContent = `${state.bearing}°`;
        extrudeIn.checked = p.get("extrude") === "1";
        state.extrude = extrudeIn.checked;
        st.setExtrude(state.extrude);
        if (wantOblique !== state.oblique || (wantOblique && !el.dataset["init"])) setOblique(wantOblique);
        const [b, a] = [p.get("before"), p.get("after")];
        if (b && a && !el.dataset["linked"]) {
          el.dataset["linked"] = "1";
          void picker.runFromLink(b, a);
        }
        el.dataset["init"] = "1";
        syncQueryResult();
      } else {
        syncSwipeUi();
        renderLegend();
      }
      requestAnimationFrame(() => st.resize());
    };
    el.dataset["ready"] = "1";
    q(el, ".ws-loading").remove();
    (window as unknown as { __stage?: MapStage }).__stage = st;
    if (pending) applyRoute(pending);
  }

  void init();

  return {
    el,
    show(route) {
      pending = route;
      if (stage) applyRoute(route);
    },
    hide() {
      stage?.cancelDraw();
    },
  };
}
