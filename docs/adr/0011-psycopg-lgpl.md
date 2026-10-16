# ADR-0011: psycopg under LGPL-3.0

Status: proposed.

Amends [ADR-0001](0001-clean-room-apache-2.md) (which stays as accepted) for
one case: an LGPL-licensed library that the product needs at run time.

## Context

ADR-0001 allows LGPL dependencies "only as an optional extra". The server
stores its catalogue, curation records and outbox in PostgreSQL through
Django, and Django's PostgreSQL backend works with psycopg 3 or psycopg2
only. Both are licensed LGPL-3.0 (psycopg2: LGPL-3.0 with an exception for
linking with OpenSSL). No PostgreSQL driver under a permissive licence is
supported by Django: pg8000 (BSD-3) and asyncpg (Apache-2.0) have no
maintained Django backend, and a backend of our own would be a large piece
of security-relevant code to own. SQLite is not an option for a multi-process
server with workers. So `psycopg[binary]` is a required dependency of
`oss-server`, not an optional extra, and every installed stack contains it.
A check of the locked dependencies on 2026-10-17 found psycopg and
psycopg-binary 3.3.6 (`LGPL-3.0-only`) as the only LGPL or GPL packages.

## Decision

An LGPL-licensed library may be a required run-time dependency when all of
the following hold:

- no permissively licensed alternative is supported by the framework that
  needs it;
- Semascope uses it unmodified, as a separately distributed package that is
  imported (dynamically linked) at run time, never copied or vendored into
  Semascope's source;
- the user can replace it with another build of the same library (in the
  image, `uv pip install` of another psycopg build, or a rebuilt image from
  the Dockerfile), which the LGPL requires;
- its licence text ships with the package in the image, and it is named in
  NOTICE and in the release SBOM.

psycopg 3 (`psycopg[binary]`) is accepted on these terms. Every other rule of
ADR-0001 stands: GPL and AGPL code never, LGPL otherwise only as an optional
extra, non-OSI licences never.

## Consequences

On acceptance:

- NOTICE names psycopg and its licence, and the release SBOM lists it with
  `LGPL-3.0-only`.
- A licence check of the locked dependencies (for CI, beside the
  vulnerability audit) accepts `LGPL-3.0-only` for `psycopg` and
  `psycopg-binary` and for no other package; any further LGPL dependency
  needs its own ADR.
- The image keeps psycopg as an installed wheel in site-packages that the
  operator can replace; nothing in Semascope patches or bundles it.
- Semascope's own code stays Apache-2.0.

## Alternatives

- **pg8000 with a third-party Django backend**: unmaintained backends, and
  the risk of subtle transaction and type differences in the record of
  curation decisions.
- **psycopg2**: the same licence, and in maintenance mode.
- **An optional extra**: the server cannot run without a PostgreSQL driver,
  so an "optional" driver would only move the dependency to every
  installation's instructions.
