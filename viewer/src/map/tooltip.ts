/**
 * One floating tooltip for every `[data-tip]` element: shown at once on hover, keyboard focus or tap (native `title`
 * tooltips are slow, easy to miss and never appear on touch). Fixed-positioned in <body>, so scrolling panels do not
 * clip it, and kept inside the viewport.
 */
let tip: HTMLDivElement | null = null;
let owner: HTMLElement | null = null;

function show(el: HTMLElement): void {
  tip ??= Object.assign(document.createElement("div"), { className: "tip", role: "tooltip" });
  if (!tip.isConnected) document.body.append(tip);
  owner = el;
  tip.textContent = el.dataset["tip"] ?? "";
  tip.hidden = false;
  const r = el.getBoundingClientRect();
  const { width: w, height: h } = tip.getBoundingClientRect();
  const left = Math.min(Math.max(8, r.left), window.innerWidth - w - 8);
  const below = r.bottom + 6 + h <= window.innerHeight - 8;
  tip.style.left = `${left}px`;
  tip.style.top = `${below ? r.bottom + 6 : r.top - h - 6}px`;
}

function hide(): void {
  if (tip) tip.hidden = true;
  owner = null;
}

const target = (e: Event) => (e.target instanceof Element ? e.target.closest<HTMLElement>("[data-tip]") : null);

export function installTooltips(): void {
  document.addEventListener("pointerover", (e) => {
    if ((e as PointerEvent).pointerType === "touch") return;
    const el = target(e);
    if (el) show(el);
  });
  document.addEventListener("pointerout", (e) => {
    const el = target(e);
    if (el && el === owner && !el.contains((e as PointerEvent).relatedTarget as Node | null)) hide();
  });
  document.addEventListener("focusin", (e) => {
    const el = target(e);
    if (el) show(el);
  });
  document.addEventListener("focusout", hide);
  document.addEventListener("click", (e) => {
    if ((e as PointerEvent).pointerType === "mouse") return; // hover already shows it
    const el = target(e);
    if (el && el !== owner) show(el);
    else if (!el || el === owner) hide();
  });
  document.addEventListener("scroll", hide, true);
  document.addEventListener("keydown", (e) => e.key === "Escape" && hide());
}
