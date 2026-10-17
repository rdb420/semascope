# ADR-0016: Original files are served by the API, per collection

Status: proposed, 2026-10-17. Records decision D-2 of the review (serving
original files).

## Context

The fetch stage keeps every original in the data volume under the SHA-256
of the bytes it read, and the preview stage renders page images from it.
Up to 1.0.0-rc.2 the only way to see a document's bytes was a page preview
(`GET /api/documents/{id}/preview/{page}`); there was no way to open or
download the original, so a search result could not lead back to the file
it was found in.

Open Semantic Search identified documents by their file or web address and
let the browser open that address. That only works when every reader can
reach the original location (a shared drive mounted the same way on every
machine, a public site), and it bypasses whatever access control the search
applies. Semascope's sources include uploads, mailbox attachments, archive
members, S3 objects and pages fetched once, whose original location is not
something a reader can open, and collections are read per collection
([Roles](../concepts.md#roles)).

Serving untrusted files from the application's own origin is itself a risk:
an HTML or SVG file opened inline would run its scripts with the reader's
session.

## Decision

`GET /api/documents/{id}/original` serves the stored original
(`oss_server.api.views.original`, `oss_server.api.files`):

- **Same rule as the document.** The caller needs `reader` on the
  document's collection, as for its text and previews; a feed token cannot
  open it. The rule is in the route-by-route authorisation test.
- **Found from the record, never from the request.** The file is the
  document's stored blob, which must resolve inside the blob store
  (`<data>/blobs`); anything else is a 404. No part of the request names a
  path.
- **Safe types inline, the rest as attachments.** PDF, raster images
  (PNG, JPEG, GIF, WebP, AVIF, BMP), plain text and CSV, audio and video open
  in the browser; every other type, HTML and SVG included, and any document
  with `?download=true`, is sent with `Content-Disposition: attachment`.
  Every response carries `X-Content-Type-Options: nosniff`, and every
  response except a PDF a sandboxing `Content-Security-Policy`
  (browsers will not run their PDF viewer in a sandbox).
- **Viewer-friendly.** The stored media type, the original file name (from
  the URI, else the title), an ETag (the content hash), `Accept-Ranges` and
  a single byte range (206, or 416 when unsatisfiable), so PDF viewers and
  media players can seek in large files; `Cache-Control: private`.
- **Logged.** Each response is a line on the server's log with the
  document, collection, user and range.

The document page and each search result offer "open original" and
"download".

## Consequences

- Readers see exactly the bytes that were indexed, even when the original
  location has changed or was never reachable.
- Reading a collection now includes its originals, not only its extracted
  text. A collection whose originals should not leave the server needs its
  own decision (a per-collection switch is not part of this one).
- Original downloads are recorded in the server log, not in the audit log
  table, and are not rate-limited apart from Caddy's limits.
- The blob store is shared by content hash, so deleting one of two
  identical documents keeps the file for the other
  ([ADR-0013](0013-deletion-and-retention.md)).
- Tests: `packages/oss-server/tests/test_views.py` (type and name, the
  reader rule, the blob-store confinement, byte ranges) and
  `tests/integration/test_views.py::test_original_is_the_stored_file`.

## Alternatives

- **Link to the original location** (the file URI or web address): no
  access control, and impossible for uploads, attachments, archive members
  and buckets.
- **Let Caddy serve the blob store directly**: faster for large files, but
  the reader check would need a second mechanism (signed URLs or an auth
  sub-request), and the content-type and disposition rules would live in the
  proxy configuration.
- **Download only, never inline**: simplest and safest, but makes reading a
  PDF or listening to a recording a two-step chore; the inline allow-list
  keeps the convenience for types that cannot run script.
