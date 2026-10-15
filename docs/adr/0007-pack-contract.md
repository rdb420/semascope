# ADR-0007: The pack contract is the ontology skill's bundle

Status: proposed.

## Decision

A pack is an `ontology` skill project bundle (contract version 2.2.0): the
eleven required files, the `model.yaml` term kinds and support values, and
the `release-manifest.json` hash list. Semascope implements the contract in
`oss_core.pack`; it does not depend on the skill's code. The product's own
base vocabulary (`packs/base`) is such a bundle and is validated with the
skill's scripts in CI, which is the first integration test of the importer.

## Consequences

- Authoring happens with the skill or any SKOS/OWL editor. The in-app editor
  emits deltas with `status: draft` and `support: proposed`; it never changes
  an imported pack.
- Every tag carries `pack_id` and `pack_version`; a pack upgrade is a diff
  and a reindex plan, never a silent change.
- A contract change is a minor release of Semascope with a migration.
