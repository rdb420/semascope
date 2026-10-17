# ADR-0015: PST mailboxes through libpff, as an optional extra

Status: proposed, 2026-10-17. Applies ADR-0001's rule for LGPL libraries.

## Context

Outlook keeps mail in PST and OST files. The `pst` extra of `oss-pipeline`
named the PyPI package `pypff`, which is an unrelated pure-Python package
pulling in astropy, dask and zarr, and no code used it (SEMA-066, O-56).

Licences checked on 2026-10-17:

| Option | Licence | Usable |
|---|---|---|
| `libpff-python` (libyal libpff, module `pypff`), wheels for Linux x86-64 and arm64 | LGPL-3.0-or-later (package metadata `License-Expression`) | as an optional extra only (ADR-0001) |
| `readpst` (libpst) as an external program | GPL (libpst's licence; not used, so not checked further) | no: ADR-0001 and ADR-0011 allow no GPL code, and the image would ship the program |
| `extract-msg` (single `.msg` messages) | GPL (PyPI metadata, 0.56.1) | no |
| Apache Tika's Outlook message parser (Apache POI) | Apache-2.0 | already in the stack: `.msg` files from any file source are parsed by Tika, with their attachments' text |
| A pure-Python PST reader under a permissive licence | none found | — |

## Decision

- The `pst` extra installs `libpff-python`. Semascope imports it unmodified
  and dynamically, only in `oss_pipeline.connectors.pst`, and never vendors
  or patches it.
- The extra is not installed by default and not in the published image. The
  licence gate (`.github/licence-policy.toml`) checks the locked runtime set
  without extras, so it stays free of LGPL apart from psycopg (ADR-0011). An
  operator who wants PST builds an image with the extra
  (`uv sync --extra pst`); the LGPL then requires that the library can be
  replaced, which an installed wheel allows.
- Without the extra, a `pst` source fails its run with a message naming the
  extra; nothing is deleted.
- Single `.msg` messages need no extra: Tika parses them.

## Consequences

- PST support is real but opt-in. The integration stack does not exercise
  it; the unit tests use a stand-in with libpff's interface, and check that
  interface against the real binding when the extra is installed.
- Shipping the extra in the default image would need its own ADR, as
  ADR-0011 did for psycopg, and a licence-policy exception.

## Alternatives

- **Tika for PST**: one `/rmeta` request returns every message of a mailbox
  in one JSON answer, which does not scale to multi-gigabyte PSTs; Tika's
  unpacker does not give back messages as RFC 822 files.
- **Writing a PST reader**: the format (NDB B-trees, heap-on-node property
  and table contexts) is documented by Microsoft ([MS-PST]) but large; not
  worth it before 1.0.
