# Outbox, events and webhooks

Semascope never writes back to the system that owns your data. What
happens inside it (a document indexed, a curator's decision, a pack
activated, a saved search with new matches) is recorded as an **event** in
an append-only outbox in PostgreSQL. Other systems read the outbox by
cursor, or receive each event by **webhook**. This page is the contract
for both, as implemented in `oss_server.curation` and
`oss_server.pipeline.tasks`.

## Events

Every event has the same envelope:

| Field | Type | Meaning |
|---|---|---|
| `id` | integer | increasing; the cursor |
| `kind` | string | see below |
| `collection` | string | the collection's slug, or empty for events that belong to none |
| `subject` | string | what the event is about (a document id, an IRI, ...) |
| `payload` | object | depends on the kind |
| `created_at` | string | ISO 8601 timestamp |

Kinds and payloads:

| `kind` | `subject` | `payload` | Emitted when |
|---|---|---|---|
| `document.indexed` | the document id | `uri`, `title`, `tags` (how many), `reviews_opened` | a document is indexed (also after a reindex) |
| `proposal.accept_tag`, `proposal.reject_tag`, `proposal.add_tag`, `proposal.remove_tag`, `proposal.link_entity`, `proposal.add_note`, `proposal.dismiss_violation` | the target: a concept or entity IRI, or `annotation:<id>` for a note | `proposal_id`, `external_ref`, `supersedes` (the `external_ref` of the decision this one replaces, or `null`), `document_id`, `params`, `reason`, `pack_id`, `pack_version`, `decided_by`, `decided_at` | a curator decides a review item or a tag, adds a note, or imports annotations; written in the same transaction as the decision. Repeating the decision already in force emits nothing |
| `pack.import`, `pack.activate` | `<pack_id>@<version>` | `job_id`, `status`, `by`, `reindexed` | a pack import or activation job succeeds (collection empty) |
| `alert.saved_search` | `saved-search:<id>` | `saved_search` (its id), `new` (matches since the last check), `total` | an alerting saved search has more matches than at its last check |

`params` of a tag decision carries `tag`: the tag as it was when decided
(stage, confidence, provenance and up to 200 spans, with `span_count`).
`external_ref` is a stable 64-character hex id of the decision; a later
decision on the same target gets a new one and names the old one in
`supersedes`. See [Review and proposal](concepts.md#review-and-proposal).

An alert event deliberately carries no owner, search name, query or
documents, because everyone who reads the collection can read the outbox;
the owner sees the matches on the Alerts page and in their feeds
([Alerts and feeds](concepts.md#alerts-and-feeds)).

Events are never deleted.

## Reading the outbox

`GET /api/curation/events` (a bearer token or a session):

| Parameter | Default | Meaning |
|---|---|---|
| `since_id` | 0 | return events with a larger `id`; pass the last `id` you processed |
| `kind` | — | a prefix: `proposal.` for every decision |
| `collection` | — | one collection |
| `limit` | 200 | at most 1,000 |

Events come oldest first. With `collection`, the caller needs `reader` on
it; without it, the caller sees the events of the collections they can
read. Events without a collection (pack jobs) are shown only to a caller
who can read every collection. A consumer loops: read from its stored
`since_id`, process, store the last `id`, repeat.

`GET /api/curation/proposals` lists the decisions themselves (`collection`,
`kind` as an exact proposal kind, `since_id`, `limit` up to 1,000): `id`,
`collection`, `document_id`, `kind`, `target`, `params`, `reason`,
`pack_id`, `pack_version`, `decided_by`, `decided_at`, `external_ref` and
`applied` (false while Solr or Neo4j has not taken it yet). `supersedes` is
only in the event.

## Webhooks

Staff manage webhooks at `/api/curation/webhooks` (`GET`, `POST`,
`DELETE /api/curation/webhooks/{id}`) or in the admin:

| Field | Meaning |
|---|---|
| `url` | an `http` or `https` URL with a host (422 otherwise) |
| `kinds` | prefixes of the kinds to send (`["proposal.", "pack."]`); empty sends every kind |
| `secret` | signs each delivery when set; never shown again (the admin keeps it when the field is left empty) |
| `enabled` | on or off |

A new webhook starts at the newest event: it does not receive the backlog
(read the outbox for that). The list shows `last_event_id` (its cursor) and
`last_error`.

Delivery:

- Every minute the scheduler sends each enabled webhook the events after
  its cursor, oldest first, up to 100 per run, **one HTTP `POST` per
  event**. Events whose kind does not match `kinds` are skipped and the
  cursor moves past them.
- The body is the event envelope above as JSON, and the request carries
  `Content-Type: application/json`, `X-Semascope-Event: <kind>` and, with a
  secret, `X-Semascope-Signature`.
- Any answer from 200 to 299 (and any other non-redirect answer below 400)
  counts as delivered and moves the cursor past that event; the cursor
  never moves back. A redirect (not followed), a status of 400 or more, a
  refused address, an answer over 64 KiB or a network error stops the run,
  records `last_error`, and the same event is tried again at the next run,
  about a minute later, until it succeeds. There is no back-off and no
  dead-letter queue: disable a webhook whose receiver is gone.
- One delivery run per webhook at a time (a lease in PostgreSQL), so no
  event is sent twice by overlapping runs. A delivery can still repeat if
  the receiver answered but the cursor update was lost; receivers should
  de-duplicate by event `id`.
- Requests go through the outbound address guard: a receiver on a private
  address is reached only when it is listed in
  `SEMASCOPE_ALLOWED_PRIVATE_HOSTS` ([Security](security.md)). Timeouts are
  10 s to connect and 15 s to read.

### Verifying the signature

`X-Semascope-Signature` is `sha256=` followed by the lower-case hex
HMAC-SHA256 of the **raw request body** with the webhook's secret as the
key. Compute it over the bytes you received, before parsing the JSON, and
compare in constant time:

```python
import hashlib
import hmac


def verify(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))
```

The signature covers the body only, with no timestamp, so it proves the
sender knew the secret but not when it sent the event. To refuse a replayed
request, remember the event `id`s you have processed (they increase) and
ignore any you have seen.

## Feeds

Saved searches, any keyword search and all of a user's alerting searches
are also Atom or RSS feeds (`/api/feeds/saved/{id}`, `/api/feeds/search`,
`/api/feeds/alerts`), read with a feed token; see
[Alerts and feeds](concepts.md#alerts-and-feeds).
