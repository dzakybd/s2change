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
    const file = normalize(join(webDir, decodeURIComponent(url.slice(5))));
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

export default defineConfig({
  base: "./",
  plugins: [serveDocsWeb()],
  build: {
    outDir: "../docs/viewer",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
});
