import type { ApiState, ReadyApi } from "../api";
import { ApiError, apiUrl, compareDates, compareStart, compareStatus, friendlyError } from "../api";
import { getJson, indexed } from "../data";
import { withUnlock } from "../unlock";
import { CHEVRON, CLOSE, LIVE_URL, esc, q } from "../domain";
import type { ChangeCollection, CompareDate, CompareResult, Dataset } from "../types";

const PROVIDED_BEFORE = "provided-before";
const PROVIDED_AFTER = "provided-after";
const POLL_MS = 1500;
const MAX_WAIT_MS = 12 * 60_000;

interface Options {
  api: Promise<ApiState>;
  provided: Dataset;
  onDataset: (ds: Dataset) => void;
}

type Phase = "choose" | "running" | "failed";

/**
 * "Compare other dates": a chip showing the active pair and a popover that lists clear dates from the API, runs
 * POST /api/compare, polls the job and hands the finished dataset to the map. Offline it explains where to go.
 */
export class DatePicker {
  readonly root: HTMLElement;
  private readonly pop: HTMLElement;
  private readonly trigger: HTMLButtonElement;
  private current: Dataset;
  private api: ApiState | null = null;
  private dates: CompareDate[] | null = null;
  private before: string = PROVIDED_BEFORE;
  private after: string = PROVIDED_AFTER;
  private phase: Phase = "choose";
  private message = "";
  private runToken = 0;
  private loadError: string | null = null;

