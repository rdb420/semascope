# Assessing Semascope

Status: **draft**, updated 2026-10-17. Describes 1.0.0-rc.3 (unreleased: the
main line after 1.0.0-rc.2).

These pages exist so that someone deciding whether to adopt Semascope can
check its claims rather than take them on trust. Each one says what the
code does today, what evidence backs it, and what is missing.

| Page | Answers |
|---|---|
| [Capability matrix](capabilities.md) | Which inputs, enrichment functions, UI surfaces, graph operations, review actions and export targets are implemented, experimental or planned, and what tests back each one |
| [Pack specification](pack-spec.md) | What a pack is on disk, how its SKOS, OWL, SHACL and named individuals are read, how versions and conflicts work, and what validation checks |
| [Explainability model](explainability.md) | What is recorded for every document–concept and document–entity link (span, method, confidence, pack version, reviewer decisions) and where |
| [Deployment guide](deployment.md) | Supported platforms, memory and storage, dependencies, authentication, TLS, backups, updates and offline operation |
| [Coming from Open Semantic Search](migration-from-oss.md) | How Semascope relates to Open Semantic Search, and how to move a corpus across |
| [Benchmarks](benchmarks.md) | The repeatable benchmark protocol, and which parts of it have results so far |
| [Decisions](decisions.md) | Each product decision raised by the review of 1.0.0-rc.2: what Semascope chose and where it is recorded, or that the owner has still to decide |
| [Walkthrough](walkthrough.md) | One piece of evidence followed from ingestion to the entity page, graph, review queue and exported decision |

Where a page lists a gap, the gap is real in the code these pages describe.
The aim is to close or re-scope each one before 1.0, and to update these pages
in the same change. [Decisions](decisions.md) lists every product decision
the review of 1.0.0-rc.2 raised, with what was chosen or that it is still
open.
