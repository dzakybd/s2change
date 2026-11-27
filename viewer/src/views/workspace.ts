import "maplibre-gl/dist/maplibre-gl.css";
import "../styles/map.css";
import type { Feature } from "geojson";
import type { MapGeoJSONFeature } from "maplibre-gl";
import type { AppContext, View } from "../app";
import { fetchRun, STATIC_RUN_IDS } from "../api";
import { loadAoi, loadDefaultDataset, loadTruePasses, loadTruePassesManifest, TRUE_PASSES_ID } from "../data";
import { q, qa } from "../domain";
import { DEFAULT_LAYER, LAYERS, layerById } from "../map/layers";
import type { LayerDef } from "../map/layers";
import { Menu } from "../map/menu";
import { MapStage } from "../map/stage";
import { Swipe } from "../map/swipe";
import { installTooltips } from "../map/tooltip";
import { facilityCard, featureCard, legendHtml } from "../map/cards";
import type { ListUi } from "../map/changes";
import { featureBounds, RunOverlay } from "../overlays";
import type { Route } from "../router";
import { replaceParams } from "../router";
import type { ChangeProps, Dataset, FacilityProps } from "../types";
import type { AgentHost } from "./analyst";

installTooltips();

const ICON_SWIPE = `<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="M10 3v14M6.5 7 3 10l3.5 3M13.5 7 17 10l-3.5 3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

const ICON_OBLIQUE = `<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="m10 3 7 3.5-7 3.5-7-3.5zM3 10.2l7 3.5 7-3.5M3 13.7 10 17.2l7-3.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
const ICON_AGENT = `<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="M4 4.5h12a1.5 1.5 0 0 1 1.5 1.5v6.5a1.5 1.5 0 0 1-1.5 1.5H9l-3.5 3v-3H4a1.5 1.5 0 0 1-1.5-1.5V6A1.5 1.5 0 0 1 4 4.5Z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M6.5 8.5h7M6.5 11h4.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`;
const ICON_EYE = `<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="M1.8 10S4.8 4.5 10 4.5 18.2 10 18.2 10 15.2 15.5 10 15.5 1.8 10 1.8 10Z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><circle cx="10" cy="10" r="2.6" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
const DEFAULT_BEARING = 24;
/** Which result the map shows: the brief's method, this method on the provided mosaics, or on true single-pass dates. */
type ResultId = "original" | "mosaic" | "true";
const km2 = (ha: number | undefined) => (ha === undefined ? "" : `${(ha / 100).toFixed(2)} km²`);
const phone = window.matchMedia("(max-width: 720px)");
/** Wide screens open with the agent beside the map; narrower ones, where it would stack over the map, open on the map alone. */
const agentByDefault = (): boolean => window.matchMedia("(min-width: 901px)").matches;

const TEMPLATE = `
<div class="ws" data-mode="map">
  <aside class="ws-chat" id="chat-panel" aria-label="Agent" data-collapsed="true"><button type="button" class="chat-rail" aria-controls="chat-panel" aria-expanded="false" title="Open the agent">${ICON_AGENT}<span>Agent</span></button><div class="chat-body" hidden></div></aside>
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
    <div class="hud hud-tl"><div class="hud-row" id="hud-layer"></div><div class="cmp"><div class="cmp-chip glass"><div class="cmp-text"><span class="cmp-k">Comparing</span><span class="cmp-v num"></span><span class="cmp-n"></span></div></div></div></div>
    <div class="hud hud-tr">
      <div class="tools glass">
        <button type="button" class="tool" id="t-changes" aria-pressed="true" title="Show or hide the changes, to see the imagery underneath">${ICON_EYE}<span>Changes</span></button>
        <button type="button" class="tool" id="t-swipe" aria-pressed="true" title="Compare with the before image">${ICON_SWIPE}<span>Swipe</span></button>
        <button type="button" class="tool" id="t-agent" aria-pressed="false" aria-controls="chat-panel" title="Ask the agent; it answers and moves this map">${ICON_AGENT}<span>Agent</span></button>
        <button type="button" class="tool" id="t-oblique" aria-pressed="false" title="Tilt the camera (2.5D)">${ICON_OBLIQUE}<span>2.5D</span></button>
      </div>
      <div class="oblique-panel glass" hidden>
        <label class="rng"><span>Bearing <output id="o-bearing-v">${DEFAULT_BEARING}°</output></span><input id="o-bearing" type="range" min="0" max="359" step="1" value="${DEFAULT_BEARING}" aria-label="Bearing in degrees"></label>
        <label class="chk"><input id="o-extrude" type="checkbox" checked><span>Raise changes by confidence<small>Taller = stronger change. A data view, not terrain.</small></span></label>
      </div>
      <aside class="inspector glass" hidden aria-live="polite"></aside>
    </div>
    <div class="result-chip glass" hidden><span></span><button type="button" class="btn ghost icon sm" aria-label="Remove query result from the map">×</button></div>
    <section class="legend glass" aria-label="Legend"></section>
    <div class="ws-loading"><span class="spinner"></span><span>Loading the map…</span></div>
  </div>
