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
hash), and marks items the source no longer yields as deleted.

Connectors: `filesystem` (a folder under the import directory, include and
exclude globs), `web` (pages, a sitemap, or a bounded same-host crawl that
respects robots.txt and refuses private addresses), `upload` (files sent
through the API or `oss upload`), `feed` (RSS and Atom), `mail` (mbox and
eml, attachments as child documents), `archive` (zip and tar with member,
size, ratio and depth limits), `table` (CSV, TSV and XLSX rows as documents),
`s3` (objects in an S3-compatible bucket, AWS, MinIO or Ceph, with change
detection by ETag), `rdf` (one document per subject, from RDF files or a
SPARQL endpoint).

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

A pack can be **reconciled against**: `/api/reconcile` is a W3C
Reconciliation Service (0.2) over the active packs' concepts and
individuals, so OpenRefine and similar tools resolve names to pack IRIs.

Curators **propose changes** (`PackDelta`): another name for a concept, a new
concept in a scheme, a label to retire, or a note. A proposed name tags the
proposing collection, and only that collection, at once as a draft
dictionary entry, and stays one across dictionary syncs and new pack
versions until a pack version accepts or rejects it; the pending deltas
export as `taxonomy.delta.ttl` and `model.delta.yaml`, terms marked `draft`
and `proposed`, for the ontology-skill workflow to accept into the next pack
version. The imported pack itself is never edited.

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

One step of the pipeline, declared with the queue it runs on and the fields
it reads and writes. Stages are plugins registered through entry points.
Stages: `fetch` (store the original under its content hash), `extract`
(Tika text and metadata, OCR for images and scanned PDFs, language
detection; with a MinerU server, PDF, OOXML Office and raster image files
are parsed by MinerU instead), `structure` (pages and chunks), `patterns` (email addresses,
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
kept in the item's history. Proposals are applied at once to Semascope's stores
(Postgres, the Solr facets, a `CURATED` edge in the graph) and written to
the **outbox** (`/api/curation/events`, read by cursor) so an external
system of record can mirror, reject or audit them. Webhooks deliver outbox
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
Cypher console.

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
their text and previews, search in every mode (with its facets, terms, list
search, CSV export, and saved searches and their alerts), the graph,
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
signed-in user (the Reconciliation API also anonymously, for OpenRefine).

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

`compose.observe.yaml` adds an OpenTelemetry collector, Prometheus and
Grafana (on `127.0.0.1:3000`); the server, the workers and beat export
traces and metrics over OTLP/HTTP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
The bundled collector logs traces (debug exporter) and sends metrics to
Prometheus: request and HTTP client durations from the instrumentation,
plus `semascope.documents` (documents processed, by status) and
`semascope.stage.duration` (by stage and outcome). `compose.gpu.yaml` is the
`gpu` runtime: the worker runs from the `-gpu` image on an NVIDIA card, with
FastEmbed on CUDA and GLiNER2 for entities (`./oss up` adds it when
`SEMASCOPE_RUNTIME=gpu`). `compose.dev.yaml` exposes every service on
loopback with hot reload.
