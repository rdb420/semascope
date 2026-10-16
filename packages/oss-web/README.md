# oss-web

The Semascope browser UI: React 19, TypeScript, Vite, TanStack Query and
Router, Tailwind 4. Builds into `packages/oss-server/src/oss_server/static/web`,
which the server image serves at `/`.

```bash
pnpm install
pnpm dev      # http://localhost:5173, proxies /api to a server on :8000
pnpm build    # type-check and build into the server package
pnpm lint     # oxlint; warnings fail too
pnpm typecheck
pnpm test     # vitest (src/__tests__)
pnpm size     # gzip size of the first load and of each chunk, against the budget
```

Every page except search is its own chunk, loaded on first visit
(`src/router.tsx`), and the echarts timeline loads with the first histogram
(`src/components/Timeline.tsx`), so a first visit downloads about 113 kB of
gzipped JavaScript instead of 697 kB. `scripts/bundle-size.mjs` fails CI when
the first load or any chunk outgrows its budget.

Phase 1 views: sign in, search with facets and highlighted snippets, document
page with extracted text per page, found entities and pipeline stage runs.
Later phases add previews, the graph, the map, the timeline and the curation
queue.
