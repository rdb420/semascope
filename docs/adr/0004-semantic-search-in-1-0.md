# ADR-0004: Semantic search in 1.0

Status: accepted, 2026-10-15.

## Decision

Dense embeddings, a `chunks` collection and hybrid search (BM25 plus kNN
fused with reciprocal rank fusion) are part of 1.0, delivered in Phase 4.

## Context

A product called a semantic search without vector search would not meet
expectations in 2026. The cost is bounded: FastEmbed (Apache-2.0, ONNX) with a
small default model runs on CPU and the model is baked into the image.

## Consequences

The `Embedder` protocol allows an external OpenAI-compatible `/embeddings`
endpoint to replace the bundled model. Sparse learned vectors are deferred.
