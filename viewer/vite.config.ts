import { createReadStream, existsSync, statSync } from "node:fs";
import { extname, join, normalize, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import type { Connect, Plugin } from "vite";
import { defineConfig } from "vite";

const here = fileURLToPath(new URL(".", import.meta.url));
const webDir = resolve(here, "../docs/web");
// Dev-only fixtures (monthly manifest + placeholder frames). They stand in for ../docs/web/monthly until the
// real files exist; S2CD_FIXTURES=1 prefers them even when the real ones do. Never part of a build.
const fixturesDir = resolve(here, "fixtures");
const preferFixtures = process.env["S2CD_FIXTURES"] === "1";

const MIME: Record<string, string> = {
  ".json": "application/json",
  ".geojson": "application/geo+json",
  ".webp": "image/webp",
  ".png": "image/png",
};

/**
 * Serves ../docs/web at /web/ for `vite dev` and `vite preview`, so the viewer's default
 * data path (../web/ relative to index.html) resolves exactly like it does on GitHub Pages
 * (docs/viewer/ next to docs/web/). Nothing is copied: the build never touches docs/web.
 */
function serveDocsWeb(): Plugin {
  const middleware: Connect.NextHandleFunction = (req, res, next) => {
    const url = (req.url ?? "").split("?")[0] ?? "";
    if (!url.startsWith("/web/")) return next();
    const rel = decodeURIComponent(url.slice(5));
    const roots = rel.startsWith("monthly/") && preferFixtures ? [fixturesDir, webDir] : rel.startsWith("monthly/") ? [webDir, fixturesDir] : [webDir];
    const file = roots.map((r) => ({ r, f: normalize(join(r, rel)) })).find(({ r, f }) => f.startsWith(r + sep) && existsSync(f) && statSync(f).isFile())?.f;
    if (!file) return next();
    res.setHeader("Content-Type", MIME[extname(file)] ?? "application/octet-stream");
    createReadStream(file).pipe(res);
  };
  return {
    name: "serve-docs-web",
    configureServer: (server) => void server.middlewares.use(middleware),
    configurePreviewServer: (server) => void server.middlewares.use(middleware),
  };
}

// The agent backend (app/) runs on :8000 in development; same-origin /api in production.
const apiProxy = { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } };

export default defineConfig({
  base: "./",
  plugins: [serveDocsWeb()],
  server: { proxy: apiProxy },
  preview: { proxy: apiProxy },
  build: {
    outDir: "../docs/viewer",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
});
