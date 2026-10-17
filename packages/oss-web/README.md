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

Pages: sign in; search (list, media, terms, table, entities and graph views,
facets, timeline, suggestions); document (text per page, previews, tags and
why, query highlighting, original file, similar documents, annotations);
packs, concepts and the review queue; graph; map; list search; alerts and
feeds; overview. The capability matrix (`docs/assess/capabilities.md`) lists
them with their tests.
