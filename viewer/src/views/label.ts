import "../styles/label.css";
import type { AppContext, View } from "../app";
import { dataUrl, getJson } from "../data";
import { download, esc, fmtInt, q, qa } from "../domain";

type Label = "change" | "no_change" | "unsure";
interface Point {
  id: string;
  lon: number;
  lat: number;
}
interface Sample {
  chip_m: number;
  chip_px: number;
  note: string;
  points: Point[];
}

const LABELS_KEY = "s2cd.labels.v1";
const POS_KEY = "s2cd.labels.pos.v1";
const CHOICES: { key: Label; text: string; hot: string }[] = [
  { key: "change", text: "Change", hot: "1" },
  { key: "no_change", text: "No change", hot: "2" },
  { key: "unsure", text: "Unsure", hot: "3" },
];

/** Deterministic shuffle: strata are encoded in the ids, so the order must not follow them. */
function shuffled<T>(items: T[], seed = 20230902): T[] {
  let s = seed >>> 0;
  const rnd = () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const a = [...items];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rnd() * (i + 1));
    [a[i], a[j]] = [a[j] as T, a[i] as T];
  }
  return a;
}

function readLabels(): Record<string, Label> {
  try {
    const raw = JSON.parse(localStorage.getItem(LABELS_KEY) ?? "{}") as Record<string, unknown>;
    return Object.fromEntries(Object.entries(raw).filter(([, v]) => v === "change" || v === "no_change" || v === "unsure")) as Record<string, Label>;
  } catch {
    return {};
  }
}

