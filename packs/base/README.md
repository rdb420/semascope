# Semascope base knowledge model

Status: **draft** (v0.1.0). Contract: `ontology` skill project bundle 2.2.0.

## What it models

The things every indexed corpus has: `Document`, `Page`, `Chunk`, `Source`,
`Collection`; the generic entity types that pattern extraction and
named-entity recognition produce (`Person`, `Organisation`, `Place`, `Event`,
`EmailAddress`, `PhoneNumber`, `URL`, `MonetaryAmount`, `LegalReference`,
`Hashtag`, all under `Entity`); and the provenance of tags (`Tag`, `Pack`,
`StageRun`). One SKOS scheme, the content type group, is the only vocabulary.

## What it does not model

Any domain vocabulary (other packs), document management semantics (versions,
approvals, retention), and application state (users, roles, permissions).

## Intended use

Retrieval and cataloguing inside Semascope, integration with dcterms, PROV-O
and SKOS consumers, and SHACL validation of extracted metadata.

## Sources and reuse

dcterms for document metadata, PROV-O for tag provenance (a `StageRun` is a
`prov:Activity`, a `Tag` a `prov:Entity`), SKOS for concepts. schema.org
`Person`, `Organization` and `Place` are recorded as `skos:closeMatch`
candidates, not asserted as equivalent (DEC-005).

## Validation

```bash
uv run oss pack validate packs/base
cd packs/base && python3 <ontology-skill>/scripts/validate_project.py . --json validation/validation.json
python3 <ontology-skill>/scripts/run_cq_tests.py . --json validation/cq-results.json
```

Core competency questions: CQ-001 (documents tagged with a concept, with pack
version and stage), CQ-002 (entities of a type in a document), CQ-003 (source
and hash of a document). All three have executable SPARQL tests.

## Unresolved

ISS-001: whether extracted dates need an entity class for the timeline.