</div>`;

export async function createWorkspace(ctx: AppContext): Promise<View> {
  const el = document.createElement("div");
  el.className = "view fill";
  el.innerHTML = TEMPLATE;
  const ws = q(el, ".ws");

  let stage: MapStage | null = null;
  let chatOpen = false;
  let pending: Route | null = null;
  let analystLoaded = false;
  const state = { layer: DEFAULT_LAYER, swipe: true, oblique: false, bearing: DEFAULT_BEARING, extrude: true, changes: true };

  let applyRoute: (route: Route) => void = () => {};

  async function init(): Promise<void> {
    const [provided, aoi] = await Promise.all([loadDefaultDataset(), loadAoi()]);
    const truePasses = await loadTruePassesManifest().catch(() => null);
    let ds: Dataset = provided;
    const facName = new Map(provided.facilities.features.map((f) => [f.properties.facility_id, f.properties.name]));
    const hidden = new Set<string>();
    const list: ListUi = { tab: "list", showAll: false, noise: false };
    let selectedFid: number | null = null;

    const swipe = new Swipe(q(el, ".swipe"), q(el, ".swipe-handle"), q(el, "#map-compare"));
    const inspector = q(el, ".inspector");
    const legend = q(el, ".legend");

    /* ---------- inspector ---------- */
    const strongerThan = (c: number): number | null => {
      const all = ds.changes.features.map((f) => f.properties.confidence).filter((v) => Number.isFinite(v));
      return all.length && Number.isFinite(c) ? all.filter((v) => v < c).length / all.length : null;
    };
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
      dataset: ds,
      initialLayer,
      initialSwipe: state.swipe,
      onFeatureClick: (f: MapGeoJSONFeature | null) => {
        if (!f) return closeInspector();
        const p = f.properties as unknown as ChangeProps;
        stage?.select(p.fid);
        markSelected(p.fid);
        showInspector(featureCard(p, p.facility_id ? (facName.get(p.facility_id) ?? null) : null, strongerThan(p.confidence)));
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
    const resultMenu = new Menu("Result", (id) => void setResult(id as ResultId));
    q(el, "#hud-layer").prepend(resultMenu.root);
    const menuItems = () =>
      LAYERS.map((l) => {
        return { id: l.id, label: l.label, hint: l.hint, disabled: !!l.providedOnly && !ds.nominal, disabledReason: "Only for the provided pair" };
      });

    const labels = () => {
      q(el, ".swipe-label.l").textContent = `Before · ${ds.dates.before}`;
      q(el, ".swipe-label.r").textContent = `After · ${ds.dates.after}`;
    };
    /** The analysed pair, read-only: the Result menu is the only way to change it. */
    const renderChip = () => {
      q(el, ".cmp-v").textContent = `${ds.dates.before} → ${ds.dates.after}`;
      q(el, ".cmp-n").textContent = ds.nominal ? "nominal dates (each a two-pass mosaic)" : "true acquisition dates (single passes)";
    };

    const renderLegend = () => {
      legend.innerHTML = legendHtml(layerById(state.layer), ds, hidden, list, selectedFid, ds.facilities.features.map((f) => f.properties));
      legend.querySelector(".lg-head")?.setAttribute("aria-expanded", String(!legend.classList.contains("collapsed")));
    };

    const syncSwipeUi = () => {
      const def = layerById(state.layer);
      const btn = q<HTMLButtonElement>(el, "#t-swipe");
      btn.setAttribute("aria-pressed", String(def.swipe && state.swipe));
      btn.title = def.swipe ? "Compare with the before image" : "Swipe between the two Sentinel-2 dates (switches to that layer)";
      swipe.setEnabled(st.swipeActive);
      labels();
    };

    const syncHash = () => {
      replaceParams("map", {
        layer: state.layer === DEFAULT_LAYER ? undefined : state.layer,
        result: ds.id === TRUE_PASSES_ID ? "true" : undefined,
        swipe: state.swipe ? undefined : "0",
        changes: state.changes ? undefined : "0",
        oblique: state.oblique ? undefined : "0",
        bearing: state.oblique && state.bearing !== DEFAULT_BEARING ? String(state.bearing) : undefined,
        extrude: state.oblique && !state.extrude ? "0" : undefined,
        agent: chatOpen === agentByDefault() ? undefined : chatOpen ? "1" : "0",
      });
    };

    // Layer to go back to when the swipe button switched to Sentinel-2 by itself and is then turned off.
    let swipeReturn: string | null = null;

    function setLayer(id: string): void {
      let def: LayerDef = layerById(id);
      if (def.providedOnly && !ds.nominal) def = layerById(DEFAULT_LAYER);
      if (def.id !== state.layer) swipeReturn = null;
      state.layer = def.id;
      st.setLayer(def);
      menu.setValue(def.id);
      syncResult();
      syncSwipeUi();
      renderLegend();
      syncHash();
    }

    menu.setItems(menuItems(), state.layer);

    /* ---------- result: original method, ours on the mosaics, ours on true passes ---------- */
    const currentResult = (): ResultId =>
      ds.id === TRUE_PASSES_ID ? "true" : state.layer === "baseline" ? "original" : "mosaic";
    const resultItems = () => {
      const items: { id: ResultId; label: string; hint: string; disabled?: boolean; disabledReason?: string }[] = [
        { id: "original", label: "Original, as is", hint: `The brief's Euclidean + Otsu method on the provided mosaics: ${km2(provided.summary?.["euclidean_baseline"])}, mostly pass seams and haze` },
        { id: "mosaic", label: "Ours, mosaic passes", hint: `This method on the provided rasters, each a mosaic of two passes, normalised per pass: ${km2(provided.summary?.["robust_cva"])}` },
        {
          id: "true",
          label: "Ours, true passes",
          hint: truePasses ? `This method on single-pass scenes of the true dates, ${truePasses.date_before} → ${truePasses.date_after}: ${km2(truePasses.summary["robust_cva"])}` : "",
          disabled: !truePasses,
          disabledReason: "The true-pass export is not available",
        },
      ];
      return items;
    };
    const syncResult = () => resultMenu.setItems(resultItems(), currentResult());

    async function setResult(id: ResultId): Promise<void> {
      if (id === "true") {
        if (ds.id !== TRUE_PASSES_ID) useDataset(await loadTruePasses());
      } else if (id === "original" || id === "mosaic") {
        if (ds.id !== provided.id) useDataset(provided);
        if (id === "original") setLayer("baseline");
        else if (state.layer === "baseline") setLayer(DEFAULT_LAYER);
      }
      syncResult();
      syncHash();
    }

    q(el, "#t-swipe").addEventListener("click", () => {
      // Layers without a pair (intensity overlays aside) switch to Sentinel-2 to swipe.
      const from = state.layer;
      const toPair = !layerById(from).swipe;
      if (toPair) {
        state.swipe = true;
        setLayer("types");
        swipeReturn = from;
      } else if (state.swipe && swipeReturn) {
        // Swipe was only opened to compare: go back to the layer the user was on (intensity, facilities, ...).
        const back = swipeReturn;
        setLayer(back);
      } else {
        state.swipe = !state.swipe;
      }
      st.setSwipe(state.swipe);
      syncSwipeUi();
      syncHash();
    });

    const setChanges = (on: boolean) => {
      state.changes = on;
      q(el, "#t-changes").setAttribute("aria-pressed", String(on));
      st.setChangesVisible(on);
    };
    q(el, "#t-changes").addEventListener("click", () => {
      setChanges(!state.changes);
      syncHash();
    });

    /* ---------- 2.5D view ---------- */
    const bearingIn = q<HTMLInputElement>(el, "#o-bearing");
    const extrudeIn = q<HTMLInputElement>(el, "#o-extrude");
    const showBearing = () => {
      bearingIn.value = String(state.bearing);
      q(el, "#o-bearing-v").textContent = `${state.bearing}°`;
    };
    /** `panel`: also show the bearing and raise settings (not when a phone opens straight into 2.5D). */
    const setOblique = (on: boolean, panel = true) => {
      state.oblique = on;
      q(el, "#t-oblique").setAttribute("aria-pressed", String(on));
      q(el, ".oblique-panel").hidden = !(on && panel);
      st.setOblique(on, state.bearing);
      syncHash();
    };
    q(el, "#t-oblique").addEventListener("click", () => setOblique(!state.oblique));
    bearingIn.addEventListener("input", () => {
      state.bearing = Number(bearingIn.value);
      showBearing();
      st.setBearing(state.bearing);
    });
    bearingIn.addEventListener("change", syncHash);
    st.main.on("rotateend", () => {
      if (!state.oblique) return;
      state.bearing = Math.round((st.main.getBearing() + 360) % 360);
      showBearing();
      syncHash();
    });
    extrudeIn.addEventListener("change", () => {
      state.extrude = extrudeIn.checked;
      st.setExtrude(state.extrude);
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
      const fac = t.closest<HTMLElement>("[data-fac]")?.dataset["fac"];
      if (fac) return void openFacility(fac);
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
    if (phone.matches) legend.classList.add("collapsed");

    /** Zoom to a facility of the current pair and open its card (with this pair's numbers). */
    const openFacility = (facilityId: string): string | null => {
      const f = ds.facilities.features.find((x) => x.properties.facility_id === facilityId);
      if (!f) return `No facility ${facilityId}.`;
      setLayer("facilities");
      fit([f as Feature], 15);
      st.select(null);
      showInspector(facilityCard(f.properties));
      return null;
    };

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
      showInspector(featureCard(p, p.facility_id ? (facName.get(p.facility_id) ?? null) : null, strongerThan(p.confidence)));
    };
    const rowFid = (e: Event): number | null => {
      const v = (e.target as HTMLElement).closest<HTMLElement>(".ch-row")?.dataset["fid"];
      return v === undefined ? null : Number(v);
    };
    legend.addEventListener("pointerover", (e) => {
      if (e.pointerType === "mouse") st.setHover(rowFid(e));
    });
    legend.addEventListener("pointerleave", () => st.setHover(null));

    /* ---------- dataset: the provided mosaics or the true passes ---------- */
    function useDataset(next: Dataset): void {
      ds = next;
      st.setDataset(next);
      renderChip();
      closeInspector();
      hidden.clear();
      st.setHiddenTypes(hidden);
      menu.setItems(menuItems(), state.layer);
      setLayer(state.layer);
    }
    renderChip();

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
        return state.layer === id ? null : `The ${id} layer is not available for this comparison.`;
      },
      setView({ swipe, oblique }) {
        if (swipe !== undefined && swipe !== (state.swipe && layerById(state.layer).swipe)) q<HTMLButtonElement>(el, "#t-swipe").click();
        if (oblique !== undefined && oblique !== state.oblique) setOblique(oblique);
        return null;
      },
      showFacility(facilityId) {
        if (ds.id !== provided.id) useDataset(provided); // the agent's facility numbers come from the provided pair
        return openFacility(facilityId);
      },
      reset: () => runs.setHighlight([]),
    };

    const resizer = new ResizeObserver(() => st.resize());
    resizer.observe(q(el, ".ws-stage"));

    /* ---------- agent panel ---------- */
    /** Open the agent panel, or collapse it to a slim rail (on phones, out of the way entirely). */
    function setChat(open: boolean, sync = true): void {
      chatOpen = open;
      ws.dataset["mode"] = open ? "agent" : "map";
      const panel = q(el, ".ws-chat");
      panel.dataset["collapsed"] = String(!open);
      q(panel, ".chat-body").hidden = !open;
      q(panel, ".chat-rail").setAttribute("aria-expanded", String(open));
      q(el, "#t-agent").setAttribute("aria-pressed", String(open));
      if (open && !analystLoaded) {
        analystLoaded = true;
        void import("./analyst").then((m) => m.mountAnalyst(q(panel, ".chat-body"), host, ctx.api));
      }
      requestAnimationFrame(() => st.resize());
      if (sync) syncHash();
    }
    q(el, "#t-agent").addEventListener("click", () => setChat(!chatOpen));
    q(el, ".chat-rail").addEventListener("click", () => setChat(true));
    q(el, ".ws-chat").addEventListener("chat-close", () => setChat(false));

    /* ---------- apply route ---------- */
    applyRoute = (route: Route) => {
      const agent = route.params.get("agent");
      setChat(agent === null ? agentByDefault() : agent === "1", false);
      {
        const p = route.params;
        const layer = layerById(p.get("layer"));
        state.swipe = p.get("swipe") !== "0";
        st.setSwipe(state.swipe);
        if (layer.id !== state.layer || !el.dataset["init"]) setLayer(layer.id);
        else syncSwipeUi();
        const bearing = Number(p.get("bearing") ?? DEFAULT_BEARING);
        state.bearing = Number.isFinite(bearing) ? Math.round(((bearing % 360) + 360) % 360) : DEFAULT_BEARING;
        showBearing();
        setChanges(p.get("changes") !== "0");
        extrudeIn.checked = state.extrude = p.get("extrude") !== "0";
        st.setExtrude(state.extrude);
        const wantOblique = p.get("oblique") !== "0";
        if (wantOblique !== state.oblique) setOblique(wantOblique, !el.dataset["init"] ? !phone.matches : true);
        const wantTrue = p.get("result") === "true";
        if (wantTrue ? ds.id !== TRUE_PASSES_ID : ds.id === TRUE_PASSES_ID) void setResult(wantTrue ? "true" : "mosaic");
        el.dataset["init"] = "1";
        syncQueryResult();
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
  };
}
