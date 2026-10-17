# Architecture decision records

One file per decision, numbered. An accepted record is never edited:
a later decision supersedes or amends it in a record of its own. A
proposed record may still change; when it does after it was first
written, it says so in a dated "Amended" note. Template: context,
decision, consequences, alternatives.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-clean-room-apache-2.md) | Clean-room implementation under Apache-2.0 | accepted 2026-10-15; amended for psycopg by 0011 (proposed) |
| [0002](0002-name-and-home.md) | Name and home | accepted 2026-10-15 |
| [0003](0003-solr-10.md) | Apache Solr 10 as the search engine | accepted 2026-10-15 |
| [0004](0004-semantic-search-in-1-0.md) | Semantic search in 1.0 | accepted 2026-10-15 |
| [0005](0005-demo-corpus.md) | Public-domain demo corpus from Project Gutenberg | accepted 2026-10-15 |
| [0006](0006-stack.md) | The rest of the stack | proposed; amended 2026-10-17 |
| [0007](0007-pack-contract.md) | The pack contract is the ontology skill's bundle | proposed |
| [0008](0008-runtimes-and-providers.md) | Runtimes and per-stage model providers | accepted 2026-10-16 |
| [0009](0009-explainability-record.md) | Every tag carries its own explanation | proposed |
| [0010](0010-one-task-per-document.md) | One task per document, two queues | proposed |
| [0011](0011-psycopg-lgpl.md) | psycopg under LGPL-3.0 (amends 0001) | proposed |
| [0012](0012-never-delivered-capabilities.md) | Capabilities Open Semantic Search promised and never delivered | proposed |
| [0013](0013-deletion-and-retention.md) | Deletion and retention of source documents | proposed |
| [0014](0014-ocr-later.md) | OCR now, later or off | proposed |
| [0015](0015-pst-libpff-extra.md) | PST mailboxes through libpff, as an optional extra | proposed |
| [0016](0016-original-file-download.md) | Original files are served by the API, per collection | proposed |

Known departures from the rule above, kept visible rather than rewritten:

- ADR-0008 gained its paragraph on the CUDA EULA (the GPU image's licences)
  21 minutes after it was accepted, in the same release (`a98a601`).
- Some statements in accepted records no longer describe the code, and are
  left as written until a superseding record is accepted: ADR-0001's
  "LGPL only as an optional extra" (see 0011); ADR-0003's `IndexBackend`
  protocol "so a contributor can add another engine" (the protocol exists,
  but nothing implements or calls it, so a second engine would need more
  than the protocol); ADR-0005's derived formats come from
  `packs/demo/derive.py`, not from `build.py`.

Product decisions that have no record of their own yet are listed, with
what was chosen and where it is documented, in
[Decisions](../assess/decisions.md).
