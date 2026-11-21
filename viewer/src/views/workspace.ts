import "maplibre-gl/dist/maplibre-gl.css";
import "../styles/map.css";
import type { Feature } from "geojson";
import type { MapGeoJSONFeature } from "maplibre-gl";
import type { AppContext, View } from "../app";
import { fetchRun, STATIC_RUN_IDS } from "../api";
import { loadAoi, loadDefaultDataset, loadFacilities } from "../data";
import { q, qa } from "../domain";
import { DEFAULT_LAYER, LAYERS, layerById } from "../map/layers";
import type { LayerDef } from "../map/layers";
import { Menu } from "../map/menu";
import { DatePicker } from "../map/picker";
import { MapStage } from "../map/stage";
import { Swipe } from "../map/swipe";
import { facilityCard, featureCard, legendHtml } from "../map/cards";
import type { ListUi } from "../map/changes";
import { featureBounds, RunOverlay } from "../overlays";
import type { Route } from "../router";
import { replaceParams } from "../router";
import type { ChangeProps, Dataset, FacilityProps } from "../types";
import type { AgentHost } from "./analyst";

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
      </div>
      <aside class="inspector glass" hidden aria-live="polite"></aside>
    </div>
    <div class="result-chip glass" hidden><span></span><button type="button" class="btn ghost icon sm" aria-label="Remove query result from the map">×</button></div>
    <section class="legend glass" aria-label="Legend"></section>
    <div class="ws-loading"><span class="spinner"></span><span>Loading the map…</span></div>
  </div>
