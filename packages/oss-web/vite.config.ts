import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";

// Built assets land inside the server package so one image serves API and UI.
const outDir = fileURLToPath(
  new URL("../oss-server/src/oss_server/static/web", import.meta.url),
);

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
