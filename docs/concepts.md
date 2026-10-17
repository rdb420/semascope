# Concepts

## Collection

A named corpus inside one Semascope deployment: its sources, its packs, its
pipeline profile and who may read or curate it. One Solr collection and one
Neo4j database serve every collection, with the collection name as a filter.

## Source

One configured connector instance inside a collection: a folder, a site, a
feed, a mailbox, a bucket, an RDF endpoint, or the collection's upload area.
A source runs on demand (`POST /sources/{id}/run`, `oss index`) or on a
schedule (`every 15m`, `every 2h`, or a cron expression). Each run
re-discovers the source, re-queues only new or changed items (by content
hash; a folder's unchanged files, same size and time, are not read again),
and marks items the source no longer yields as deleted. One run of a source
runs at a time: a run request while one is in progress is refused (`409`),
and a scheduled run that finds its source running skips its turn. A failed
run is retried at the source's next scheduled time.

Deletion by absence is careful ([ADR-0013](adr/0013-deletion-and-retention.md)):
a run deletes only when it read everything it tried, did not stop at a
limit (`max_pages`, a SPARQL `limit`), and saw at least half of the
documents the source had (`min_yield`, 0.5, for sources of ten documents or
more; a run that sees nothing never deletes). Otherwise nothing is deleted
and the source's `last_error` says why; a run that could read nothing at
all (a site that is down, a missing feed) fails. Feeds never delete by
absence, because entries roll off a feed while the articles still exist; a
source's `delete_missing` (true or false) overrides its connector. A
document deleted by an earlier run comes back when its source has it again.

Connectors: `filesystem` (a folder under the import directory, include and
exclude globs), `web` (pages, a sitemap, or a bounded same-host crawl that
respects robots.txt and refuses private addresses), `upload` (files sent
through the API or `oss upload`), `feed` (RSS and Atom), `mail` (mbox and
eml, attachments as child documents; a message is `mail:<Message-ID>#<hash>`,
so two messages sharing a Message-ID stay two documents), `archive` (zip and
tar; members are streamed to disk, never held in memory, within a per-member
size limit, `max_member_bytes`, 256 MiB, a byte budget for everything
expanded from one archive, nested archives included, of `max_ratio` (100)
times its size and at most `max_total_bytes` (4 GiB), `max_members` (10,000)
across all levels, and `max_depth` (2) nesting levels; a member whose zip
header claims a higher ratio is skipped), `table` (CSV, TSV and XLSX rows as
documents), `s3` (objects in an S3-compatible bucket, AWS, MinIO or Ceph,
with change detection by ETag), `rdf` (one document per subject, from RDF
files or a SPARQL endpoint, read in a stable order).

Items from folders, archives, buckets and web sources carry their folder as
a hierarchical `folder_paths` field (the source folder's name, a bucket or a
host, then the folders below it) and their file extension as `extension_s`,
for a folder facet and a file-type filter. Connectors that download or
unpack what they find write it to a work area on the shared data volume
(`<data>/work/<collection>/<source>/`), which the document task clears once
the original is in the blob store.

## Pack

A knowledge model packaged as an `ontology` skill project bundle:
`requirements.yaml`, `model.yaml`, `ontology.ttl` (OWL classes and
properties), `taxonomy.ttl` (SKOS schemes and concepts), `shapes.ttl` (SHACL),
`instances.ttl` (named individuals), `queries/` (competency questions),
evidence and decisions, and a `release-manifest.json` of content hashes.
Semascope imports, validates, versions, diffs and exports packs; it never
edits one silently. SKOS concepts and, when the pack opts in, named individuals tag text
(`semascope.tag_schemes` and `semascope.tag_individuals` in
`requirements.yaml`); OWL classes become entity types, facet families and
graph labels; shapes validate extracted metadata; competency questions run
at import against the pack's triples in the embedded store.

A concept under several broader concepts (a polyhierarchy) has a facet
path through each of them, and the pack page's tree lists it under each
parent; `skos:related` links are kept and shown on the concept page.

Importing or activating a pack is a **job**: the request validates the
bundle and, when the change would replace an active version, answers 409
with the diff and reindex plan until it is confirmed; then a worker loads
the triple store, derives the vocabulary, runs the competency questions,
syncs the tagging dictionary and queues the reindex, and the request
answers 202 with the job. The pack pages, `oss pack import` and
`oss pack activate` follow it to `done` or `failed`
(`/api/knowledge/pack-jobs`). The dictionary sync adds the new entries
before it removes the old ones, so tagging never runs against an empty
dictionary. A **plain list** of names (`Name | other name`, one concept per
line) can be imported as a pack too (`oss pack import-list`, or the
Knowledge models page): Semascope writes it as a bundle with one SKOS scheme
and imports it the same way.

A pack can be **reconciled against**: `/api/reconcile` is a W3C
Reconciliation Service (0.2) over the active packs' concepts and
individuals, so OpenRefine and similar tools resolve names to pack IRIs.
Callers sign in (a bearer token) like everywhere else; OpenRefine cannot send
a token, so an operator who wants it to call the service sets
`SEMASCOPE_RECONCILE_PUBLIC=true`, which opens it without sign-in, limited
per client address.

Curators **propose changes** (`PackDelta`): another name for a concept, a new
concept in a scheme, a label to retire, or a note. A proposed name becomes a
draft dictionary entry for the proposing collection, and only that
collection: documents processed from then on are tagged with it, and the
collection's documents that already contain it are queued for the tag stage
at once (tagged when that replay has run, usually within seconds to
minutes). It stays a draft entry across dictionary syncs and new pack
versions until a pack version accepts or rejects it; the pending deltas
export as `taxonomy.delta.ttl` and `model.delta.yaml`, terms marked `draft`
and `proposed`, for the ontology-skill workflow to accept into the next pack
version. The export is written by an RDF serialiser, and IRIs and language
tags in a proposal are checked, so a proposal's text cannot add triples to
the fragment. The imported pack itself is never edited.

When two active packs define the same IRI or give the same label to
different individuals, **pack precedence** decides: a collection's
`PackBinding.precedence` (lowest first, then pack id), or, for a collection
without bindings and for lookups outside a collection, the lowest
precedence any binding gives the pack (100 when unbound), then pack id. The
tag and link stages, the concept page (`/api/knowledge/concepts?iri=`, with
an optional `collection`) and the reconciliation preview all use it.

## Profile

An ordered list of pipeline stages for a collection, validated as a DAG of
`requires` and `provides`. `default`, `fast` and `full` ship with the
product.

## Stage

One step of the pipeline, declared with the fields it reads and writes.
A document's whole profile runs in one task in one worker process
([ADR-0010](adr/0010-one-task-per-document.md)). Stages are plugins
registered through entry points.
Stages: `fetch` (store the original under the SHA-256 of the bytes it
read), `extract` (Tika text and metadata, OCR for images and scanned PDFs in
the collection's `ocr_languages`, language detection, and the text of
embedded items such as attachments and the files of a zip, within limits;
with a MinerU server, PDF, OOXML Office and raster image files are parsed by
MinerU instead, and Tika keeps OCR for everything else), `structure` (pages and chunks), `patterns` (email addresses,
links, phone numbers, amounts, hashtags), `tag` (dictionary tagging against
the active packs through Solr's Tagger, with a hierarchical facet per
scheme), `ner` (named entities: spaCy, GLiNER2 or a remote service, see
the runtimes in [operations](operations.md#runtimes)), `link` (NER entities to pack
individuals by label), `validate` (SHACL over the document's metadata graph,
`full` profile), `preview` (PDF page images and image thumbnails), `index`
(Solr). Every run is recorded on the document with
its outcome and duration, and `reindex` replays everything after `extract`
from the stored text without touching Tika.

## Review and proposal

The pipeline opens **review items** where a person should look: a named
entity the model found that resembles a pack individual (link it, or
reject), a tag matched on a less certain label, a SHACL violation on the
document's metadata. A decision on a review item, or directly on a
document's tags, becomes a **proposal**: the record of the decision with a
stable `external_ref`. The proposal log is append-only and the newest
decision on a target is the one in force, so accept, reject and accept again
leaves three proposals, each with its own `external_ref` (chained to the one
it supersedes, which the outbox event names in `supersedes`), and the tag
accepted. Repeating the decision already in force records nothing. A
proposal keeps the tag as it was decided (its evidence) and the pack version
it came from, and every decision on a review item, a deferral included, is
kept in the item's history. A proposal, the document's record in Postgres
and its outbox event are written in one transaction; then, before the API
answers, the decision is applied to the Solr facets (the hierarchical
`<scheme>_paths` facets included: a rejected concept leaves the tree, a
concept a curator added joins it) and to a `CURATED` edge in the graph. If
Solr or Neo4j is down, the proposal is kept with `applied: false` and a
periodic task applies it when they are back. The **outbox**
(`/api/curation/events`, read by cursor) lets an external system of record
mirror, reject or audit every decision.

**Bulk tagging** applies one decision to every document a search finds: on
a concept's page a curator counts the documents of a keyword search in a
collection, then accepts or rejects the concept on all of them (at most
10,000; `POST /api/curation/bulk-tags`). It runs as a job, and each
document gets its own proposal, with the job named in its reason, exactly as
if it had been decided on its own page. Webhooks deliver outbox
events with an HMAC signature to an http(s) URL; like URL sources, they
reach a receiver on a private address only when it is listed in
`SEMASCOPE_ALLOWED_PRIVATE_HOSTS`, and a redirect is reported, not followed. A reindex never undoes a decision. A rejected
tag stays on the document, flagged, as the machine's evidence; search and
the graph leave it out. See the [explainability model](assess/explainability.md).

## Graph

Every indexed document is projected into Neo4j: `Document`, `Source`,
`Collection`, `Concept` (pack concepts, `TAGGED`), `Entity` (pack
individuals and named entities, `MENTIONS`), `Pack`, and `CURATED` edges for
human decisions. A collection can pseudonymise named-entity keys. The UI
graph page shows the hubs of a collection and expands neighbourhoods;
`/api/graph` offers neighbours, shortest paths and a staff-only read-only
Cypher console. `/api/views/graph` computes co-occurrence (concepts and
entities sharing documents, optionally within a search's results) and
`/api/views/graph/edge` the documents behind an edge; see
[views of a search](#views-of-a-search).

## Original files

The fetch stage keeps every original in the data volume under its content
hash. `GET /api/documents/{id}/original` serves it, and only to a user who
may read the document's collection; the file is found from the document
record, never from a path in the request. The response carries the stored
media type, the original file name, `nosniff` and an ETag (the content
hash), and it answers a single byte range so viewers can seek in large
files. PDFs, raster images, plain text, audio and video open in the browser;
everything else, HTML and SVG included, downloads as an attachment, so a
document can never run script in Semascope's pages. `?download=true` always
downloads. The document page and every search result have an "open original"
command.

## Views of a search

One search (its words, collection, filters and mode) can be looked at in
several ways, and the links between them keep it:

- **List**, **media** and **terms** on the search page.
- **Table**: one row per document with sortable columns. Title, size and the
  dates sort every match; the other columns sort the rows on the page. Above
  the table, column statistics cover every match: total size, pages, the date
  range, types and languages.
- **Entities**: every entity, concept and pattern facet of the results as a
  grid of top values; a value narrows the result list to it.
- **Graph**: the concepts and entities that occur in the same documents of
  the results (the top 500 by keyword relevance), with thicker edges for
  more shared documents. Clicking an edge, here or on the neighbourhood
  graph, lists the documents behind it; clicking a node centres the graph on
  what occurs with it.

A result link carries the query to the document page, which highlights its
words in the text, counts the matches per page, marks the matching page
previews and jumps to the first match. Matching there is literal (whole
words, simple inflections, `*` as a prefix), so a document found through
stemming, synonyms or semantic search may show no highlight.

### Alerts and feeds

The **Alerts** page lists your saved searches with their newest documents;
switching a search's alert on also puts an `alert.saved_search` event in the
outbox, and so to its webhooks, when the search has new matches. Every reader
of the collection can read the outbox, so the event carries only the saved
search's id and how many matches it has: never its owner, its name or the
documents, which only the owner reads on the Alerts page. Every saved search, any keyword search
(`/api/feeds/search?q=…&collection=…&filter=field:value`) and all your
alerting searches together (`/api/feeds/alerts`) are Atom feeds, or RSS with
`format=rss`, newest documents first. Feed readers cannot sign in, so a feed
URL carries a **feed token**: it opens your feeds and nothing else, and a
feed shows only what you may read at the moment it is fetched. Renewing the
token on the Alerts page cuts every older feed URL off; revoking removes it.

## Search modes

`keyword` matches words and phrases (Solr edismax over title and text, with
highlighting). `semantic` embeds the query with the bundled FastEmbed model
and ranks documents by their best-matching chunks, returning the passages.
`hybrid` fuses both rankings with reciprocal rank fusion. Every document's
chunks are embedded by the `embed` stage into the `chunks` collection, each
chunk recording its model; the embedder is a runtime choice (bundled
FastEmbed on CPU or CUDA, an embedding sidecar, or any OpenAI-compatible
`/embeddings` endpoint), and only chunks of the current model take part in
a search.

## Roles

`reader` searches and reads; `curator` decides reviews, tags and annotates;
`editor` manages collections, sources, pack imports and pack deltas; `admin`
does everything and is what Django staff and superusers are. Each role
includes the ones before it. A role is held on one collection or globally
(`CollectionRole` in the admin, with or without a collection).

Reading a collection needs at least `reader` on it. That covers documents,
their text, previews and original files, search in every mode (with its facets, terms, list
search, CSV export, the table and entities views, saved searches, their
alerts and feeds), the graph,
concept documents and places, the curation queues (reviews, proposals,
events, annotations, tags) and the statistics. A search, list or graph query
that names no collection covers only the collections the user may read.

`SEMASCOPE_DEFAULT_ROLE` is the role a signed-in user has on a collection
they hold no role on:

| Value | Effect |
|---|---|
| `none` (default) | per-collection reading: a user sees only the collections they have a role on, or all of them with a global role |
| `reader` | every signed-in user reads every collection; roles add the right to change things |
| `curator`, `editor`, `admin` | that role on every collection, for small single-team installs |

An unknown value counts as `none`. Up to 1.0.0-rc.2 every signed-in user
could read every collection whatever this setting said; up to 1.0.0-rc.3 the
default was `reader`. An install that relied on open reading sets
`SEMASCOPE_DEFAULT_ROLE=reader` in `.env` explicitly (`oss init` writes
`none` for new installs). Packs, concepts and
the Reconciliation API are not collection data and are readable by every
signed-in user who holds a role on at least one collection (or a global
role); an account with no role at all reads nothing, since a list pack can
name people or clients. The Reconciliation API is anonymous only when
`SEMASCOPE_RECONCILE_PUBLIC=true`.

Nobody signs themselves up. Local accounts are created by an administrator
(the Django admin or `oss-manage createsuperuser`) and always work; set the
`SEMASCOPE_OIDC_*` variables to add single sign-on through OpenID Connect.
Single sign-on creates an account on a user's first sign-in while
`SEMASCOPE_OIDC_AUTO_SIGNUP` is true (the default); with `false`, only
accounts an administrator created can sign in that way, matched by e-mail
address. `SEMASCOPE_OIDC_DEFAULT_ROLE` is the global role those new
accounts get: `none` (the default) gives them no role row, so they read what
`SEMASCOPE_DEFAULT_ROLE` allows; `reader`, `curator`, `editor` or `admin`
gives every new sign-on account that role on every collection. Roles are
changed afterwards the same way as for any user.

## Backup and restore

`./oss backup` writes Postgres, the three Solr collections and the data
volume (originals, previews, uploads, the pack store) into
`data/backups/<stamp>`; `--with-graph` also dumps Neo4j (which otherwise
`./oss reindex` rebuilds). `./oss restore <dir>` loads a backup back. Keep
`.env` with the backup: it holds the secrets the data was written with.
Both stop with a non-zero exit and a message at the first step that fails.
A restore first checks every file of the backup, then loads Postgres and
each Solr collection beside the live data (a `semascope_restore` database,
`<collection>_restoring` collections); only when all of them loaded does it
switch over. A backup that cannot be read leaves the existing data as it was
and the application running.

## Operations profiles

`compose.observe.yaml` adds an OpenTelemetry collector, Jaeger, Prometheus,
Grafana (on `127.0.0.1:3000`, user `admin`, password `GRAFANA_PASSWORD`
from `.env`, which `oss init` generates; add one to an older `.env`) and
Flower (on `127.0.0.1:5555`, the same user and password); the server, the
workers and beat export traces and metrics over OTLP/HTTP when
`OTEL_EXPORTER_OTLP_ENDPOINT` is set. The bundled collector sends traces to
Jaeger (in memory) and metrics to Prometheus: request and HTTP client
durations from the instrumentation, plus `semascope.documents` (documents
processed, by status) and `semascope.stage.duration` (by stage and
outcome), shown on Grafana's *Semascope* dashboard.
`compose.secrets.yaml` reads every secret from a file (Docker secrets;
`./oss up` adds it after `oss init --secrets-dir`). `compose.gpu.yaml` is the
`gpu` runtime: the worker runs from the `-gpu` image on an NVIDIA card, with
FastEmbed on CUDA and GLiNER2 for entities (`./oss up` adds it when
`SEMASCOPE_RUNTIME=gpu`). `compose.dev.yaml` exposes every service on
loopback with hot reload.
