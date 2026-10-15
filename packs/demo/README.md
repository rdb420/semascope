# Demo corpus and demo pack

`build.py` fetches sixteen public-domain sacred and mythological texts from
Project Gutenberg, strips the licence header, footer and trademark as that
licence requires, and writes them to `corpus/` (gitignored). `sources.yaml`
lists every text with its translator and death year, and after a build also
the download date and hash. See ADR-0005 for why these texts and not the
Internet Sacred Text Archive.

`pack/` (Phase 2) is the demo knowledge model: traditions, text types and
themes as SKOS schemes; deities, heroes, prophets, places, texts and motifs as
classes and individuals with alternative and hidden labels; SHACL shapes; and
competency questions that run at import.
