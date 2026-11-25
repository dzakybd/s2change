import "../styles/report.css";
import type { AppContext, View } from "../app";
import { dataUrl } from "../data";
import { BLOB_URL, EXTERNAL, esc, q, prefersReducedMotion } from "../domain";
import { renderRich } from "../rich";
import { hrefFor } from "../router";
import type { Route } from "../router";

const ICON_DL = `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 2v8m-3-3 3 3 3-3M3 13h10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

export async function createReport(_ctx: AppContext): Promise<View> {
  const el = document.createElement("div");
  el.className = "view";
  el.innerHTML = `<div class="page rp"><div class="skel-doc" role="status"><span class="sr-only">Loading the report…</span><i class="skel s-title"></i><i class="skel s-p"></i><i class="skel s-p"></i><i class="skel s-p w2"></i></div></div>`;
  const page = q(el, ".page");
  let loaded: Promise<void> | null = null;
  let spy: IntersectionObserver | null = null;

  async function load(): Promise<void> {
    const url = dataUrl("report/report.md");
    let md: string;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      md = await res.text();
    } catch (err) {
      page.innerHTML = `<div class="callout error"><b>Could not load the report</b><p>${esc(err instanceof Error ? err.message : String(err))}. It is exported by <code>python -m s2cd.site</code>. <a href="${BLOB_URL}report.md" target="_blank" rel="noopener noreferrer">Read it on GitHub</a>.</p></div>`;
      return;
    }
    const doc = await renderRich(md, { imageBase: new URL("./", url), figures: true });
    doc.className = "prose";
    const heads = [...doc.querySelectorAll<HTMLElement>("h2, h3")];
    const toc = heads.map((h) => `<li class="${h.tagName === "H3" ? "sub" : ""}"><a href="${hrefFor("report", { s: h.id })}" data-id="${esc(h.id)}">${esc(h.textContent ?? "")}</a></li>`).join("");
    const words = (doc.textContent ?? "").split(/\s+/).length;
    page.innerHTML = `<div class="rp-grid"><nav class="toc" aria-label="Contents"><details open><summary>Contents</summary><ol>${toc}</ol></details></nav><article class="rp-article"><div class="rp-actions" role="group" aria-label="Download the report"><span class="muted">Take the report with you</span><a class="btn sm primary" href="${dataUrl("report/s2change-report.pdf").href}" download="s2change-report.pdf">${ICON_DL} PDF</a><a class="btn sm" href="${dataUrl("report/s2change-report.md").href}" download="s2change-report.md">${ICON_DL} Markdown</a></div><div class="rp-meta"><span class="muted">${Math.max(1, Math.round(words / 220))} min read</span><a class="chip" href="${BLOB_URL}report.md" target="_blank" rel="noopener noreferrer">Source on GitHub${EXTERNAL}</a></div></article></div>`;
    q(page, ".rp-article").append(doc);
    if (window.matchMedia("(max-width: 900px)").matches) q(page, ".toc details").removeAttribute("open");

    spy = new IntersectionObserver(
      (entries) => {
        const top = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (!top) return;
        for (const a of page.querySelectorAll(".toc a")) a.classList.toggle("on", (a as HTMLElement).dataset["id"] === top.target.id);
      },
      { root: el, rootMargin: "-56px 0px -70% 0px" },
    );
    for (const h of heads) spy.observe(h);
  }

  function scrollToSection(id: string | null): void {
    if (!id) return;
    const target = page.querySelector<HTMLElement>(`[id="${CSS.escape(id)}"]`);
    target?.scrollIntoView({ block: "start", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }

  return {
    el,
    show(route: Route) {
      loaded ??= load();
      void loaded.then(() => scrollToSection(route.params.get("s")));
    },
  };
}
