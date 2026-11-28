import { LIVE_URL, prefersReducedMotion } from "./domain";

/**
 * Markdown to sanitised DOM for the Report tab and the monitoring brief. marked and DOMPurify load on first use,
 * so they stay out of the entry bundle. Post-processing happens on the sanitised DOM: heading ids for the table of
 * contents, figures with numbered captions, scrollable tables and external links that open safely. ```mermaid fences
 * become SVG diagrams; the mermaid package is imported only when a document contains one, and KaTeX only when it has `$` math. GitHub-style alerts
 * (`> [!NOTE]`) become callout boxes, and remote images are limited to the technology logos on cdn.simpleicons.org.
 */
interface RichOptions {
  /** Resolve relative image URLs against this (the report's own folder). */
  imageBase?: URL;
  figures?: boolean;
}

/** Heading ids follow GitHub's rule (lowercase, punctuation dropped, spaces to hyphens), so `[x](#tldr)` works in both places. */
const slug = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .replace(/\s/g, "-")
    .slice(0, 60);

/** Remote images are allowed only from these hosts (technology logos); anything else is dropped. */
const IMAGE_HOSTS = new Set(["cdn.simpleicons.org"]);

const ALERTS = {
  note: { title: "Note", icon: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.6v.2"/>' },
  tip: { title: "Tip", icon: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.3 1.1 2.2h5c0-.9.4-1.6 1.1-2.2A6 6 0 0 0 12 3Z"/>' },
  important: { title: "Important", icon: '<path d="M4 5h16v11H9l-5 4Z"/><path d="M12 8.5v3.5M12 14v.2"/>' },
  warning: { title: "Warning", icon: '<path d="M12 4 2.8 19.5h18.4Z"/><path d="M12 10v4.5M12 17v.2"/>' },
  caution: { title: "Caution", icon: '<path d="M8.5 3h7L21 8.5v7L15.5 21h-7L3 15.5v-7Z"/><path d="M12 8v5M12 15.6v.2"/>' },
} as const;
type AlertKind = keyof typeof ALERTS;

/** Turn `> [!NOTE]` blockquotes (GitHub alert syntax) into styled callouts; other blockquotes are left alone. */
function renderAlerts(root: HTMLElement): void {
  for (const bq of root.querySelectorAll("blockquote")) {
    const first = bq.firstElementChild;
    const m = first?.tagName === "P" ? /^\s*\[!(note|tip|important|warning|caution)\]\s*/i.exec(first.textContent ?? "") : null;
    if (!first || !m) continue;
    const kind = (m[1] ?? "note").toLowerCase() as AlertKind;
    const walker = document.createTreeWalker(first, NodeFilter.SHOW_TEXT);
    const t = walker.nextNode();
    if (t) t.textContent = (t.textContent ?? "").replace(/^\s*\[![A-Za-z]+\]\s*/, "");
    first.querySelector(":scope > br:first-child")?.remove();
    if (!first.textContent?.trim() && !first.children.length) first.remove();
    const box = document.createElement("div");
    box.className = `md-alert md-alert-${kind}`;
    box.setAttribute("role", "note");
    const head = document.createElement("div");
    head.className = "md-alert-title";
    head.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ALERTS[kind].icon}</svg>`;
    head.append(ALERTS[kind].title);
    box.append(head, ...bq.childNodes);
    bq.replaceWith(box);
  }
}

export async function renderRich(markdown: string, opts: RichOptions = {}): Promise<HTMLElement> {
  const [{ Marked }, { default: DOMPurify }] = await Promise.all([import("marked"), import("dompurify")]);
  const md = new Marked({ gfm: true });
  if (markdown.includes("$")) {
    const [{ default: markedKatex }] = await Promise.all([import("marked-katex-extension"), import("katex/dist/katex.min.css")]);
    md.use(markedKatex({ throwOnError: false, output: "html" }));
  }
  const html = await md.parse(markdown);
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
    const href = a.getAttribute("href") ?? "";
    // Links into the live app (they work on GitHub too) navigate in place when the document is shown inside the app.
    if (href.startsWith(`${LIVE_URL}/#/`)) a.setAttribute("href", href.slice(LIVE_URL.length + 1));
    else if (/^https?:/i.test(href)) {
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

  renderAlerts(root);
  for (const a of root.querySelectorAll<HTMLAnchorElement>('a[href^="#"]')) {
    // The app routes on the hash, so in-document anchors scroll instead of navigating.
    a.addEventListener("click", (e) => {
      const id = decodeURIComponent((a.getAttribute("href") ?? "").slice(1));
      const target = id ? root.querySelector(`[id="${CSS.escape(id)}"]`) : null;
      if (!target) return;
      e.preventDefault();
      target.scrollIntoView({ block: "start", behavior: prefersReducedMotion() ? "auto" : "smooth" });
    });
  }

  await renderMermaid(root);

  let n = 0;
  for (const img of root.querySelectorAll("img")) {
    const src = img.getAttribute("src") ?? "";
    let logo = false;
    if (/^https?:/i.test(src)) {
      try {
        logo = IMAGE_HOSTS.has(new URL(src).hostname);
      } catch {
        /* malformed URL: dropped below */
      }
      if (!logo) {
        img.remove();
        continue;
      }
      img.classList.add("tech");
    } else if (opts.imageBase && !/^data:/i.test(src)) img.src = new URL(src, opts.imageBase).href;
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("error", () => (img.closest("figure") ?? img).remove());
    // Logos and explicitly sized images (the header mark) stay inline; everything else becomes a numbered figure.
    if (opts.figures && !logo && !img.hasAttribute("width") && !img.hasAttribute("height")) {
      const fig = document.createElement("figure");
      const cap = document.createElement("figcaption");
      const holder = img.parentElement?.tagName === "P" && img.parentElement.childNodes.length === 1 ? img.parentElement : img;
      // An italic-only paragraph right after the image is its caption (renders as plain italics on GitHub).
      const next = holder.nextElementSibling;
      const own = next?.tagName === "P" && next.children.length === 1 && next.firstElementChild?.tagName === "EM" && next.textContent === next.firstElementChild.textContent ? next : null;
      const text = (own?.textContent ?? img.alt ?? "").trim();
      own?.remove();
      cap.textContent = `Figure ${++n}. ${text.replace(/^./, (c) => c.toUpperCase())}`;
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
    } catch (err) {
      console.warn("mermaid: diagram left as source", err); // keep the source visible
    }
  }
}
