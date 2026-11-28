import "../styles/database.css";
import type { AppContext, View } from "../app";
import type { ReadyApi } from "../api";
import { ApiError, dbConnection, dbExportUrl, dbPage, dbSchema, dbUri, friendlyError } from "../api";
import { BLOB_URL, LIVE_URL, esc, fmtInt, q } from "../domain";
import { renderRich } from "../rich";
import type { Route } from "../router";
import { hrefFor, replaceParams } from "../router";
import type { DbPage, DbTable } from "../types";
import { withUnlock } from "../unlock";

const SIZES = [10, 25, 50, 100] as const;
/** change_features has 30+ columns; the diagram shows the ones a reader needs, the explorer shows all of them. */
const ERD_COLUMNS: Record<string, string[]> = {
  change_features: ["id", "run_id", "facility_id", "change_type", "brief_category", "headline", "area_m2", "confidence", "geometry"],
};

interface State {
  table: string;
  page: number;
  size: number;
  sort: string | undefined;
  desc: boolean;
  filter: { column: string; value: string } | null;
}

function erd(tables: DbTable[]): string {
  const type = (t: string) => t.replace(/\(.*\)/, "").replace(/[^A-Za-z0-9]+/g, "_");
  const links = tables.flatMap((t) =>
    t.links.map((l) => {
      const target = tables.find((x) => x.table === l.ref_table);
      const oneToOne = target && t.primary_key.join() === l.column;
      return `  ${l.ref_table} ${oneToOne ? "||--||" : l.enforced ? "||--o{" : "|o--o{"} ${t.table} : "${l.column}${l.enforced ? "" : " (logical)"}"`;
    }),
  );
  const entities = tables.map((t) => {
    const keep = ERD_COLUMNS[t.table];
    const cols = t.columns.filter((c) => !keep || keep.includes(c.name));
    const rows = cols.map((c) => {
      const tag = [t.primary_key.includes(c.name) ? "PK" : "", t.links.some((l) => l.column === c.name) ? "FK" : ""].filter(Boolean).join(",");
      const note = c.name === t.geometry?.column ? ` "${t.geometry.type}, EPSG:${t.geometry.srid}"` : "";
      return `    ${type(c.type)} ${c.name}${tag ? ` ${tag}` : ""}${note}`;
    });
    if (cols.length < t.columns.length) rows.push(`    etc more_columns "${t.columns.length - cols.length} more in the explorer"`);
    return `  ${t.table} {\n${rows.join("\n")}\n  }`;
  });
  return ["erDiagram", ...links, ...entities].join("\n");
}

const isNum = (v: unknown) => typeof v === "number";
const num = new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 });

function cell(v: unknown): { text: string; full: string; cls: string } {
  if (v === null || v === undefined) return { text: "null", full: "", cls: "nul" };
  if (typeof v === "number") return { text: num.format(v), full: String(v), cls: "num" };
  if (typeof v === "boolean") return { text: String(v), full: "", cls: "bool" };
  const s = typeof v === "object" ? JSON.stringify(v) : String(v);
  return { text: s.length > 90 ? `${s.slice(0, 87)}…` : s, full: s.length > 90 ? s : "", cls: typeof v === "object" ? "json" : "" };
}

