# oss-web

The Semascope browser UI: React 19, TypeScript, Vite, TanStack Query and
Router, Tailwind 4. Builds into `packages/oss-server/src/oss_server/static/web`,
which the server image serves at `/`.

```bash
pnpm install
pnpm dev      # http://localhost:5173, proxies /api to a server on :8000
pnpm build    # type-check and build into the server package
pnpm lint
```

Phase 1 views: sign in, search with facets and highlighted snippets, document
page with extracted text per page, found entities and pipeline stage runs.
Later phases add previews, the graph, the map, the timeline and the curation
queue.
