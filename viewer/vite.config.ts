import { createReadStream, existsSync, statSync } from "node:fs";
import { extname, join, normalize, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import type { Connect, Plugin } from "vite";
import { defineConfig } from "vite";

const here = fileURLToPath(new URL(".", import.meta.url));
const webDir = resolve(here, "../docs/web");

const MIME: Record<string, string> = {
  ".json": "application/json",
  ".geojson": "application/geo+json",
  ".webp": "image/webp",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".md": "text/markdown; charset=utf-8",
};

/**
 * Serves ../docs/web at /web/ for `vite dev` and `vite preview`, as Caddy does in production.
 * Nothing is copied: the build never touches docs/web.
 */
function serveDocsWeb(): Plugin {
  const middleware: Connect.NextHandleFunction = (req, res, next) => {
    const url = (req.url ?? "").split("?")[0] ?? "";
    if (!url.startsWith("/web/")) return next();
    const rel = decodeURIComponent(url.slice(5));
    const file = normalize(join(webDir, rel));
    if (!file.startsWith(webDir + sep) || !existsSync(file) || !statSync(file).isFile()) return next();
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
    outDir: "dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
});