</div>`;

interface UiState {
  layer: string;
  swipe: boolean;
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
  const state: UiState = { layer: DEFAULT_LAYER, swipe: true };

  let applyRoute: (route: Route) => void = () => {};

  async function init(): Promise<void> {
    const [dataset, aoi, facilities] = await Promise.all([loadDefaultDataset(), loadAoi(), loadFacilities()]);
    const provided = dataset;
    let ds: Dataset = dataset;
    const facName = new Map(facilities.features.map((f) => [f.properties.facility_id, f.properties.name]));
    const hidden = new Set<string>();
    const list: ListUi = { tab: "list", showAll: false, noise: false };
    let selectedFid: number | null = null;

    const swipe = new Swipe(q(el, ".swipe"), q(el, ".swipe-handle"), q(el, "#map-compare"));
    const inspector = q(el, ".inspector");
    const legend = q(el, ".legend");

    /* ---------- inspector ---------- */
    const markSelected = (fid: number | null) => {
      selectedFid = fid;
      for (const r of qa(legend, ".ch-row")) {
        if (r.dataset["fid"] === String(fid)) r.setAttribute("aria-current", "true");
        else r.removeAttribute("aria-current");
      }
    };

    const showInspector = (html: string) => {
      inspector.innerHTML = html;
      inspector.hidden = false;
      inspector.classList.remove("enter");
      void inspector.offsetWidth; // restart the entrance
      inspector.classList.add("enter");
    };
    const closeInspector = () => {
      inspector.hidden = true;
      stage?.select(null);
      markSelected(null);
    };

    /* ---------- stage ---------- */
    const initialLayer = layerById(state.layer);
    stage = new MapStage({
      main: q(el, "#map"),
      compare: q(el, "#map-compare"),
      aoi,
      facilities,
      dataset: ds,
      initialLayer,
      initialSwipe: state.swipe,
      onFeatureClick: (f: MapGeoJSONFeature | null) => {
        if (!f) return closeInspector();
        const p = f.properties as unknown as ChangeProps;
        stage?.select(p.fid);
        markSelected(p.fid);
        showInspector(featureCard(p, p.facility_id ? (facName.get(p.facility_id) ?? null) : null));
      },
      onFacilityClick: (p: FacilityProps) => {
        stage?.select(null);
        // MapLibre serialises nested objects in feature properties; parse them back.
        const raw = p.change_types as unknown;
        const types = typeof raw === "string" ? (JSON.parse(raw) as Record<string, number> | null) : p.change_types;
        showInspector(facilityCard({ ...p, change_types: types }));
      },
    });
    await stage.init();
    const st = stage;

    /* ---------- layer menu ---------- */
    const menu = new Menu("Layer", (id) => setLayer(id));
    q(el, "#hud-layer").prepend(menu.root);
    const menuItems = () =>
      LAYERS.map((l) => {
        return { id: l.id, label: l.label, hint: l.hint, disabled: !!l.providedOnly && !ds.nominal, disabledReason: "Only for the provided pair" };
      });

    const labels = () => {
      q(el, ".swipe-label.l").textContent = `Before · ${ds.dates.before}`;
      q(el, ".swipe-label.r").textContent = `After · ${ds.dates.after}`;
    };

    const renderLegend = () => {
      legend.innerHTML = legendHtml(layerById(state.layer), ds, hidden, list, selectedFid);
      legend.querySelector(".lg-head")?.setAttribute("aria-expanded", String(!legend.classList.contains("collapsed")));
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
      });
    };

    function setLayer(id: string): void {
      let def: LayerDef = layerById(id);
      if (def.providedOnly && !ds.nominal) def = layerById(DEFAULT_LAYER);
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

    /* ---------- legend ---------- */
    legend.addEventListener("click", (e) => {
      const t = e.target as HTMLElement;
      const tab = t.closest<HTMLElement>("[data-tab]")?.dataset["tab"];
      if (tab === "list" || tab === "legend") {
        list.tab = tab;
        return renderLegend();
      }
      const more = t.closest<HTMLElement>("[data-list]")?.dataset["list"];
      if (more) {
        if (more === "all") list.showAll = !list.showAll;
        else list.noise = !list.noise;
        return renderLegend();
      }
      const chg = t.closest<HTMLElement>("[data-fid]");
      if (chg) return void openChange(Number(chg.dataset["fid"]));
      const row = t.closest<HTMLElement>("[data-type]");
      if (row?.dataset["type"]) {
        const k = row.dataset["type"];
        if (hidden.has(k)) hidden.delete(k);
        else hidden.add(k);
        st.setHiddenTypes(hidden);
        const top = legend.scrollTop;
        renderLegend(); // the head tally follows the hidden types
        legend.scrollTop = top;
        return;
      }
      const head = t.closest<HTMLElement>(".lg-head");
      if (head) {
        const open = head.getAttribute("aria-expanded") !== "true";
        head.setAttribute("aria-expanded", String(open));
        legend.classList.toggle("collapsed", !open);
      }
    });
    const phone = window.matchMedia("(max-width: 720px)");
    if (phone.matches) legend.classList.add("collapsed");

    /** Fly to a change, select it and open its card: the "What changed" list rows and the card share this path. */
    const openChange = (fid: number) => {
      const f = ds.changes.features[fid];
      if (!f) return;
      const b = featureBounds([f as Feature]);
      const { clientWidth: w, clientHeight: h } = q(el, ".ws-stage");
      if (b) st.fit(b, { maxZoom: 16, padding: w > 720 ? { top: 90, bottom: 90, left: Math.min(380, w * 0.3), right: Math.min(400, w * 0.3) } : { top: 90, bottom: Math.round(h * 0.5), left: 30, right: 30 } });
      st.select(fid);
      markSelected(fid);
      if (phone.matches) {
        legend.classList.add("collapsed");
        legend.querySelector(".lg-head")?.setAttribute("aria-expanded", "false");
      }
      const p = f.properties;
      showInspector(featureCard(p, p.facility_id ? (facName.get(p.facility_id) ?? null) : null));
    };
    const rowFid = (e: Event): number | null => {
      const v = (e.target as HTMLElement).closest<HTMLElement>(".ch-row")?.dataset["fid"];
      return v === undefined ? null : Number(v);
    };
    legend.addEventListener("pointerover", (e) => {
      if (e.pointerType === "mouse") st.hoverFeature(rowFid(e));
    });
    legend.addEventListener("pointerleave", () => st.hoverFeature(null));

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

    inspector.addEventListener("click", (e) => {
      const act = (e.target as HTMLElement).closest<HTMLElement>("[data-act]")?.dataset["act"];
      if (act === "close") closeInspector();
    });
    document.addEventListener("keydown", (e) => {
      if (el.hidden || e.key !== "Escape") return;
      if (!inspector.hidden) closeInspector();
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
      setView({ swipe }) {
        if (swipe !== state.swipe) q<HTMLButtonElement>(el, "#t-swipe").click();
        return null;
      },
      showFacility(facilityId) {
        const f = facilities.features.find((x) => x.properties.facility_id === facilityId);
        if (!f) return `No facility ${facilityId}.`;
        if (ds.id !== provided.id) picker.reset(); // facility numbers belong to the provided pair
        setLayer("facilities");
        syncHash();
        fit([f as Feature], 15);
        stage?.select(null);
        showInspector(facilityCard(f.properties));
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
    hide() {},
  };
}