export async function createDatabase(ctx: AppContext): Promise<View> {
  const el = document.createElement("div");
  el.className = "view";
  el.innerHTML = `<div class="page db">
    <div class="page-head"><h1>Explore the PostGIS database</h1><p>The change polygons and the facility register live in PostGIS on Supabase. Browse every table a page at a time, follow the keys between them, and take the data with you as SQL.</p></div>
    <div id="db-body"><div class="db-skel" role="status"><span class="sr-only">Connecting to the database…</span><i class="skel s-card"></i><i class="skel s-tall"></i></div></div></div>`;
  const body = q(el, "#db-body");
  let started = false;
  let pending: Route | null = null;

  function offline(reason: string): void {
    body.innerHTML = `<div class="callout offline"><b>The database is live-only</b><p>${esc(reason)} The explorer reads PostGIS through the API, which GitHub Pages cannot host.</p><p><a class="btn primary" href="${LIVE_URL}/#/database" target="_blank" rel="noopener noreferrer">Open s2change.dzaky.ai.id</a> <a class="btn" href="${BLOB_URL}sql/schema_postgis.sql" target="_blank" rel="noopener noreferrer">Read the schema</a></p></div>`;
  }

  async function start(api: ReadyApi): Promise<void> {
    let tables: DbTable[];
    try {
      tables = await dbSchema(api);
    } catch (err) {
      body.innerHTML = `<div class="callout error"><b>Could not load the schema</b><p>${esc(friendlyError(err, "the database"))}</p></div>`;
      return;
    }
    const byName = new Map(tables.map((t) => [t.table, t]));
    const params = pending?.params ?? new URLSearchParams();
    const first = byName.has(params.get("t") ?? "") ? (params.get("t") as string) : "change_features";
    const fc = params.get("fc");
    const state: State = {
      table: first,
      page: Math.max(1, Number(params.get("p")) || 1),
      size: SIZES.includes(Number(params.get("n")) as (typeof SIZES)[number]) ? Number(params.get("n")) : 25,
      sort: params.get("sort") ?? undefined,
      desc: params.get("desc") === "1",
      filter: fc ? { column: fc, value: params.get("fv") ?? "" } : null,
    };

    body.innerHTML = `
      <section class="card conn" aria-labelledby="conn-h">
        <div class="conn-head"><h2 id="conn-h">Connection</h2><div class="conn-badges" id="conn-badges"><span class="muted"><span class="spinner"></span></span></div></div>
        <div class="conn-uri"><code id="conn-uri" class="mono">…</code><button type="button" class="btn sm primary" id="copy-uri" aria-live="polite">Copy URI</button></div>
        <p class="muted conn-hint">A read-only role (<code>SELECT</code> only, 5 s statement timeout). Copying the full URI, password included, needs the access code. Try it: <code>psql "&lt;uri&gt;" -c "SELECT postgis_full_version();"</code></p>
        <div class="conn-actions"><a class="btn sm" href="${dbExportUrl(api)}" download>Download the whole database (.sql)</a><span class="muted">Schema, constraints, indexes and every row; geometry as EWKB, loads into any PostGIS.</span></div>
      </section>
      <details class="card erd" open><summary><span>How the tables relate</span><small class="muted">Entity-relationship diagram of the live schema. Click a table to open it below.</small></summary><div id="db-erd"><span class="muted"><span class="spinner"></span> Drawing…</span></div></details>
      <section class="explorer" aria-labelledby="ex-h">
        <h2 id="ex-h" class="sr-only">Table explorer</h2>
        <div class="ex-tabs" role="tablist" aria-label="Tables">${tables.map((t) => `<button type="button" role="tab" class="ex-tab" data-table="${esc(t.table)}"><span class="mono">${esc(t.table)}</span><small class="num">${fmtInt(t.rows)}</small></button>`).join("")}</div>
        <div class="card ex-panel" role="tabpanel"><div id="ex-info"></div><div id="ex-grid" aria-live="polite"></div></div>
      </section>`;

    void dbConnection(api).then(
      (c) => {
        q(body, "#conn-badges").innerHTML = `<span class="pill">${esc(c.postgres)}</span><span class="pill geo" title="${esc(c.postgis_full)}">${esc(c.postgis)}</span><span class="pill">Supabase · ${esc(c.user.split(".")[0] ?? c.user)}</span>`;
        q(body, "#conn-uri").textContent = c.uri_masked;
      },
      (err: unknown) => (q(body, "#conn-badges").innerHTML = `<span class="muted">${esc(friendlyError(err, "the database"))}</span>`),
    );
    q(body, "#copy-uri").addEventListener("click", async (e) => {
      const btn = e.currentTarget as HTMLButtonElement;
      try {
        const { uri } = await withUnlock(ctx.api, () => dbUri(api));
        await navigator.clipboard.writeText(uri);
        btn.textContent = "Copied";
      } catch (err) {
        btn.textContent = err instanceof ApiError && err.status === 401 ? "Copy URI" : "Could not copy"; // 401: unlock cancelled
      }
      setTimeout(() => (btn.textContent = "Copy URI"), 1800);
    });

    const erdHost = q(body, "#db-erd");
    void renderRich(`\`\`\`mermaid\n${erd(tables)}\n\`\`\``).then((doc) => {
      erdHost.replaceChildren(doc);
      for (const t of tables) {
        const node = [...erdHost.querySelectorAll<SVGGElement>("g[id]")].find((g) => new RegExp(`^(?:.*-)?entity-${t.table}(?:-\\d+)?$`).test(g.id));
        if (!node) continue;
        node.classList.add("erd-node");
        node.dataset["table"] = t.table;
        node.setAttribute("tabindex", "0");
        node.setAttribute("role", "button");
        node.setAttribute("aria-label", `Open ${t.table} in the explorer`);
      }
      markErd();
    });
    const openFromErd = (target: EventTarget | null) => {
      const t = (target as Element | null)?.closest<SVGGElement>(".erd-node")?.dataset["table"];
      if (!t) return;
      open({ table: t, filter: null });
      q(body, ".explorer").scrollIntoView({ behavior: "smooth", block: "start" });
    };
    erdHost.addEventListener("click", (e) => openFromErd(e.target));
    erdHost.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") (e.preventDefault(), openFromErd(e.target));
    });

    const markErd = () => {
      for (const n of erdHost.querySelectorAll<SVGGElement>(".erd-node")) n.classList.toggle("on", n.dataset["table"] === state.table);
      for (const b of body.querySelectorAll<HTMLElement>(".ex-tab")) b.setAttribute("aria-selected", String(b.dataset["table"] === state.table));
    };

    const info = q(body, "#ex-info");
    const grid = q(body, "#ex-grid");
    let last: DbPage | null = null;
    let abort: AbortController | null = null;

    function renderInfo(t: DbTable): void {
      const incoming = tables.flatMap((x) => x.links.filter((l) => l.ref_table === t.table).map((l) => ({ from: x.table, ...l })));
      const meta = [
        `<span class="pill num">${fmtInt(t.rows)} rows</span>`,
        `<span class="pill">${t.columns.length} columns</span>`,
        t.primary_key.length ? `<span class="pill">Key <b class="mono">${esc(t.primary_key.join(", "))}</b></span>` : "",
        ...t.links.map((l) => `<span class="pill" title="${l.enforced ? "Foreign key constraint" : "Same key, not enforced"}"><b class="mono">${esc(l.column)}</b> → <button type="button" class="linkish" data-open="${esc(l.ref_table)}">${esc(l.ref_table)}</button>${l.enforced ? "" : " (logical)"}</span>`),
        ...incoming.map((l) => `<span class="pill">Referenced by <button type="button" class="linkish" data-open="${esc(l.from)}">${esc(l.from)}</button>.${esc(l.column)}</span>`),
        t.geometry ? `<span class="pill geo">${esc(t.geometry.type)} · EPSG:${t.geometry.srid}</span>` : "",
      ].join("");
      const filter = state.filter ? `<span class="filter">Filtered: <b class="mono">${esc(state.filter.column)} = ${esc(state.filter.value)}</b><button type="button" class="linkish" data-clear>Clear</button></span>` : "";
      info.innerHTML = `<div class="ex-head"><div><h3 class="mono">${esc(t.table)}</h3><p>${esc(t.description)}</p></div>
        <div class="ex-actions">${t.geometry ? '<button type="button" class="btn sm" data-map>Show these rows on the map</button>' : ""}<a class="btn sm" href="${dbExportUrl(api, t.table)}" download>Download table (.sql)</a></div></div>
        <div class="ex-meta">${meta}</div>${filter}`;
    }

    function renderGrid(p: DbPage, t: DbTable): void {
      const outgoing = new Map(t.links.map((l) => [l.column, l]));
      const incoming = tables.flatMap((x) => x.links.filter((l) => l.ref_table === t.table).map((l) => ({ from: x.table, column: l.column, key: l.ref_column })));
      const keyIdx = new Map(p.columns.map((c, i) => [c.name, i]));
      const head = p.columns
        .map((c) => {
          const geom = c.name === t.geometry?.column;
          const sorted = p.sort === c.name || (!p.sort && t.primary_key[0] === c.name);
          const dir = sorted ? (p.desc && p.sort === c.name ? "descending" : "ascending") : "none";
          return `<th scope="col" aria-sort="${dir}">${geom ? `<span title="${esc(c.type)}">${esc(c.name)}</span>` : `<button type="button" class="sort" data-sort="${esc(c.name)}" title="${esc(c.type)}">${esc(c.name)}<i aria-hidden="true">${dir === "ascending" ? "↑" : dir === "descending" ? "↓" : ""}</i></button>`}<small>${esc(c.type.replace(/\(.*\)/, ""))}</small></th>`;
        })
        .join("");
      const rows = p.rows
        .map((r) => {
          const tds = r
            .map((v, i) => {
              const col = p.columns[i]?.name ?? "";
              const c = cell(v);
              const link = outgoing.get(col);
              const inner = link && v != null ? `<button type="button" class="linkish" data-fk="${esc(link.ref_table)}" data-col="${esc(link.ref_column)}" data-val="${esc(String(v))}" title="Open ${esc(link.ref_table)} where ${esc(link.ref_column)} = ${esc(String(v))}">${esc(c.text)}</button>` : esc(c.text);
              return `<td class="${c.cls}${isNum(v) ? " num" : ""}"${c.full ? ` title="${esc(c.full)}"` : ""}>${inner}</td>`;
            })
            .join("");
          const related = incoming
            .map((l) => {
              const v = r[keyIdx.get(l.key) ?? -1];
              return v == null ? "" : `<button type="button" class="chip sm" data-fk="${esc(l.from)}" data-col="${esc(l.column)}" data-val="${esc(String(v))}">${esc(l.from)} ›</button>`;
            })
            .join("");
          return `<tr>${tds}${incoming.length ? `<td class="rel">${related}</td>` : ""}</tr>`;
        })
        .join("");
      const from = p.total ? (p.page - 1) * p.size + 1 : 0;
      const to = Math.min(p.total, p.page * p.size);
      grid.innerHTML = `${p.rows.length ? `<div class="table-wrap ex-table"><table><thead><tr>${head}${incoming.length ? '<th scope="col">Related</th>' : ""}</tr></thead><tbody>${rows}</tbody></table></div>` : `<p class="muted empty">${state.filter ? `No rows have ${esc(state.filter.column)} = ${esc(state.filter.value)}. Clear the filter above to see every row.` : "This table has no rows yet. The pipeline fills it on its next run."}</p>`}
        <div class="pager"><span class="muted num">Rows ${fmtInt(from)}–${fmtInt(to)} of ${fmtInt(p.total)} · ${p.elapsed_ms} ms</span>
          <label class="muted">Rows per page <select id="ex-size">${SIZES.map((s) => `<option${s === p.size ? " selected" : ""}>${s}</option>`).join("")}</select></label>
          <span class="pager-nav"><button type="button" class="btn sm" data-page="${p.page - 1}" ${p.page <= 1 ? "disabled" : ""} aria-label="Previous page">‹ Prev</button><span class="num">Page ${p.page} of ${p.pages}</span><button type="button" class="btn sm" data-page="${p.page + 1}" ${p.page >= p.pages ? "disabled" : ""} aria-label="Next page">Next ›</button></span></div>`;
    }

    async function load(): Promise<void> {
      const t = byName.get(state.table)!;
      markErd();
      renderInfo(t);
      replaceParams("database", { t: state.table, p: state.page > 1 ? String(state.page) : undefined, n: state.size !== 25 ? String(state.size) : undefined, sort: state.sort, desc: state.desc ? "1" : undefined, fc: state.filter?.column, fv: state.filter?.value });
      abort?.abort();
      abort = new AbortController();
      grid.classList.add("loading");
      try {
        last = await dbPage(api, state.table, { page: state.page, size: state.size, sort: state.sort, desc: state.desc, column: state.filter?.column, value: state.filter?.value }, abort.signal);
        state.page = last.page;
        renderGrid(last, t);
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        grid.innerHTML = `<div class="callout error"><b>Could not load ${esc(state.table)}</b><p>${esc(friendlyError(err, "the database"))}</p></div>`;
      } finally {
        grid.classList.remove("loading");
      }
    }

    function open(next: Partial<State>): void {
      const changingTable = next.table !== undefined && next.table !== state.table;
      Object.assign(state, changingTable ? { page: 1, sort: undefined, desc: false } : {}, next);
      void load();
    }

    body.addEventListener("click", async (e) => {
      const t = e.target as HTMLElement;
      const tab = t.closest<HTMLElement>(".ex-tab")?.dataset["table"];
      if (tab) return open({ table: tab, filter: null });
      const to = t.closest<HTMLElement>("[data-open]")?.dataset["open"];
      if (to) return open({ table: to, filter: null });
      const fk = t.closest<HTMLElement>("[data-fk]");
      if (fk) return open({ table: fk.dataset["fk"]!, filter: { column: fk.dataset["col"]!, value: fk.dataset["val"]! }, page: 1, sort: undefined, desc: false });
      if (t.closest("[data-clear]")) return open({ filter: null, page: 1 });
      const sort = t.closest<HTMLElement>("[data-sort]")?.dataset["sort"];
      if (sort) return open({ sort, desc: state.sort === sort ? !state.desc : false, page: 1 });
      const pg = t.closest<HTMLElement>("[data-page]")?.dataset["page"];
      if (pg) return open({ page: Number(pg) });
      if (t.closest("[data-map]")) {
        const btn = t.closest<HTMLButtonElement>("[data-map]")!;
        btn.disabled = true;
        try {
          const p = await dbPage(api, state.table, { page: state.page, size: state.size, sort: state.sort, desc: state.desc, column: state.filter?.column, value: state.filter?.value, geojson: true });
          if (p.geojson?.features.length) {
            ctx.queryResult = { label: `${state.table}, page ${p.page}`, geojson: p.geojson };
            location.hash = hrefFor("map");
          }
        } finally {
          btn.disabled = false;
        }
      }
    });
    body.addEventListener("change", (e) => {
      const s = e.target as HTMLSelectElement;
      if (s.id === "ex-size") open({ size: Number(s.value), page: 1 });
    });
    void load();
  }

  return {
    el,
    show(route: Route) {
      if (started) return;
      started = true;
      pending = route;
      void ctx.api.then((api) => {
        if (api.kind !== "ready") return offline(api.reason === "static-host" ? "This page is served as static files." : "The live backend did not answer its health check.");
        void start(api);
      });
    },
  };
}