  constructor(private readonly opts: Options) {
    this.current = opts.provided;
    this.root = document.createElement("div");
    this.root.className = "cmp";
    this.root.innerHTML = `<div class="cmp-chip glass"><div class="cmp-text"><span class="cmp-k">Comparing</span><span class="cmp-v num"></span><span class="cmp-n"></span></div><button type="button" class="btn sm cmp-btn" aria-haspopup="dialog" aria-expanded="false">Other dates ${CHEVRON}</button></div><div class="cmp-pop glass" role="dialog" aria-label="Compare other dates" hidden></div>`;
    this.pop = q(this.root, ".cmp-pop");
    this.trigger = q(this.root, ".cmp-btn");
    this.trigger.addEventListener("click", () => (this.pop.hidden ? this.open() : this.close()));
    this.pop.addEventListener("click", (e) => this.onClick(e));
    document.addEventListener("pointerdown", (e) => {
      if (!this.pop.hidden && !this.root.contains(e.target as Node) && !(e.target as Element).closest?.("dialog.unlock")) this.close();
    });
    this.root.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !this.pop.hidden) {
        this.close();
        this.trigger.focus();
      }
    });
    void opts.api.then((a) => (this.api = a));
    this.renderChip();
  }

  /** Link target for `#/map?before=..&after=..`: open the picker pre-selected and run it. */
  async runFromLink(before: string, after: string): Promise<void> {
    this.api = await this.opts.api;
    if (this.api.kind !== "ready") return;
    this.before = before;
    this.after = after;
    this.open();
    await this.run();
  }

  /** Back to the provided pair (what "Use provided pair" does). */
  reset(): void {
    this.before = PROVIDED_BEFORE;
    this.after = PROVIDED_AFTER;
    this.opts.onDataset(this.opts.provided);
  }

  setDataset(ds: Dataset): void {
    this.current = ds;
    this.renderChip();
  }

  private renderChip(): void {
    const ds = this.current;
    q(this.root, ".cmp-v").textContent = `${ds.dates.before} → ${ds.dates.after}`;
    q(this.root, ".cmp-n").textContent = ds.nominal ? "nominal dates (each a two-scene mosaic)" : "true acquisition dates";
  }

  /* ---------- popover ---------- */

  private open(): void {
    this.pop.hidden = false;
    this.trigger.setAttribute("aria-expanded", "true");
    this.render();
    if (this.api?.kind === "ready" && !this.dates && !this.loadError) void this.loadDates(this.api);
  }

  private close(): void {
    this.pop.hidden = true;
    this.trigger.setAttribute("aria-expanded", "false");
  }

  private async loadDates(api: ReadyApi): Promise<void> {
    try {
      this.dates = (await compareDates(api)).sort((a, b) => a.date.localeCompare(b.date));
      this.loadError = null;
    } catch (err) {
      this.loadError = friendlyError(err, "the date service");
    }
    this.render();
  }

  private sortKey(v: string): string {
    return v === PROVIDED_BEFORE ? this.opts.provided.dates.before : v === PROVIDED_AFTER ? this.opts.provided.dates.after : v;
  }

  private valid(): boolean {
    return this.sortKey(this.before) < this.sortKey(this.after);
  }

  private render(): void {
    if (this.pop.hidden) return;
    const head = `<div class="pop-head"><b>Compare other dates</b><button type="button" class="btn ghost icon sm" data-act="close" aria-label="Close">${CLOSE}</button></div>`;
    if (!this.api || this.api.kind !== "ready") {
      const reason = this.api?.kind === "disabled" && this.api.reason === "static-host" ? "This page is served as static files, so there is no backend to compute a new pair." : "The live backend did not answer, so other dates can't be computed right now.";
      this.pop.innerHTML = `${head}<div class="pop-body"><p>${reason}</p><p class="muted">The provided pair stays fully explorable here. Picking two other clear dates and the SQL console run on the live site.</p><a class="btn primary" href="${LIVE_URL}/#/map" target="_blank" rel="noopener noreferrer">Open s2change.dzaky.ai.id</a></div>`;
      return;
    }
    if (this.phase === "running") {
      this.pop.innerHTML = `${head}<div class="pop-body"><p><b class="num">${esc(this.label(this.before))} → ${esc(this.label(this.after))}</b></p><div class="progress" role="progressbar" aria-label="Computing change"><i></i></div><p class="muted" role="status">${esc(this.message || "Starting…")}</p><p class="muted">Results are cached per pair; a new pair takes about a minute.</p><button type="button" class="btn sm" data-act="cancel">Stop waiting</button></div>`;
      return;
    }
    if (this.phase === "failed") {
      this.pop.innerHTML = `${head}<div class="pop-body"><div class="callout error"><b>Could not compare these dates</b><p>${esc(this.message)}</p></div><button type="button" class="btn" data-act="back">Choose again</button></div>`;
      return;
    }
    if (this.loadError) {
      this.pop.innerHTML = `${head}<div class="pop-body"><div class="callout error"><b>Could not list dates</b><p>${esc(this.loadError)}</p></div><button type="button" class="btn" data-act="retry">Try again</button></div>`;
      return;
    }
    if (!this.dates) {
      this.pop.innerHTML = `${head}<div class="pop-body"><p class="muted"><span class="spinner"></span> Loading the date catalogue…</p></div>`;
      return;
    }
    const list = (kind: "before" | "after") => {
      const sel = kind === "before" ? this.before : this.after;
      const provided = kind === "before" ? PROVIDED_BEFORE : PROVIDED_AFTER;
      const rows = [{ value: provided, text: `Nominal ${this.opts.provided.dates[kind]}`, meta: "provided mosaic" }, ...this.dates!.map((d) => ({ value: d.date, text: d.date, meta: `${d.aoi_cloud < 0.005 ? "<1" : Math.round(d.aoi_cloud * 100)}% cloud${kind === "after" && d.ready && this.before === PROVIDED_BEFORE ? " · ready" : ""}` }))];
      const other = this.sortKey(kind === "before" ? this.after : this.before);
      return rows
        .map((r) => {
          const bad = kind === "before" ? this.sortKey(r.value) >= other : this.sortKey(r.value) <= other;
          return `<button type="button" role="radio" class="dt" data-kind="${kind}" data-v="${esc(r.value)}" aria-checked="${r.value === sel}" ${bad ? "disabled" : ""}><span class="num">${esc(r.text)}</span><small>${esc(r.meta)}</small></button>`;
        })
        .join("");
    };
    this.pop.innerHTML = `${head}<div class="pop-body"><p class="muted">One clear Sentinel-2 scene per month since the provided pair, downloaded and cloud-checked in advance. Pairs from the provided before date are ready instantly; other pairs take about a minute.</p><div class="dt-cols"><div><h4>Before</h4><div class="dt-list" role="radiogroup" aria-label="Before date">${list("before")}</div></div><div><h4>After</h4><div class="dt-list" role="radiogroup" aria-label="After date">${list("after")}</div></div></div><div class="pop-foot"><button type="button" class="btn ghost sm" data-act="reset">Use provided pair</button><button type="button" class="btn primary" data-act="run" ${this.valid() ? "" : "disabled"}>Compare</button></div></div>`;
    for (const list of this.pop.querySelectorAll<HTMLElement>(".dt-list")) {
      list.querySelector<HTMLElement>('[aria-checked="true"]')?.scrollIntoView({ block: "nearest" });
    }
  }

  private label(v: string): string {
    return v === PROVIDED_BEFORE ? `${this.opts.provided.dates.before} (nominal)` : v === PROVIDED_AFTER ? `${this.opts.provided.dates.after} (nominal)` : v;
  }

  private onClick(e: MouseEvent): void {
    const t = e.target as HTMLElement;
    const dt = t.closest<HTMLElement>(".dt");
    if (dt && !(dt as HTMLButtonElement).disabled) {
      if (dt.dataset["kind"] === "before") this.before = dt.dataset["v"] ?? this.before;
      else this.after = dt.dataset["v"] ?? this.after;
      const scrolls = [...this.pop.querySelectorAll<HTMLElement>(".dt-list")].map((l) => l.scrollTop);
      this.render();
      this.pop.querySelectorAll<HTMLElement>(".dt-list").forEach((l, i) => (l.scrollTop = scrolls[i] ?? 0));
      return;
    }
    const act = t.closest<HTMLElement>("[data-act]")?.dataset["act"];
    if (act === "close") this.close();
    else if (act === "run") void this.run();
    else if (act === "cancel") {
      this.runToken++;
      this.phase = "choose";
      this.render();
    } else if (act === "back") {
      this.phase = "choose";
      this.render();
    } else if (act === "retry" && this.api?.kind === "ready") {
      this.loadError = null;
      this.render();
      void this.loadDates(this.api);
    } else if (act === "reset") {
      this.reset();
      this.close();
    }
  }

  /* ---------- compare job ---------- */

  private async run(): Promise<void> {
    const api = this.api;
    if (api?.kind !== "ready" || !this.valid()) return;
    if (this.before === PROVIDED_BEFORE && this.after === PROVIDED_AFTER) {
      this.opts.onDataset(this.opts.provided);
      this.close();
      return;
    }
    const token = ++this.runToken;
    this.phase = "running";
    this.message = "Submitting the job";
    this.render();
    const started = Date.now();
    try {
      const [before, after] = [this.before, this.after];
      let job = await withUnlock(this.opts.api, () => compareStart(api, before, after));
      const id = job.job_id;
      while (job.status !== "done") {
        if (token !== this.runToken) return;
        if (job.status === "error") throw new Error(job.message || "The job failed");
        if (Date.now() - started > MAX_WAIT_MS) throw new Error("This is taking longer than expected. Try again in a minute; finished pairs are cached.");
        this.message = `${job.message || (job.status === "queued" ? "Queued" : "Running")} · ${Math.round((Date.now() - started) / 1000)} s`;
        this.render();
        await new Promise((r) => setTimeout(r, POLL_MS));
        if (!id) throw new Error("The server did not return a job id");
        job = await compareStatus(api, id);
      }
      if (token !== this.runToken) return;
      if (!job.result) throw new Error("The job finished without a result");
      const ds = await this.toDataset(api, job.result, this.label(this.before), this.label(this.after), this.before === PROVIDED_BEFORE || this.after === PROVIDED_AFTER);
      if (token !== this.runToken) return;
      this.phase = "choose";
      this.opts.onDataset(ds);
      this.close();
    } catch (err) {
      if (token !== this.runToken) return;
      this.phase = "failed";
      this.message = err instanceof ApiError ? friendlyError(err, "the compare service") : err instanceof Error ? err.message : "The comparison failed";
      this.render();
    }
  }

  private async toDataset(api: ReadyApi, r: CompareResult, beforeLabel: string, afterLabel: string, nominal: boolean): Promise<Dataset> {
    const changes = indexed(await getJson<ChangeCollection>(apiUrl(api, r.changes_url)));
    const abs = (o: { url: string; bounds: CompareResult["overlays"]["before"]["bounds"] }) => ({ url: apiUrl(api, o.url), bounds: o.bounds });
    const before = abs(r.overlays.before);
    const after = abs(r.overlays.after);
    return {
      id: r.pair,
      dates: { before: beforeLabel.replace(" (nominal)", ""), after: afterLabel.replace(" (nominal)", "") },
      nominal,
      rasters: { before, after_normalized: after, change_intensity: abs(r.overlays.intensity) },
      changes,
    };
  }
}
