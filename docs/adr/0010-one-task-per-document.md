# ADR-0010: One task per document, two queues

Status: proposed, 2026-10-16.

## Decision

A document is processed by one Celery task that runs its collection's whole
profile in one worker process. There are two queues: `cpu` for the document
tasks (`process_document`, `reindex_document`) and `io` for the light tasks
(discovery, deletes, graph re-projection, the scheduler tick, recovery,
webhooks and alerts). Stages no longer declare a queue: `queue` is removed
from the `Stage` protocol in `oss_core.contracts`, and the `ocr`, `nlp` and
`graph` queues are gone from the Celery app, from `oss-worker --queues` and
from `compose.yaml`.

## Context

Up to 1.0.0-rc.3 every stage declared one of five queues (`io`, `cpu`,
`ocr`, `nlp`, `graph`) and the worker listened on all five, but nothing
routed by them: `process_document` ran the whole profile in a `cpu` task and
no task used `ocr`, `nlp` or `graph`. The declarations suggested a split of
work, and a way to scale OCR or NER separately, that did not exist.

Routing stages for real would mean a chain of tasks per document, with the
working document (text, chunks, embeddings) passed through Valkey or stored
between stages, and a failure and retry model per hop. Measured on
`mixed-1k` (docs/quality.md), the whole profile takes about a second per
document on CPU, and book-length documents spend their time in embedding and
NER, which a separate queue would not make faster on the same host. The
remote runtime already moves the heavy models off the worker.

## Consequences

- What the queues say is what happens; an operator scales by worker
  processes (`oss init --size`) and by the runtime, not by queue.
- Light tasks never wait behind a backlog of documents, because they have
  their own queue.
- A third-party stage that still sets `queue` keeps working: the attribute
  is ignored. The built-in `tag`, `ner`, `link` and `patterns` stages still
  carry it until their next change.
- Splitting stages across tasks later is still possible; it would come
  with its own ADR and a design for passing the working document.

## Alternatives

- Route each stage to its queue with a Celery chain: rejected for 1.0, see
  the context.
- Keep the declarations as documentation of a stage's cost: rejected,
  because a field named `queue` reads as routing.
