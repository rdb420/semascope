## What and why

<!-- What this changes, and the issue, finding (SEMA-nnn) or ADR it answers. -->

## How it was tested

<!-- Commands you ran and what they showed. A change without tests is not merged. -->

## Checklist

- [ ] **Clean room (required).** I confirm that no part of this change was copied,
      pasted, ported or translated line by line from Open Semantic Search or any other
      GPL-licensed project (code, Solr configsets, templates, stylesheets, scripts,
      images or documentation text), and that it adds no such project as a
      submodule, vendored file or build-time download. Behaviour described in words
      and reimplemented from that description is fine.
      See [CONTRIBUTING.md](../CONTRIBUTING.md#clean-room-rule).
- [ ] New or changed dependencies are under a licence the policy accepts
      (`.github/licence-policy.toml`; `just licences` passes), or this PR links the ADR
      that accepts them.
- [ ] `just check` passes (ruff, mypy, pytest), and `just web` if the UI changed.
- [ ] A contract change in `packages/oss-core` has an ADR in `docs/adr/`.
- [ ] `CHANGELOG.md` has an entry under *Unreleased* for anything a user or operator
      would notice.
