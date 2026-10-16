# ADR-0013: Deletion and retention of source documents

Status: proposed, 2026-10-17.

## Decision

A full source run deletes the documents its source no longer has, and
nothing else. Concretely:

- **Per-connector policy.** Each connector says whether what a run no longer
  yields is gone (`deletes_missing`): folders, uploads, buckets, archives,
  mailboxes, tables, RDF files and endpoints, and web sources say yes; feeds
  say no, because entries roll off a feed while the articles still exist. A
  source's `delete_missing` setting (true or false) overrides the policy.
- **Only complete runs delete.** A connector reports what it could not read
  on a run (a page that answered 5xx, 429, 401 or 403, a timeout, a host name
  that did not resolve, a folder it may not list, a corrupt archive) and
  whether it stopped at a limit (`max_pages`, a SPARQL `limit`, a sitemap
  cap). After such a run nothing is deleted, and the source's `last_error`
  says why. A connector that could read nothing at all (the seed of a crawl,
  a feed, the first sitemap, a bucket listing) fails the run; a failed run
  deletes nothing.
- **Minimum yield.** A run that sees nothing at all, or, for a source with at
  least ten documents, fewer than half of them (`min_yield`, 0.5), deletes
  nothing and says so. An operator who knows the documents are gone sets
  `min_yield` to 0 and runs the source again.
- **Deleted documents come back.** A document a run deleted is a tombstone
  (its row with the content cleared); when a later run yields it again, with
  the same content or not, it is processed again.
- **One run per source.** A run claims its source (`running`, with a run id)
  in one conditional update; a second run finds it claimed and does nothing,
  and the API answers `409` to a run request while one is in progress. A run
  deletes only while it still holds its source and only what it did not see
  and nothing touched since it started; each delete it queues is a no-op for
  a document a newer run has seen.
- **A delete reaches everything.** Deleting a document removes it from Solr
  (document and passages) and from the graph (its edges, the entities only it
  mentioned, and its properties; the node stays only to carry curator
  decisions), its blob and previews when no other document uses them, and
  its work file. Deleting a source or a collection takes its documents out of
  search at once and then removes their graph nodes (curator decisions
  included), files, the source's work area and, for uploads, the uploaded
  files. A daily `gc` task removes blobs and previews no document uses and
  work files older than a week.

## Context

The 1.0.0-rc.2 review found three ways to lose data silently (SEMA-010,
SEMA-012, SEMA-034): a transient failure (a site's 503, a feed timing out, a
corrupt zip, an unreadable folder) made a run yield less, and the run
deleted the rest; a deleted document was never processed again when its
source yielded it unchanged; a feed forgot every article that rolled off;
and two runs of one source (a click on "run" during the scheduled run)
deleted each other's documents. Deleting a source or collection also left
graph nodes, blobs, previews and uploads behind (SEMA-011), although the
documentation promised deletion.

Open Semantic Search kept documents until they were deleted by hand or the
whole index was rebuilt, and checked only whether a document existed before
skipping it. Deleting by absence is more useful for folders and buckets that
mirror a system of record, but it is only safe when a run is known to have
seen everything.

## Consequences

- A source whose items are unreadable on every run never deletes anything
  until the problem is fixed; the source's `last_error` names the first
  unreadable item, so the problem is visible.
- A crawl that reaches `max_pages` never deletes by absence: raise
  `max_pages` or narrow `include` for a site that should be mirrored exactly.
- A feed keeps every article it ever showed. Retention by age (a tombstone
  TTL, or deleting feed entries older than N days) is not part of this
  decision.
- Mail URIs carry a hash of the message, so the same Message-ID on two
  different messages gives two documents; existing mail documents are
  re-created once under their new URI on the first run after the upgrade.
- Uploaded files, work files and blobs are removed when their documents are
  deleted, so a restore from backup is the only way back for them.

## Alternatives

- **Never delete by absence** (the Open Semantic Search behaviour): simple and
  safe, but a mirrored folder keeps files that were removed for legal or
  privacy reasons until someone deletes each by hand.
- **Delete only after N consecutive runs miss a document**: tolerates
  transient errors without connector reports, but delays wanted deletions
  and needs a per-document counter; the connector report plus the minimum
  yield cover the same failures on the first run.
- **A lock in Valkey for the source run**: works, but the source row already
  holds the run state the API and the scheduler read, and `recover` already
  releases runs whose worker died.
