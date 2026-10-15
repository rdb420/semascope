# Concepts

## Collection

A named corpus inside one Semascope deployment: its sources, its packs, its
pipeline profile and who may read or curate it. One Solr collection and one
Neo4j database serve every collection, with the collection name as a filter.

## Source

One configured connector instance inside a collection: a folder, a site, a
feed, a mailbox, a bucket, an RDF endpoint. Sources are discovered
incrementally with a cursor, and deletions are honoured.

## Pack

A knowledge model packaged as an `ontology` skill project bundle:
`requirements.yaml`, `model.yaml`, `ontology.ttl` (OWL classes and
properties), `taxonomy.ttl` (SKOS schemes and concepts), `shapes.ttl` (SHACL),
`instances.ttl` (named individuals), `queries/` (competency questions),
evidence and decisions, and a `release-manifest.json` of content hashes.
Semascope imports, validates, versions, diffs and exports packs; it never
edits one silently. SKOS concepts and individuals tag text; OWL classes become
entity types, facet families and graph labels; shapes validate extracted
metadata; competency questions run at import.

## Profile

An ordered list of pipeline stages for a collection, validated as a DAG of
`requires` and `provides`. `default`, `fast` and `full` ship with the
product.

## Stage

One step of the pipeline (`extract`, `ocr`, `tag`, `ner`, `embed`, `index`,
`graph`, ...), declared with the queue it runs on and the fields it reads and
writes. Stages are plugins registered through entry points.

## Proposal

The record of a curation decision: accept or reject a machine tag, add a
synonym, propose a concept, merge entities, add a note. Proposals are applied
to Semascope's own stores at once and exported through the outbox so an
external system of record can mirror, reject or audit them.
