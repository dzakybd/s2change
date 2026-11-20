import "../styles/database.css";
import type { AppContext, View } from "../app";
import { dbExamples, dbQuery, dbSchema, friendlyError } from "../api";
import { BLOB_URL, LIVE_URL, download, esc, fmtInt, q } from "../domain";
import { hrefFor } from "../router";
import type { DbExample, DbQueryResult, DbTable } from "../types";

const KEY = "s2cd.sql.v1";
const DEFAULT_SQL = "SELECT change_type, count(*) AS n_polygons,\n       round((sum(area_m2) / 10000.0)::numeric, 1) AS area_ha\nFROM change_features\nWHERE method = 'robust_cva'\nGROUP BY change_type\nORDER BY area_ha DESC;";

const isNumeric = (v: unknown) => typeof v === "number" || (typeof v === "string" && /^-?\d+(\.\d+)?(e[+-]?\d+)?$/i.test(v));

function cellText(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") {
    const s = JSON.stringify(v);
    return s.length > 80 ? `${s.slice(0, 77)}…` : s;
  }
  return String(v);
}

function csv(res: DbQueryResult): string {
  const cell = (v: unknown) => {
    const s = v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
    return /[",\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
  };
  return [res.columns.map(cell).join(","), ...res.rows.map((r) => r.map(cell).join(","))].join("\n");
}

export async function createDatabase(ctx: AppContext): Promise<View> {
  const el = document.createElement("div");
  el.className = "view";
  el.innerHTML = `<div class="page db">
    <div class="page-head"><div class="eyebrow">Database</div><h1>Query the results</h1><p>The change polygons and the facility register live in PostGIS. Run a single read-only SELECT (5 s timeout, 500 rows) and draw spatial results on the map.</p></div>
    <div id="db-body"></div></div>`;
  const body = q(el, "#db-body");
  let started = false;

  function offline(reason: string): void {
    const tables: [string, string][] = [
      ["change_runs", "One row per method run: parameters and summary as JSONB."],
      ["change_features", "Every change polygon with type, explanation, confidence and facility (EPSG:32735)."],
      ["facilities", "The facility register: outlines of pits, dumps, tailings, plant, camp, dam, town."],
      ["facility_changes", "Hectares changed per facility and the change mix."],
    ];
    body.innerHTML = `<div class="callout offline"><b>The database is live-only</b><p>${esc(reason)} The schema browser and SQL console talk to PostGIS through the API, which GitHub Pages cannot host.</p><p><a class="btn primary" href="${LIVE_URL}/#/database" target="_blank" rel="noopener noreferrer">Open s2change.dzaky.ai.id</a> <a class="btn" href="${BLOB_URL}sql/queries_postgis.sql" target="_blank" rel="noopener noreferrer">Read the example queries</a></p></div>
    <h2 class="off-h">What is in it</h2><ul class="off-tables">${tables.map(([t, d]) => `<li class="card"><code>${t}</code><span>${esc(d)}</span></li>`).join("")}</ul>`;
  }

  async function start(): Promise<void> {
    const api = await ctx.api;
    if (api.kind !== "ready") {
      offline(api.reason === "static-host" ? "This page is served as static files." : "The live backend did not answer its health check.");
      return;
    }
    body.innerHTML = `<div class="db-grid">
      <aside class="db-side">
        <h2>Schema</h2><div id="db-schema" class="schema"><span class="muted"><span class="spinner"></span> Loading…</span></div>
        <h2>Examples</h2><div id="db-ex" class="examples"></div>
      </aside>
      <section class="db-main">
        ${api.mock ? '<div class="callout mock"><b>Mock mode</b><p>Results are canned examples, not a live query.</p></div>' : ""}
        <div class="editor card">
          <label for="sql" class="sr-only">SQL</label>
          <textarea id="sql" spellcheck="false" autocapitalize="off" autocomplete="off" rows="9" aria-describedby="sql-hint"></textarea>
          <div class="editor-bar"><span id="sql-hint" class="muted"><span id="ex-desc"></span><kbd>⌘</kbd><kbd>↵</kbd> or <kbd>Ctrl</kbd><kbd>↵</kbd> to run</span><button type="button" class="btn primary" id="run">Run query</button></div>
        </div>
        <div id="result" aria-live="polite"></div>
      </section>
    </div>`;
    const sql = q<HTMLTextAreaElement>(body, "#sql");
    sql.value = localStorage.getItem(KEY) || DEFAULT_SQL;
    const result = q(body, "#result");
    result.innerHTML = `<p class="muted empty">Results appear here.</p>`;
    let last: DbQueryResult | null = null;
    let abort: AbortController | null = null;

    const run = async () => {
      const text = sql.value.trim();
      if (!text) return;
      localStorage.setItem(KEY, sql.value);
      abort?.abort();
      abort = new AbortController();
      const btn = q<HTMLButtonElement>(body, "#run");
      btn.disabled = true;
      result.innerHTML = `<p class="muted"><span class="spinner"></span> Running…</p>`;
      try {
        last = await dbQuery(api, text, abort.signal);
        render(last);
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        last = null;
        result.innerHTML = `<div class="callout error"><b>Query failed</b><p class="mono">${esc(friendlyError(err, "the database"))}</p></div>`;
      } finally {
        btn.disabled = false;
      }
    };

    const render = (r: DbQueryResult) => {
      const numeric = r.columns.map((_c, i) => r.rows.some((row) => row[i] != null) && r.rows.every((row) => row[i] == null || isNumeric(row[i])));
      const geo = r.geojson && r.geojson.features.length > 0 ? r.geojson : null;
      const head = r.columns.map((c, i) => `<th class="${numeric[i] ? "num" : ""}">${esc(c)}</th>`).join("");
      const rows = r.rows.map((row) => `<tr>${row.map((v, i) => `<td class="${numeric[i] ? "num" : ""}${v == null ? " nul" : ""}">${v == null ? "null" : esc(cellText(v))}</td>`).join("")}</tr>`).join("");
      result.innerHTML = `<div class="res-bar"><span class="num"><b>${fmtInt(r.rows.length)}</b> row${r.rows.length === 1 ? "" : "s"} · ${r.elapsed_ms} ms${r.truncated ? ' · <span class="warn">truncated at 500 rows</span>' : ""}</span><span class="res-actions">${geo ? '<button type="button" class="btn sm primary" data-act="map">Show on map</button>' : ""}<button type="button" class="btn sm" data-act="csv">CSV</button>${geo ? '<button type="button" class="btn sm" data-act="geojson">GeoJSON</button>' : ""}</span></div>
      ${r.rows.length ? `<div class="table-wrap res-table"><table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="muted empty">The query returned no rows.</p>'}`;
    };

    result.addEventListener("click", (e) => {
      const act = (e.target as HTMLElement).closest<HTMLElement>("[data-act]")?.dataset["act"];
      if (!act || !last) return;
      if (act === "csv") download("query-result.csv", csv(last), "text/csv");
      else if (act === "geojson" && last.geojson) download("query-result.geojson", JSON.stringify(last.geojson), "application/geo+json");
      else if (act === "map" && last.geojson) {
        ctx.queryResult = { label: "Query result", geojson: last.geojson };
        location.hash = hrefFor("map");
      }
    });
    q(body, "#run").addEventListener("click", () => void run());
    sql.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        void run();
      }
    });
    sql.addEventListener("input", () => (q(body, "#ex-desc").textContent = ""));

    const load = (text: string, desc = "") => {
      sql.value = text;
      q(body, "#ex-desc").textContent = desc ? `${desc} ` : "";
      sql.focus();
    };

    void Promise.all([dbSchema(api), dbExamples(api)]).then(
      ([tables, examples]) => {
        renderSchema(tables, load);
        renderExamples(examples, load, () => void run());
      },
      (err: unknown) => {
        q(body, "#db-schema").innerHTML = `<div class="callout error"><b>Could not load the schema</b><p>${esc(friendlyError(err, "the database"))}</p></div>`;
      },
    );
  }

  function renderSchema(tables: DbTable[], load: (sql: string, desc?: string) => void): void {
    q(body, "#db-schema").innerHTML = tables
      .map(
        (t) => `<details class="tbl"><summary><span class="tn">${esc(t.table)}</span><span class="tr num">${fmtInt(t.rows)} rows</span>${t.geometry ? `<span class="pill geo" title="Geometry column ${esc(t.geometry.column)}">${esc(t.geometry.type)} · ${t.geometry.srid}</span>` : ""}</summary>
        <ul>${t.columns.map((c) => `<li><span>${esc(c.name)}</span><small>${esc(c.type)}</small></li>`).join("")}</ul>
        <button type="button" class="btn sm" data-preview="${esc(t.table)}">Preview 20 rows</button></details>`,
      )
      .join("");
    q(body, "#db-schema").addEventListener("click", (e) => {
      const t = (e.target as HTMLElement).closest<HTMLElement>("[data-preview]")?.dataset["preview"];
      const table = tables.find((x) => x.table === t);
      if (!table) return;
      const cols = table.columns.filter((c) => c.name !== table.geometry?.column).map((c) => c.name);
      load(`SELECT ${cols.join(", ")}\nFROM ${table.table}\nLIMIT 20;`, `Preview of ${table.table}.`);
    });
  }

  function renderExamples(ex: DbExample[], load: (sql: string, desc?: string) => void, run: () => void): void {
    const host = q(body, "#db-ex");
    host.innerHTML = ex.map((e, i) => `<button type="button" class="chip" data-i="${i}" title="${esc(e.description)}">${esc(e.title)}</button>`).join("");
    host.addEventListener("click", (e) => {
      const i = Number((e.target as HTMLElement).closest<HTMLElement>("[data-i]")?.dataset["i"]);
      const item = ex[i];
      if (!item) return;
      load(item.sql, item.description);
      if ((e as MouseEvent).detail > 1) run();
    });
  }

  return {
    el,
    show() {
      if (started) return;
      started = true;
      void start();
    },
    hide() {},
  };
}
