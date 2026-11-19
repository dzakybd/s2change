/**
 * Minimal, safe Markdown renderer for agent answers. Every piece of input is HTML-escaped first and the
 * supported syntax is then re-introduced as a fixed set of tags, so no input can inject markup, attributes
 * or script. Links are limited to http(s). Supports headings, paragraphs, bullet/numbered lists, block
 * quotes, fenced code, rules, **bold**, *italic*, `code` and [links](https://...).
 */

export function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

function inline(raw: string): string {
  const codes: string[] = [];
  // Pull code spans out first so their content is never formatted.
  let s = esc(raw.replaceAll("\u0000", "")).replace(/`([^`\n]+)`/g, (_m, c: string) => `\u0000${codes.push(`<code>${c}</code>`) - 1}\u0000`);
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_m, text: string, url: string) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${text}</a>`);
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?![*\w])/g, "$1<em>$2</em>");
  s = s.replace(/(^|[^\w])_([^_\s][^_\n]*?)_(?![\w])/g, "$1<em>$2</em>");
  return s.replace(/\u0000(\d+)\u0000/g, (_m, i: string) => codes[Number(i)] ?? "");
}

export function renderMarkdown(src: string): string {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const out: string[] = [];
  let para: string[] = [];
  let list: { tag: "ul" | "ol"; items: string[] } | null = null;
  let quote: string[] = [];

  const flushPara = () => {
    if (para.length) out.push(`<p>${para.map(inline).join("<br>")}</p>`);
    para = [];
  };
  const flushList = () => {
    if (list) out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`);
    list = null;
  };
  const flushQuote = () => {
    if (quote.length) out.push(`<blockquote>${quote.map(inline).join("<br>")}</blockquote>`);
    quote = [];
  };
  const flushAll = () => {
    flushPara();
    flushList();
    flushQuote();
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? "";
    if (/^\s*```/.test(line)) {
      flushAll();
      const code: string[] = [];
      for (i++; i < lines.length && !/^\s*```/.test(lines[i] ?? ""); i++) code.push(lines[i] ?? "");
      out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
      continue;
    }
    if (!line.trim()) {
      flushAll();
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      flushAll();
      const level = Math.min(h[1]!.length + 2, 5); // keep headings small inside a chat bubble
      out.push(`<h${level}>${inline(h[2] ?? "")}</h${level}>`);
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      flushAll();
      out.push("<hr>");
      continue;
    }
    const li = /^\s*(?:([-*+])|(\d+)[.)])\s+(.*)$/.exec(line);
    if (li) {
      flushPara();
      flushQuote();
      const tag = li[1] ? "ul" : "ol";
      if (list && list.tag !== tag) flushList();
      list ??= { tag, items: [] };
      list.items.push(li[3] ?? "");
      continue;
    }
    const bq = /^\s*>\s?(.*)$/.exec(line);
    if (bq) {
      flushPara();
      flushList();
      quote.push(bq[1] ?? "");
      continue;
    }
    flushList();
    flushQuote();
    para.push(line.trim());
  }
  flushAll();
  return out.join("");
}
