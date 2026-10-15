import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

// MapLibre runs its worker as a module that imports a sibling chunk; both are
// copied verbatim next to index.html and the page points the worker at them.
function maplibreWorker(): Plugin {
  const require = createRequire(import.meta.url);
  const dist = require.resolve("maplibre-gl/package.json").replace(/package\.json$/, "dist/");
  const files = ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"];
  return {
    name: "maplibre-worker",
    generateBundle() {
      for (const name of files) {
        this.emitFile({ type: "asset", fileName: name, source: readFileSync(dist + name) });
      }
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const name = files.find((f) => req.url === "/" + f);
        if (!name) return next();
        res.setHeader("Content-Type", "text/javascript");
        res.end(readFileSync(dist + name));
      });
    },
  };
}

// Built assets land inside the server package so one image serves API and UI.
const outDir = fileURLToPath(
  new URL("../oss-server/src/oss_server/static/web", import.meta.url),
);

export default defineConfig({
  plugins: [react(), tailwindcss(), maplibreWorker()],
  build: { outDir, emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/admin": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/static": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
});
