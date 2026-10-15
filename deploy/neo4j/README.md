# Neo4j bootstrap

Constraints and indexes are created idempotently by the `graph` worker at
start-up from the active packs (one uniqueness constraint per node label
key: `Document.id`, `Chunk.id`, `Concept.iri`, `Entity.key`, `Pack.id`,
`Source.uri`). Nothing here is applied by hand. The projection rules live in
`packages/oss-pipeline` and are documented in ADR-0009 (Phase 3).
