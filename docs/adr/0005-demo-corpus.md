# ADR-0005: Public-domain demo corpus from Project Gutenberg

Status: accepted, 2026-10-15.

## Decision

The demo corpus is a set of sacred and mythological texts fetched from
Project Gutenberg at build time by `packs/demo/build.py`, with the Project
Gutenberg header, footer and trademark removed as its licence requires for
redistribution. The texts are not committed; their ids, titles, translators,
download dates and hashes are recorded in `packs/demo/sources.yaml`.

## Context

The Internet Sacred Text Archive was considered first. Its terms of service
(read 2026-10-15) allow non-commercial copying only, require a paid licence
for commercial use of its texts, and limit robots to one text per day. An
Apache-2.0 repository grants commercial use, so bundling those files would
breach the terms. The same public-domain works exist on Project Gutenberg.

Every author and translator in the list died before 1956, so the works are
public domain under life-plus-70 jurisdictions as well as in the United
States. The translation is the copyrightable work, so translators are listed.

## Consequences

- The builder also generates derived formats (HTML, EPUB, DOCX, ODT, PDF with
  and without a text layer, page images, zip, CSV, mbox, Turtle) from the same
  texts so every extraction path has demo data without a second licence.
- The demo pack (`packs/demo/pack`) models traditions, text types, themes,
  figures, places and motifs so facets, the graph, the map and the review
  queue have content on first run.
