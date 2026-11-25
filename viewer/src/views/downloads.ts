import "../styles/report.css"; // .prose, for the document preview
import "../styles/downloads.css";
import type { AppContext, View } from "../app";
import { dataUrl, getJson } from "../data";
import { BLOB_URL, RAW_URL, esc, fmtInt, prefersReducedMotion, q } from "../domain";
import { renderRich } from "../rich";
import { hrefFor } from "../router";

/** docs/web/downloads.json, written by `python -m s2cd.site` from the files themselves (sizes, previews, samples). */
type Kind = "rgb" | "magnitude" | "binary" | "vector" | "sqlite" | "markdown" | "report";
interface Item {
  path: string;
  title: string;
  desc: string;
  size: string;
  kind: Kind;
  image?: string;
  facts?: [string, string][];
  sample?: { columns: string[]; rows: string[][] };
  tables?: { name: string; rows: number; columns: string[] }[];
  document?: string;
}
interface Catalogue {
  groups: { title: string; blurb: string; items: Item[] }[];
}

const FORMAT: Record<Kind, string> = { rgb: "GeoTIFF", magnitude: "GeoTIFF", binary: "GeoTIFF", vector: "GeoJSON", sqlite: "SpatiaLite", markdown: "Markdown", report: "Markdown" };
const DL = `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 2v8m-3-3 3 3 3-3M3 13h10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

function tablesHtml(tables: NonNullable<Item["tables"]>, full: boolean): string {
  return `<ul class="dl-tables">${tables.map((t) => `<li><code>${esc(t.name)}</code><span class="num">${fmtInt(t.rows)} rows · ${t.columns.length} columns</span>${full ? `<small>${t.columns.map(esc).join(", ")}</small>` : ""}</li>`).join("")}</ul>`;
}

/** The preview inside a card: the rendered image, the table list, or the start of the document. */
function thumb(i: Item): string {
  if (i.image) return `<img src="${dataUrl(i.image).href}" alt="" loading="lazy" decoding="async">`;
  if (i.tables) return tablesHtml(i.tables, false);
  return `<div class="dl-doc" aria-hidden="true"><b>${esc(i.title)}</b><i></i><i></i><i class="w2"></i><i></i><i class="w3"></i></div>`;
}

function action(i: Item): string {
  if (i.kind === "report") return `<a class="btn sm" href="${hrefFor("report")}">Open the Report tab</a>`;
  if (i.kind === "markdown") return `<a class="btn sm" href="${esc(BLOB_URL + i.path)}" target="_blank" rel="noopener noreferrer">View on GitHub</a>`;
  return `<a class="btn sm primary" href="${esc(RAW_URL + i.path)}" download>${DL} Download</a>`;
}

function card(i: Item, key: string): string {
  const previewable = i.kind !== "report";
  const media = `<span class="dl-media${i.image ? " img" : ""}">${thumb(i)}${previewable ? '<span class="dl-zoom">Preview</span>' : ""}</span>`;
  return `<article class="card dl-card">
    ${previewable ? `<button type="button" class="dl-open" data-key="${key}" aria-label="Preview ${esc(i.title)}">${media}</button>` : `<a class="dl-open" href="${hrefFor("report")}" aria-label="Open the Report tab">${media}</a>`}
    <div class="dl-top"><h3 class="mono">${esc(i.title)}</h3><span class="pill">${FORMAT[i.kind]}</span></div>
    <p>${esc(i.desc)}</p>
    <div class="dl-foot"><span class="muted num">${esc(i.size)}</span>${action(i)}</div>
  </article>`;
}

async function previewBody(i: Item): Promise<HTMLElement> {
  const box = document.createElement("div");
  box.className = "dl-pv-body";
  const facts = i.facts?.length ? `<dl class="dl-facts">${i.facts.map(([k, v]) => `<div${v.length > 60 ? ' class="wide"' : ""}><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>` : "";
  const sample = i.sample?.rows.length
    ? `<h3>First rows</h3><div class="table-wrap"><table><thead><tr>${i.sample.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${i.sample.rows.map((r) => `<tr>${r.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
    : "";
  box.innerHTML = `${i.image ? `<img class="dl-pv-img" src="${dataUrl(i.image).href}" alt="Preview of ${esc(i.title)}">` : ""}${i.tables ? tablesHtml(i.tables, true) : ""}${facts}${sample}`;
  if (i.document) {
    const res = await fetch(dataUrl(i.document));
    const doc = res.ok ? await renderRich(await res.text(), { imageBase: dataUrl(i.document) }) : Object.assign(document.createElement("p"), { textContent: `Could not load the document (HTTP ${res.status}).` });
    doc.classList.add("prose");
    box.append(doc);
  }
  return box;
}

/** Images fade in once decoded instead of popping in line by line. */
function fadeInImages(root: ParentNode): void {
  for (const img of root.querySelectorAll<HTMLImageElement>(".dl-media img, .dl-pv-img")) {
    if (img.complete && img.naturalWidth) img.classList.add("ready");
    else img.addEventListener("load", () => img.classList.add("ready"), { once: true });
  }
}

function openPreview(i: Item): void {
  const dlg = document.createElement("dialog");
  dlg.className = "dl-pv";
  dlg.setAttribute("aria-labelledby", "dl-pv-title");
  dlg.innerHTML = `<header><div><h2 id="dl-pv-title" class="mono">${esc(i.title)}</h2><span class="muted">${FORMAT[i.kind]} · ${esc(i.size)}</span></div><div class="dl-pv-act">${action(i)}<button type="button" class="btn sm ghost" data-close aria-label="Close">Close</button></div></header><div class="dl-pv-scroll"><p class="muted"><span class="spinner"></span> Loading…</p></div>`;
  const scroll = q(dlg, ".dl-pv-scroll");
  const close = () => {
    if (prefersReducedMotion() || dlg.classList.contains("closing")) return dlg.close();
    dlg.classList.add("closing");
    setTimeout(() => dlg.close(), 120);
  };
  dlg.addEventListener("close", () => dlg.remove());
  dlg.addEventListener("click", (e) => {
    if ((e.target as HTMLElement).closest("[data-close]") || e.target === dlg) close();
  });
  document.body.append(dlg);
  dlg.showModal();
  previewBody(i).then(
    (b) => (scroll.replaceChildren(b), fadeInImages(b)),
    (err: unknown) => (scroll.innerHTML = `<div class="callout error"><b>Preview failed</b><p>${esc(err instanceof Error ? err.message : String(err))}</p></div>`),
  );
}

export async function createDownloads(_ctx: AppContext): Promise<View> {
  const el = document.createElement("div");
  el.className = "view";
  el.innerHTML = `<div class="page dl">
    <div class="page-head"><div class="eyebrow">Downloads</div><h1>Take the data with you</h1><p>Everything the map and the database are built from. Each card previews the file; downloads come from the GitHub repository, so they are the same bytes the pipeline wrote.</p></div>
    <div id="dl-body"><div class="dl-skel" role="status"><span class="sr-only">Loading the file list…</span>${'<div class="card"><i class="skel s-img"></i><i class="skel s-t"></i><i class="skel s-l"></i></div>'.repeat(6)}</div></div></div>`;
  const body = q(el, "#dl-body");
  const items = new Map<string, Item>();
  let loaded = false;

  async function load(): Promise<void> {
    let cat: Catalogue;
    try {
      cat = await getJson<Catalogue>(dataUrl("downloads.json"));
    } catch (err) {
      body.innerHTML = `<div class="callout error"><b>Could not load the file list</b><p>${esc(err instanceof Error ? err.message : String(err))}. It is exported by <code>python -m s2cd.site</code>.</p></div>`;
      return;
    }
    body.innerHTML = cat.groups
      .map(
        (g, gi) =>
          `<section><h2>${esc(g.title)}</h2>${g.blurb ? `<p class="blurb">${esc(g.blurb)}</p>` : ""}<div class="dl-grid">${g.items
            .map((i, ii) => {
              const key = `${gi}.${ii}`;
              items.set(key, i);
              return card(i, key);
            })
            .join("")}</div></section>`,
      )
      .join("");
    fadeInImages(body);
  }

  el.addEventListener("click", (e) => {
    const key = (e.target as HTMLElement).closest<HTMLElement>("button.dl-open")?.dataset["key"];
    const item = key ? items.get(key) : undefined;
    if (item) openPreview(item);
  });

  return {
    el,
    show() {
      if (loaded) return;
      loaded = true;
      void load();
    },
  };
}
