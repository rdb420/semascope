// Bundle-size report and budget for the built UI (run after `pnpm build`).
//
//   node scripts/bundle-size.mjs [--json report.json]
//
// "Initial" is what a first visit to the search page downloads: the entry
// script, the chunks index.html preloads and its stylesheets. Every other
// chunk loads on demand (route-level splitting in src/router.tsx, the echarts
// timeline in src/components/Timeline.tsx). The run fails when the initial
// load or any single chunk grows past its gzip budget, so a static import of
// a map, graph or chart library into the entry path is caught in review.
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

// Budgets in kB (1000 bytes) of gzip level 9. Measured 2026-10-17 after route
// splitting: initial JS 113.2 (352.4 raw), initial CSS 5.0, largest chunk 278.5
// (MapPage: maplibre). Before it, a first visit loaded one 2,360.9 kB bundle,
// 696.9 kB gzip, and 15.1 kB of CSS.
const BUDGET = { initialJs: 140, initialCss: 8, chunk: 300 };

const outDir = fileURLToPath(new URL("../../oss-server/src/oss_server/static/web/", import.meta.url));
const kb = (n) => Math.round(n / 100) / 10; // kB = 1000 bytes, as vite reports
const gz = (file) => gzipSync(readFileSync(file), { level: 9 }).length;

let html;
try {
  html = readFileSync(join(outDir, "index.html"), "utf8");
} catch {
  console.error(`no build in ${outDir}; run pnpm build first`);
  process.exit(2);
}
const initial = new Set(
  [...html.matchAll(/(?:src|href)="\/(assets\/[^"]+\.(?:js|css))"/g)].map((m) => m[1]),
);

const chunks = readdirSync(join(outDir, "assets"))
  .filter((f) => f.endsWith(".js") || f.endsWith(".css"))
  .map((f) => {
    const path = `assets/${f}`;
    const raw = readFileSync(join(outDir, path)).length;
    return { path, raw: kb(raw), gzip: kb(gz(join(outDir, path))), initial: initial.has(path) };
  })
  .sort((a, b) => b.gzip - a.gzip);

const sum = (ext) =>
  kb(chunks.filter((c) => c.initial && c.path.endsWith(ext)).reduce((n, c) => n + c.gzip * 1000, 0));
const report = {
  initialJs: sum(".js"),
  initialCss: sum(".css"),
  largestChunk: chunks.filter((c) => c.path.endsWith(".js"))[0] ?? null,
  budget: BUDGET,
  chunks,
};

for (const c of chunks) {
  console.log(`${c.initial ? "initial " : "lazy    "} ${String(c.gzip).padStart(7)} kB gzip  ${String(c.raw).padStart(8)} kB  ${c.path}`);
}
console.log(`initial load: ${report.initialJs} kB JS + ${report.initialCss} kB CSS (gzip)`);

const jsonAt = process.argv.indexOf("--json");
if (jsonAt > 0) writeFileSync(process.argv[jsonAt + 1], JSON.stringify(report, null, 2) + "\n");

const over = [];
if (report.initialJs > BUDGET.initialJs) over.push(`initial JS ${report.initialJs} kB > ${BUDGET.initialJs} kB`);
if (report.initialCss > BUDGET.initialCss) over.push(`initial CSS ${report.initialCss} kB > ${BUDGET.initialCss} kB`);
for (const c of chunks) {
  if (c.gzip > BUDGET.chunk) over.push(`${c.path} ${c.gzip} kB > ${BUDGET.chunk} kB`);
}
if (over.length) {
  console.error(`bundle budget exceeded:\n  ${over.join("\n  ")}`);
  process.exit(1);
}
console.log("bundle budget ok");