export async function createLabel(_ctx: AppContext): Promise<View> {
  const el = document.createElement("div");
  el.className = "view";
  el.innerHTML = `<div class="page lb"><div class="muted"><span class="spinner"></span> Loading the sample…</div></div>`;
  const page = q(el, ".page");
  let started = false;
  let active = false;

  async function start(): Promise<void> {
    let sample: Sample;
    try {
      sample = await getJson<Sample>(dataUrl("validation/sample.json"));
    } catch (err) {
      page.innerHTML = `<div class="callout error"><b>Could not load the validation sample</b><p>${esc(err instanceof Error ? err.message : String(err))}. It is written by <code>python -m s2cd.validation sample</code>.</p></div>`;
      return;
    }
    const points = shuffled(sample.points);
    const total = points.length;
    if (!total) {
      page.innerHTML = `<div class="callout"><b>The sample is empty</b><p>Regenerate it with <code>python -m s2cd.validation sample</code>.</p></div>`;
      return;
    }
    const labels = readLabels();
    let idx = Math.min(Number(localStorage.getItem(POS_KEY)) || 0, total - 1);
    if (!localStorage.getItem(POS_KEY)) idx = Math.max(0, points.findIndex((p) => !labels[p.id]));
    const chipUrl = (p: Point, side: "before" | "after") => dataUrl(`validation/chips/${p.id}_${side}.png`).href;

    page.innerHTML = `
      <div class="page-head"><div class="eyebrow">Accuracy assessment</div><h1>Did the land change at the crosshair?</h1><p>${esc(sample.note)} Points are shown in random order and the sampling strata are hidden, so your labels stay blind. Labels are saved in this browser only.</p></div>
      <div class="lb-progress"><div class="progress-track" role="progressbar" aria-label="Labelled points" aria-valuemin="0" aria-valuemax="${total}"><i></i></div><span class="num lb-count"></span></div>
      <div class="lb-card card">
        <div class="lb-top"><b class="num lb-pos"></b><span class="muted lb-geo num"></span></div>
        <div class="lb-body"><div class="lb-media">
        <div class="chips2">
          <figure><div class="chipbox"><img class="chip-img" id="img-before" width="${sample.chip_px}" height="${sample.chip_px}" alt="Sentinel-2 true-colour chip before the change"><span class="reticle" aria-hidden="true"></span></div><figcaption>Before <small>nominal 2023-08-12</small></figcaption></figure>
          <figure><div class="chipbox"><img class="chip-img" id="img-after" width="${sample.chip_px}" height="${sample.chip_px}" alt="Sentinel-2 true-colour chip after the change"><span class="reticle" aria-hidden="true"></span></div><figcaption>After <small>nominal 2023-09-02</small></figcaption></figure>
        </div>
        <div class="scale" aria-hidden="true"><i></i><span class="num">${sample.chip_m} m wide · each chip is a ${sample.chip_px}-pixel window, 10 m per pixel</span></div>
        </div><div class="lb-side">
        <div class="choices" role="group" aria-label="Label this point">${CHOICES.map((c) => `<button type="button" class="choice" data-k="${c.key}" aria-pressed="false"><span>${c.text}</span><kbd>${c.hot}</kbd></button>`).join("")}</div>
        <div class="lb-nav"><button type="button" class="btn" data-nav="-1"><kbd>←</kbd> Previous</button><button type="button" class="btn" data-nav="1">Next <kbd>→</kbd></button></div>
        <div class="tally num"></div>
        <div class="lb-actions"><button type="button" class="btn primary" id="dl-labels">Download labels.json</button><button type="button" class="btn ghost" id="reset">Reset labels</button></div>
        <p class="note done" hidden>All ${total} labelled. Save the file as <code>docs/web/validation/labels.json</code>, then run <code>python -m s2cd.validation assess</code>.</p>
        </div></div>
      </div>
`;

    const imgB = q<HTMLImageElement>(page, "#img-before");
    const imgA = q<HTMLImageElement>(page, "#img-after");
    const count = () => Object.keys(labels).filter((k) => points.some((p) => p.id === k)).length;

    const paint = () => {
      const p = points[idx] as Point;
      imgB.src = chipUrl(p, "before");
      imgA.src = chipUrl(p, "after");
      for (const n of [idx + 1, idx + 2]) {
        const nx = points[n];
        if (nx) for (const s of ["before", "after"] as const) new Image().src = chipUrl(nx, s);
      }
      q(page, ".lb-pos").textContent = `Point ${idx + 1} of ${total}`;
      q(page, ".lb-geo").textContent = `${p.lat.toFixed(4)}°, ${p.lon.toFixed(4)}°`;
      for (const b of qa<HTMLButtonElement>(page, ".choice")) b.setAttribute("aria-pressed", String(labels[p.id] === b.dataset["k"]));
      const n = count();
      q(page, ".progress-track").setAttribute("aria-valuenow", String(n));
      q(page, ".progress-track i").style.width = `${(n / total) * 100}%`;
      q(page, ".lb-count").textContent = `${fmtInt(n)} of ${fmtInt(total)} labelled`;
      const tally = (k: Label) => Object.values(labels).filter((v) => v === k).length;
      q(page, ".tally").innerHTML = `<span><i class="dot c"></i>${tally("change")} change</span><span><i class="dot n"></i>${tally("no_change")} no change</span><span><i class="dot u"></i>${tally("unsure")} unsure</span>`;
      q<HTMLButtonElement>(page, "#dl-labels").disabled = n === 0;
      q(page, ".done").hidden = n < total;
      localStorage.setItem(POS_KEY, String(idx));
    };

    const go = (to: number) => {
      idx = Math.max(0, Math.min(total - 1, to));
      paint();
    };
    const choose = (k: Label) => {
      const p = points[idx] as Point;
      labels[p.id] = k;
      localStorage.setItem(LABELS_KEY, JSON.stringify(labels));
      if (idx < total - 1) go(idx + 1);
      else paint();
    };

    page.addEventListener("click", (e) => {
      const t = e.target as HTMLElement;
      const k = t.closest<HTMLElement>("[data-k]")?.dataset["k"] as Label | undefined;
      if (k) return choose(k);
      const nav = t.closest<HTMLElement>("[data-nav]")?.dataset["nav"];
      if (nav) go(idx + Number(nav));
    });
    q(page, "#dl-labels").addEventListener("click", () => {
      const out = Object.fromEntries(sample.points.filter((p) => labels[p.id]).map((p) => [p.id, labels[p.id]]));
      download("labels.json", JSON.stringify(out, null, 1) + "\n", "application/json");
    });
    const reset = q<HTMLButtonElement>(page, "#reset");
    let armed: number | undefined;
    reset.addEventListener("click", () => {
      if (!armed) {
        reset.textContent = "Click again to erase all labels";
        armed = window.setTimeout(() => {
          armed = undefined;
          reset.textContent = "Reset labels";
        }, 3500);
        return;
      }
      clearTimeout(armed);
      armed = undefined;
      reset.textContent = "Reset labels";
      for (const k of Object.keys(labels)) delete labels[k];
      localStorage.removeItem(LABELS_KEY);
      go(0);
    });

    document.addEventListener("keydown", (e) => {
      if (!active || e.metaKey || e.ctrlKey || e.altKey) return;
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const hot = CHOICES.find((c) => c.hot === e.key);
      if (hot) choose(hot.key);
      else if (e.key === "ArrowRight") go(idx + 1);
      else if (e.key === "ArrowLeft") go(idx - 1);
      else return;
      e.preventDefault();
    });
    paint();
  }

  return {
    el,
    show() {
      active = true;
      if (started) return;
      started = true;
      void start();
    },
    hide() {
      active = false;
    },
  };
}
