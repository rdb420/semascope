# API

Everything the browser UI and the `oss` command do goes through one HTTP
API under `/api/`, built with Django Ninja. Its reference is generated from
the code and served by every running stack:

- `https://<host>:<port>/api/docs`: interactive documentation (Swagger UI);
- `https://<host>:<port>/api/openapi.json`: the OpenAPI 3.1 document, for
  client generators.

The reference is not published on this site, because it always matches the
server you run. This page is the orientation.

## Signing in

| Method | How | For |
|---|---|---|
| Bearer token | `Authorization: Bearer oss_...` | scripts, the CLI, other systems |
| Session | `GET /api/auth/csrf` (sets the cookie), then `POST /api/auth/login` with `X-CSRFToken`; every later `POST`, `PUT` or `DELETE` sends `X-CSRFToken` too | the browser UI |
| Feed token | `?token=` on `/api/feeds/...` only | feed readers ([Alerts and feeds](concepts.md#alerts-and-feeds)) |

Tokens are stored hashed. The CLI's token is `SEMASCOPE_API_TOKEN` in
`.env` (`oss init` generates it; with none set, the first start writes one
to `/data/cli-token` in the data volume). `./oss rotate
SEMASCOPE_API_TOKEN` replaces it. There is no route to create further
tokens yet: create one in a shell (`docker compose exec server oss-manage
shell`, then `issue_token(user, name)` from `oss_server.api.auth`, which
returns the key once), since the admin's token
form cannot show a new token's value.

Every route except sign-in, `/healthz` and the OpenSearch description needs
a signed-in user, and the Reconciliation API too unless
`SEMASCOPE_RECONCILE_PUBLIC=true`. What a user may do depends on their role
(`reader`, `curator`, `editor`, `admin`) on each collection
([Roles](concepts.md#roles)); a test calls every route as seven kinds of
user, so the reference and the rules cannot drift apart silently.

## Routes by area

| Prefix | What |
|---|---|
| `/api/collections`, `/api/sources` | collections, their sources, runs, uploads (`/collections/{slug}/upload`) and reindexing |
| `/api/documents/{id}` | a document, its text, page previews, the original file ([ADR-0016](adr/0016-original-file-download.md)), query matches, similar documents |
| `/api/search` | keyword, semantic and hybrid search (`POST /api/search`), query tagging, suggestions (`/api/suggest`), saved searches, list search, CSV export, terms ([Searching](search.md)) |
| `/api/views` | table statistics, entities, graph of a search and its edges, alerts |
| `/api/feeds` | Atom and RSS feeds and the feed token |
| `/api/knowledge` | packs (import, diff, activate, remove, jobs), concepts, places, the pack SPARQL console, curator deltas ([Pack specification](assess/pack-spec.md)) |
| `/api/reconcile` | W3C Reconciliation API 0.2 |
| `/api/graph` | neighbours, paths, shared neighbours, the staff Cypher console |
| `/api/curation` | reviews and decisions, document tags and their history, bulk tagging, annotations, proposals, the outbox (`/events`) and webhooks ([Events](events.md)) |
| `/api/export`, `/api/annotations/w3c` | RDF exports, W3C Web Annotations ([Interoperability](interop.md)) |
| `/api/analytics/overview`, `/api/stats` | counts for the Overview page and for operators |
| `/api/audit` | the audit log (staff) |
| `/api/auth`, `/api/ui` | sign-in, sign-out, the current user, the settings the browser needs |

## Errors and limits

Errors answer `{"detail": "..."}` with the HTTP status. An unexpected error
answers 500 with a reference that appears in the server log beside the
traceback; nothing internal is echoed. Bodies above
`SEMASCOPE_REQUEST_MAX_MB` (10) and uploads above `SEMASCOPE_UPLOAD_MAX_MB`
(512) answer 413. The expensive routes are rate-limited per token and answer
429 with `Retry-After`; the defaults and how to change them are in
[Security](security.md) and [Operations](operations.md#limits-and-the-audit-log),
and search parameters have the bounds in [Searching](search.md#limits).

## Integrating

- **Pushing documents in:** create an `upload` source by uploading
  (`POST /api/collections/{slug}/upload`, multipart field `files`), or point
  a source at where the documents live ([Connector settings](connectors.md)).
- **Getting decisions out:** read the outbox by cursor or receive webhooks
  ([Events](events.md)).
- **Getting data out:** search results as CSV or RDF, documents as RDF,
  notes and decisions as Web Annotations ([Interoperability](interop.md)).
