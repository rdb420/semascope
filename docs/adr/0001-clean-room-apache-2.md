# ADR-0001: Clean-room implementation under Apache-2.0

Status: accepted, 2026-10-15.

## Context

Open Semantic Search (opensemanticsearch.org) is the only open-source product
that combined a SKOS thesaurus, dictionary tagging, hierarchical facets, OCR
and a faceted exploration UI. Its last functional change was in October 2022,
it has no releases, and every component is GPL-3.0 (checked on 2026-10-15: the
top-level `LICENSE` and the `LICENSE` of the ETL, the Django apps, the entity
API, the ontology tagger, the PHP UI, the spaCy service, the graph explorer,
synonames and the ranking analysis; only the vendored cytoscape.js is MIT and
tika-python Apache-2.0). Its design (generated code that is `exec()`ed,
state changes over GET, a PHP UI, Solr 8 configsets) is not one to carry
forward.

## Decision

Semascope is a new implementation that copies nothing from that project:
no code, configset, template, stylesheet, script, image or documentation
text, and no submodule or build-time download of it. Behaviour is described in
words and reimplemented. The product is licensed under Apache-2.0, which
matches Solr, Tika, the Neo4j driver and most of the stack and lets
organisations adopt it.

## Consequences

- CI runs `scripts/cleanroom_check.py` against a scratch checkout of the
  legacy tree and fails on token-shingle similarity above the threshold.
- Dependencies must be Apache-2.0, MIT, BSD, ISC, MPL-2.0, PSF or public
  domain. LGPL only as an optional extra. Non-OSI licences never.
- The NOTICE file credits Open Semantic Search as the inspiration.

## Alternatives

Forking under GPL-3.0 would have kept the 2022 design and the copyleft
obligation, and left the name question unresolved.
