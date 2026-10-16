# Neo4j bootstrap

Constraints are created idempotently by the `graph` stage the first time it
runs in a worker process (one uniqueness constraint per node label key:
`Document.id`, `Chunk.id`, `Concept.iri`, `Entity.key`, `Pack.key`,
`Source.uri`, `Collection.slug`; see `CONSTRAINTS` in
`packages/oss-pipeline/src/oss_pipeline/clients/neo4j.py`). Nothing here is
applied by hand. The projection rules are in the docstring of
`packages/oss-pipeline/src/oss_pipeline/stages/graph.py` and in
[Concepts](../../docs/concepts.md#graph).
