/**
 * The logo: a satellite pass (teal band) over an open-pit outline (copper terraces). Inside the pass the terraces are
 * drawn bright, as if freshly imaged. Ids carry a suffix so the SVG can be inlined several times on one page.
 */
export function logoSvg(size = 28, suffix = "a"): string {
  const pit = "M16 7.2c4.9-.4 8.9 2.9 9 7.6.1 4.9-3.2 8.6-8.3 9.2-5 .6-9.2-2.2-9.5-6.9C7 12.6 10.7 7.6 16 7.2Z";
  const terraces = (stroke: string) =>
    `<g fill="none" stroke="${stroke}" stroke-linejoin="round"><path d="${pit}" stroke-width="1.5"/>` +
    `<path d="${pit}" stroke-width="1.4" transform="translate(16 15.8) scale(.64) translate(-16 -15.8)"/>` +
    `<path d="${pit}" stroke-width="1.6" transform="translate(16 15.8) scale(.3) translate(-16 -15.8)"/></g>`;
  return `<svg class="logo" viewBox="0 0 32 32" width="${size}" height="${size}" role="img" aria-label="s2change logo" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <clipPath id="lg-r-${suffix}"><rect width="32" height="32" rx="8"/></clipPath>
    <clipPath id="lg-b-${suffix}"><rect x="-4" y="12.4" width="40" height="7.2" transform="rotate(-48 16 16)"/></clipPath>
  </defs>
  <g clip-path="url(#lg-r-${suffix})">
    <rect width="32" height="32" fill="#0d131b"/>
    <g opacity=".78">${terraces("#e0894a")}</g>
    <rect x="-4" y="12.4" width="40" height="7.2" transform="rotate(-48 16 16)" fill="#5eead4" fill-opacity=".14"/>
    <g clip-path="url(#lg-b-${suffix})">${terraces("#7ff3e0")}</g>
    <g stroke="#5eead4" stroke-opacity=".7" stroke-width=".8"><path d="M-4 12.4h40" transform="rotate(-48 16 16)"/><path d="M-4 19.6h40" transform="rotate(-48 16 16)"/></g>
  </g>
  <g transform="translate(25.6 5.8) rotate(-48)"><rect x="-1.5" y="-1.5" width="3" height="3" rx=".6" fill="#f4f7fb"/><rect x="-5" y="-.7" width="2.6" height="1.4" rx=".3" fill="#f4f7fb"/><rect x="2.4" y="-.7" width="2.6" height="1.4" rx=".3" fill="#f4f7fb"/></g>
</svg>`;
}

/** Standalone version for favicon.svg (same artwork, no ids to collide). */
export const FAVICON_SVG = logoSvg(32, "f");
