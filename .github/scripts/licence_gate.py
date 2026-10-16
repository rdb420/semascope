#!/usr/bin/env python3
"""Licence gate: fail when a shipped dependency's licence is not covered by the policy.

Usage (from the repository root, after `uv sync --locked --all-packages`):

    uv run python .github/scripts/licence_gate.py --python
    pnpm --dir packages/oss-web licenses list --prod --json > /tmp/web-licences.json
    uv run python .github/scripts/licence_gate.py --pnpm-json /tmp/web-licences.json

The policy is `.github/licence-policy.toml`: SPDX identifiers that are allowed, LGPL
exceptions that an ADR accepts, aliases for free-text licence fields and hand-checked
overrides. GPL and AGPL always fail; LGPL fails unless excepted for that package; any
other licence that the policy does not allow (including none at all) also fails.

Python dependencies are the runtime set of uv.lock (`uv export --no-dev`, workspace
members excluded), read from the installed distributions' metadata: PEP 639
License-Expression first, then the License field, then the trove classifiers.
Dependencies whose environment marker excludes Linux (the image's platform) are skipped.
JavaScript dependencies are the web bundle's production dependencies, as pnpm reports
them.
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / ".github" / "licence-policy.toml"
GPL_FAMILY = re.compile(r"^(A|L)?GPL-", re.IGNORECASE)
TOKEN = re.compile(r"\(|\)|[^\s()]+")


@dataclass
class Policy:
    allowed: set[str]
    exceptions: dict[str, dict[str, str]]  # package -> {licence, adr, reason}
    aliases: dict[str, str]
    overrides: dict[str, str]

    @classmethod
    def load(cls, path: Path) -> Policy:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        exceptions: dict[str, dict[str, str]] = {}
        for item in data.get("exception", []):
            licence = item["licence"]
            if not licence.upper().startswith("LGPL-"):
                raise SystemExit(
                    f"{path}: exception for {item['package']} names {licence}; only LGPL "
                    "licences can be excepted (GPL and AGPL are always refused)"
                )
            if not item.get("adr"):
                raise SystemExit(f"{path}: exception for {item['package']} names no ADR")
            exceptions[norm_name(item["package"])] = item
        return cls(
            allowed={a.lower() for a in data.get("allowed", [])},
            exceptions=exceptions,
            aliases={k.strip().lower(): v for k, v in data.get("alias", {}).items()},
            overrides={norm_name(k): v["licence"] for k, v in data.get("override", {}).items()},
        )


@dataclass
class Verdict:
    ok: bool
    notes: list[str] = field(default_factory=list)


def norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse(expression: str) -> object:
    """Parse an SPDX licence expression into nested ("AND"|"OR", [...]) tuples and ids."""

    tokens: list[str] = TOKEN.findall(expression)
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def take() -> str:
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def atom() -> object:
        if peek() == "(":
            take()
            inner = either()
            if take() != ")":
                raise ValueError(f"unbalanced parentheses in {expression!r}")
            return inner
        tok = peek()
        if tok is None or tok.upper() in ("AND", "OR", "WITH", ")"):
            raise ValueError(f"expected a licence in {expression!r}")
        ident = take()
        if (peek() or "").upper() == "WITH":  # an exception only adds permissions
            take()
            take()
        return ident

    def both() -> object:
        parts = [atom()]
        while (peek() or "").upper() == "AND":
            take()
            parts.append(atom())
        return parts[0] if len(parts) == 1 else ("AND", parts)

    def either() -> object:
        parts = [both()]
        while (peek() or "").upper() == "OR":
            take()
            parts.append(both())
        return parts[0] if len(parts) == 1 else ("OR", parts)

    tree = either()
    if pos != len(tokens):
        raise ValueError(f"trailing text in {expression!r}")
    return tree


def leaves(tree: object) -> list[str]:
    if isinstance(tree, str):
        return [tree]
    assert isinstance(tree, tuple)
    return [leaf for part in tree[1] for leaf in leaves(part)]


def judge(package: str, licence: str, policy: Policy) -> Verdict:
    """Is this licence expression acceptable for this package under the policy?"""

    licence = policy.aliases.get(licence.strip().lower(), licence)
    try:
        tree = parse(licence)
    except ValueError as exc:
        return Verdict(False, [str(exc)])

    def leaf(ident: str) -> Verdict:
        ident = policy.aliases.get(ident.lower(), ident)
        if ident.lower() in policy.allowed:
            return Verdict(True)
        if GPL_FAMILY.match(ident):
            exc = policy.exceptions.get(norm_name(package))
            if ident.upper().startswith("LGPL-") and exc and exc["licence"] == ident:
                return Verdict(True, [f"{ident} accepted by {exc['adr']}"])
            if ident.upper().startswith("LGPL-"):
                return Verdict(False, [f"{ident} needs an ADR exception in the policy"])
            return Verdict(False, [f"{ident} is refused (GPL/AGPL)"])
        return Verdict(False, [f"{ident} is not on the allow-list"])

    def walk(node: object) -> Verdict:
        if isinstance(node, str):
            return leaf(node)
        assert isinstance(node, tuple)
        op, parts = node
        results = [walk(p) for p in parts]
        if op == "AND":
            return Verdict(all(r.ok for r in results), [n for r in results for n in r.notes])
        passing = [r for r in results if r.ok]
        if passing:
            return Verdict(True, passing[0].notes)
        return Verdict(False, [n for r in results for n in r.notes])

    return walk(tree)


def known(expression: str, policy: Policy) -> bool:
    """Does every licence in this expression mean something to the policy?"""

    try:
        ids = leaves(parse(expression))
    except ValueError:
        return False
    for ident in ids:
        ident = policy.aliases.get(ident.lower(), ident)
        if ident.lower() not in policy.allowed and not GPL_FAMILY.match(ident):
            return False
    return True


def python_licence(dist: md.Distribution, policy: Policy) -> tuple[str | None, str]:
    """The licence expression of an installed distribution, and where it came from."""

    meta = dist.metadata
    name = norm_name(meta["Name"])
    if name in policy.overrides:
        return policy.overrides[name], "override"
    if meta.get("License-Expression"):
        return meta["License-Expression"].strip(), "License-Expression"
    text = (meta.get("License") or "").strip()
    if text and text.lower() in policy.aliases:
        return policy.aliases[text.lower()], "License"
    if text and "\n" not in text and known(text, policy):
        return text, "License"
    classifiers = [
        c.split(" :: ")[-1]
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::") and c.split(" :: ")[-1] != "OSI Approved"
    ]
    mapped = [policy.aliases.get(c.strip().lower()) for c in classifiers]
    if classifiers and all(mapped):
        return " AND ".join(dict.fromkeys(m for m in mapped if m)), "classifiers"
    return None, "none"


def gpl_hint(dist: md.Distribution) -> bool:
    meta = dist.metadata
    texts = [meta.get("License") or "", meta.get("License-Expression") or ""]
    texts += [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    return any("GPL" in t.upper() or "GENERAL PUBLIC" in t.upper() for t in texts)


def python_requirements() -> list[tuple[str, str]]:
    """(name, version) of the runtime dependencies uv.lock pins for Linux."""

    from packaging.requirements import Requirement

    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [  # noqa: S607 - uv from PATH, as every CI step and recipe runs it
            "uv",
            "export",
            "--locked",
            "--all-packages",
            "--no-dev",
            "--no-emit-workspace",
            "--no-hashes",
            "--no-annotate",
            "--format",
            "requirements-txt",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    env = {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix"}
    reqs = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        req = Requirement(line)
        if req.marker and not req.marker.evaluate(env):
            continue
        pinned = next((s.version for s in req.specifier if s.operator == "=="), "url")
        reqs.append((req.name, pinned))
    return reqs


def check_python(policy: Policy) -> tuple[list[str], list[str], set[str]]:
    failures: list[str] = []
    passed: list[str] = []
    seen: set[str] = set()
    for name, version in python_requirements():
        seen.add(norm_name(name))
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            failures.append(f"{name}=={version}: not installed; run uv sync --locked first")
            continue
        licence, source = python_licence(dist, policy)
        label = f"{name}=={dist.version}"
        if licence is None:
            failures.append(f"{label}: no licence the policy can read; check it, add an override")
            continue
        verdict = judge(name, licence, policy)
        if verdict.ok and source != "override" and gpl_hint(dist):
            if not any(GPL_FAMILY.match(i) for i in leaves(parse(licence))):
                verdict = Verdict(False, ["metadata mentions the GPL elsewhere; check by hand"])
        line = f"{label}: {licence} ({source})" + (
            f" [{'; '.join(verdict.notes)}]" if verdict.notes else ""
        )
        (passed if verdict.ok else failures).append(line)
    return failures, passed, seen


def check_pnpm(report: Path, policy: Policy) -> tuple[list[str], list[str], set[str]]:
    failures: list[str] = []
    passed: list[str] = []
    seen: set[str] = set()
    data = json.loads(report.read_text(encoding="utf-8"))
    for licence, packages in data.items():
        for pkg in packages:
            name = pkg["name"]
            seen.add(norm_name(name))
            label = f"{name}@{','.join(pkg.get('versions', []))}"
            effective = policy.overrides.get(norm_name(name), licence)
            verdict = judge(name, effective, policy)
            line = f"{label}: {effective}" + (
                f" [{'; '.join(verdict.notes)}]" if verdict.notes else ""
            )
            (passed if verdict.ok else failures).append(line)
    return failures, passed, seen


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--policy", type=Path, default=POLICY)
    ap.add_argument("--python", action="store_true", help="check uv.lock's runtime set")
    ap.add_argument("--pnpm-json", type=Path, help="output of `pnpm licenses list --prod --json`")
    ap.add_argument("-v", "--verbose", action="store_true", help="list every package")
    args = ap.parse_args()
    if not args.python and not args.pnpm_json:
        ap.error("nothing to check: pass --python and/or --pnpm-json")
    policy = Policy.load(args.policy)
    failures: list[str] = []
    seen: set[str] = set()
    for kind, run in (
        ("python", (lambda: check_python(policy)) if args.python else None),
        ("javascript", (lambda: check_pnpm(args.pnpm_json, policy)) if args.pnpm_json else None),
    ):
        if run is None:
            continue
        bad, good, names = run()
        seen |= names
        failures += [f"{kind}: {b}" for b in bad]
        print(f"{kind}: {len(good)} dependencies pass, {len(bad)} fail")
        for line in good if args.verbose else [g for g in good if "accepted by" in g]:
            print(f"  ok   {line}")
    for name in policy.exceptions:
        if args.python and name not in seen:
            print(f"note: the exception for {name} matches no dependency; remove it if stale")
    for line in failures:
        print(f"  FAIL {line}", file=sys.stderr)
    if failures:
        print(
            f"\n{len(failures)} dependencies fail the licence policy ({args.policy}). "
            "See CONTRIBUTING.md, 'Licences we accept for dependencies'.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
