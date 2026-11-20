/**
 * Markdown to sanitised DOM for the Report tab and the monitoring brief. marked and DOMPurify load on first use,
 * so they stay out of the entry bundle. Post-processing happens on the sanitised DOM: heading ids for the table of
 * contents, figures with numbered captions, scrollable tables and external links that open safely. ```mermaid fences
 * become SVG diagrams; the mermaid package is imported only when a document contains one.
 */
export interface RichOptions {
  /** Resolve relative image URLs against this (the report's own folder). */
  imageBase?: URL;
  figures?: boolean;
}

const slug = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);

export async function renderRich(markdown: string, opts: RichOptions = {}): Promise<HTMLElement> {
  const [{ marked }, { default: DOMPurify }] = await Promise.all([import("marked"), import("dompurify")]);
  const html = await marked.parse(markdown, { gfm: true });
  const root = document.createElement("div");
  root.innerHTML = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });

  const seen = new Set<string>();
  for (const h of root.querySelectorAll<HTMLElement>("h1,h2,h3,h4")) {
    let id = slug(h.textContent ?? "") || "section";
    for (let n = 2; seen.has(id); n++) id = `${slug(h.textContent ?? "")}-${n}`;
    seen.add(id);
    h.id = id;
  }

  for (const a of root.querySelectorAll<HTMLAnchorElement>("a[href]")) {
    if (/^https?:/i.test(a.getAttribute("href") ?? "")) {
      a.target = "_blank";
      a.rel = "noopener noreferrer";
    }
  }

  for (const t of root.querySelectorAll("table")) {
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    t.replaceWith(wrap);
    wrap.append(t);
    for (const cell of t.querySelectorAll<HTMLElement>("td,th")) {
      if (/^[−+-]?[\d,.]+\s?(%|ha|km²|m)?$/.test((cell.textContent ?? "").trim())) cell.classList.add("num");
    }
  }

  await renderMermaid(root);

  let n = 0;
  for (const img of root.querySelectorAll("img")) {
    const src = img.getAttribute("src") ?? "";
    if (opts.imageBase && !/^(https?:|data:)/i.test(src)) img.src = new URL(src, opts.imageBase).href;
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("error", () => (img.closest("figure") ?? img).remove());
    if (opts.figures) {
      const fig = document.createElement("figure");
      const cap = document.createElement("figcaption");
      cap.textContent = `Figure ${++n}. ${(img.alt || "").replace(/^./, (c) => c.toUpperCase())}`;
      const holder = img.parentElement?.tagName === "P" && img.parentElement.childNodes.length === 1 ? img.parentElement : img;
      holder.replaceWith(fig);
      fig.append(img, cap);
    }
  }
  return root;
}

let mermaidSeq = 0;

/** Replace ```mermaid code blocks with rendered SVG (light "neutral" theme, strict security). A block that fails to parse stays as code. */
async function renderMermaid(root: HTMLElement): Promise<void> {
  const blocks = [...root.querySelectorAll<HTMLElement>("pre > code.language-mermaid")];
  if (!blocks.length) return;
  const { default: mermaid } = await import("mermaid");
  mermaid.initialize({ startOnLoad: false, theme: "neutral", securityLevel: "strict", fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif" });
  for (const code of blocks) {
    const pre = code.parentElement;
    if (!pre) continue;
    try {
      const { svg } = await mermaid.render(`mmd-${++mermaidSeq}`, code.textContent ?? "");
      const fig = document.createElement("div");
      fig.className = "mermaid-diagram";
      fig.setAttribute("role", "img");
      fig.setAttribute("aria-label", "Diagram");
      fig.innerHTML = svg;
      pre.replaceWith(fig);
    } catch {
      /* keep the source visible */
    }
  }
}
