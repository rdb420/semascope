# Demo corpus and demo pack

`build.py` fetches sixteen public-domain sacred and mythological texts from
Project Gutenberg, strips the licence header, footer and trademark as that
licence requires, and writes them to `corpus/` (gitignored). `sources.yaml`
lists every text with its translator and death year, and after a build also
the download date and hash. See ADR-0005 for why these texts and not the
Internet Sacred Text Archive.

The corpus is pinned. `build.py` fails when a text no longer matches the hash in
`sources.yaml` (pass `--accept-changes` to record a new version), and writes
`corpus.sha256`, whose own hash is the corpus manifest hash that benchmark
reports cite. Check a built corpus with `cd packs/demo && sha256sum -c corpus.sha256`.

`derive.py` (`just mixed-1k`) cuts the texts into about 1,000 chapters and
renders each one in one of twelve formats (HTML, DOCX, ODT, EPUB, PDF with a
text layer, scanned PDF without one, PNG page image, e-mail, mbox, CSV,
Turtle, zip) into `derived/mixed-1k/` (gitignored), deterministically: the
same texts and the pinned Pillow give byte-identical files, which
`mixed-1k.sha256` pins. `derived/mixed-1k.index.csv` says which chapter each
file holds. This is the `mixed-1k` corpus of the
[benchmark protocol](../../docs/assess/benchmarks.md).

`pack/` (Phase 2) is the demo knowledge model: traditions, text types and
themes as SKOS schemes; deities, heroes, prophets, places, texts and motifs as
classes and individuals with alternative and hidden labels; SHACL shapes; and
competency questions that run at import.
