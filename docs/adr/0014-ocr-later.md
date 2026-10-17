# ADR-0014: OCR now, later or off

Status: proposed, 2026-10-17. Records decision D-4 of the review (two-tier OCR).

## Context

OCR is the slowest step of extraction by far: a scanned page costs Tesseract
seconds, a text-layer page milliseconds. With one pass, a large import of
scans is invisible to search until every page has been OCRed, and documents
with a text layer wait behind them. Open Semantic Search offered to run OCR
later; Semascope had one pass with OCR on.

## Decision

A collection chooses when scanned content is OCRed with
`settings.pipeline.ocr`:

- **`now`** (the default; `true` as before): in the document's own run.
- **`later`**: the document's run reads the text layer and metadata only
  (Tika with OCR skipped; MinerU in `txt` mode). If the result looks like it
  needs OCR (a raster image, a PDF with fewer than `ocr_min_chars_per_page`
  (40) characters per page, or a container holding raster images), the
  document is indexed as it is and marked `ocr_pending` (in Postgres and as
  `ocr_pending_b` in the index). A follow-up task, `ocr_document`, is queued
  on the `cpu` queue at the lowest Redis priority (9; ordinary tasks are 0),
  so workers take it only when no other document task waits. It runs
  `extract` again with OCR on from the stored original and every stage after
  it, and stores the result as a normal run would, under the same
  fingerprint, so a later source run finds the document done.
- **`off`** (`false` as before): never.

The task checks the document still has the content it was queued for and
is still pending, so duplicates and superseded tasks do nothing. When the
OCR pass fails for good, the text-layer result stays searchable, the mark
is removed and the document records why (`metadata.ocr_error`). A transient
failure is retried with the document tasks' backoff. `/api/stats` and
`oss stats`/`oss doctor` count pending documents; the document page shows
"OCR pending". A full `oss reindex` queues the OCR pass again for pending
documents whose task was lost.

## Consequences

- One queue and one worker pool still do everything (ADR-0010): no new
  queue, no compose change. Priority is a Redis-transport feature of Celery
  (`priority_steps` 0, 3, 6, 9); a task sent without a priority goes first.
- A document under `later` is processed twice when it needs OCR. The total
  work is the same OCR plus one more run of the cheap stages.
- The paths-first pass of the review's W2-3 (metadata and paths before any
  extraction) and an import-status panel in the UI are not part of this
  decision.

## Alternatives

- **A separate `ocr` queue and worker service**: isolates OCR fully but
  needs a compose service and sizing; priority on the shared queue gives the
  ordering without either.
- **Two Tika services** (a fast one without OCR and a full one): the shape
  Open Semantic Search used; doubles the Java memory for the same result.
