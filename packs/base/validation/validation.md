# Validation summary

Generated 2026-10-15 with the `ontology` skill scripts (skill v2.2.0) and `oss pack validate`.
Run from the pack directory: `validate_project.py . --json validation/validation.json`.

- validate_project: {'project': '/mnt/development/dev/projects/semascope/packs/base', 'pass': True}
- run_cq_tests: {'project': '/mnt/development/dev/projects/semascope/packs/base', 'pass': True, 'test_count': 3, 'blocking_failures': 0}
- run_shacl (pyshacl): conforms=True
- No external OWL reasoner was run; `release_policy.require_external_reasoner` is false.
- `oss pack validate packs/base`: ok, 17 files hashed into release-manifest.json.

Status: draft. The pack becomes a release candidate when the Phase 2 importer round-trips it.
