# ADR-0007: The pack contract is the ontology skill's bundle

Status: proposed.

## Decision

A pack is an `ontology` skill project bundle (contract version 2.2.0): the
eleven required files, the `model.yaml` term kinds and support values, and
the `release-manifest.json` hash list. Semascope implements the contract in
`oss_core.pack`; it does not depend on the skill's code. The product's own
base vocabulary (`packs/base`) and the demo pack (`packs/demo/pack`) are such
bundles, and CI validates both with `oss pack validate`, which is the first
integration test of the importer.

The skill's own scripts (`validate_project.py`, `run_cq_tests.py`,
`run_shacl.py`, `release_manifest.py`) are not public and carry no licence,
so CI cannot fetch them and the repository does not vendor them.
`oss pack validate` implements their gates instead:

- the required files, YAML that parses, `ontology.id`, `version` and
  `base_iri`, and RDF that parses;
- a label and a definition on every class and property inside `base_iri`,
  and a `skos:prefLabel` on every SKOS concept;
- `model.yaml` term kinds and support values, a rationale or evidence on
  every accepted term, no accepted `proposed` or `disputed` term, unique term
  and evidence ids, and evidence ids that exist in `evidence.jsonl`;
- the query manifest: a query file and a known expectation type for every
  entry, fixtures inside the pack, a query for every core competency
  question; then the competency questions themselves, each over the dataset
  its entry names, as `run_cq_tests.py` builds it;
- SHACL (pyshacl) over `instances.ttl` with the ontology and taxonomy mixed
  in, as `run_shacl.py` runs it;
- `release-manifest.json` against the files on disk.

Not implemented from the skill's validator: the declaration-hygiene checks
(undeclared custom predicates, SKOS/OWL hierarchy mixing, model/RDF
mismatches), the axiom and mapping metadata checks, and the release-policy
gates. Some checks above are stricter than the skill's validator, which
reports SHACL, competency questions and the manifest through separate
scripts, so the two can disagree in either direction; a pack meant for
Semascope should pass both.

## Consequences

- Authoring happens with the skill or any SKOS/OWL editor. The in-app editor
  emits deltas with `status: draft` and `support: proposed`; it never changes
  an imported pack.
- Every tag carries `pack_id` and `pack_version`; a pack upgrade is a diff
  and a reindex plan, never a silent change. Replacing an active version
  (import with activation, or activation of another version) shows the diff
  and the documents it affects per collection, and proceeds only when
  confirmed.
- A contract change is a minor release of Semascope with a migration.

## Tagging policy

The contract has no notion of "which labels may tag text", and its
`instances.ttl` is defined as fixtures. Semascope therefore reads an optional
top-level `semascope:` block in `requirements.yaml`, which the skill's
validator ignores:

```yaml
semascope:
  tag_schemes: [traditions, themes]   # omit for every scheme; [] for none
  tag_individuals: true               # default false: instances are fixtures
  entity_classes: [Deity, Hero]       # optional: classes offered to zero-shot NER
```

Classes never tag by label. `proposed` and `disputed` terms never tag.
`entity_classes` names classes by local name or label; without it, zero-shot
NER providers are asked for every class of every active pack.

## Implementation

As of the 1.0 hardening (branch `hardening/packs`):

- `oss_core.pack.load_pack` runs every check above; import refuses a pack
  with errors (HTTP 422). A manifest mismatch is an error unless `--force`.
- The four RDF files may be Turtle, N-Triples, RDF/XML or JSON-LD, chosen by
  extension; `shapes` must be Turtle, and JSON-LD must carry its context
  inline.
- `oss pack diff PATH` and `POST /api/knowledge/packs/diff` show the plan;
  `oss pack import` and `oss pack activate` need `--yes` (the API
  `confirm=true`) to replace an active version.
- `oss pack remove ID --version V` (admin) removes a version that is not
  active; documents tagged with it keep their tags until they are reindexed.

The details are in the [pack specification](../assess/pack-spec.md).
