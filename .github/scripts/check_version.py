#!/usr/bin/env python3
"""Check that every place that names the product version agrees, and with the git tag.

Usage:
    uv run python .github/scripts/check_version.py              # files agree with each other
    uv run python .github/scripts/check_version.py --tag v1.0.0-rc.3   # ... and with the tag
    python3 .github/scripts/check_version.py --print   # the version, e.g. 1.0.0-rc.3

The version lives in the Python packages (`packages/*/pyproject.toml`, PEP 440, e.g.
`1.0.0rc3`), in `oss_core.__version__`, in `packages/oss-web/package.json` (semver,
`1.0.0-rc.3`) and as the default image tag in the compose files
(`${SEMASCOPE_IMAGE_TAG:-1.0.0-rc.3}`), which is what `./oss up` runs when the env file
names no tag. The release tag is `v` plus the semver form. A release commit bumps all of
them together (docs/operations.md, "Cutting a release").
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PEP440 = re.compile(r"^(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?$")
PRE = {"a": "alpha", "b": "beta", "rc": "rc"}
IMAGE_DEFAULT = re.compile(r"\$\{SEMASCOPE_IMAGE_TAG:-([^}]*)\}")
COMPOSE_FILES = ("compose.yaml", "compose.gpu.yaml")


def semver(pep440: str) -> str:
    """1.0.0rc3 -> 1.0.0-rc.3; 1.0.0 -> 1.0.0."""

    m = PEP440.match(pep440)
    if not m:
        raise ValueError(f"{pep440!r} is not a release version (X.Y.Z, optionally aN/bN/rcN)")
    base, pre, num = m.groups()
    return f"{base}-{PRE[pre]}.{num}" if pre else base


def found(root: Path) -> dict[str, str]:
    """Every version declaration, keyed by where it was found, in semver form."""

    out: dict[str, str] = {}
    for pyproject in sorted(root.glob("packages/*/pyproject.toml")):
        version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
        out[str(pyproject.relative_to(root))] = semver(version)
    init = root / "packages/oss-core/src/oss_core/__init__.py"
    m = re.search(r'^__version__ = "([^"]+)"', init.read_text(encoding="utf-8"), re.M)
    if m:
        out[str(init.relative_to(root))] = semver(m.group(1))
    web = root / "packages/oss-web/package.json"
    out[str(web.relative_to(root))] = json.loads(web.read_text(encoding="utf-8"))["version"]
    for name in COMPOSE_FILES:
        text = (root / name).read_text(encoding="utf-8")
        for i, default in enumerate(IMAGE_DEFAULT.findall(text)):
            out[f"{name} (image tag default {i + 1})"] = default
    return out


def check(root: Path, tag: str | None) -> list[str]:
    versions = found(root)
    expected = versions.get("packages/oss-core/pyproject.toml") or next(iter(versions.values()))
    problems = [
        f"{where}: {value} (expected {expected})"
        for where, value in versions.items()
        if value != expected
    ]
    if tag is not None:
        tag = tag.removeprefix("refs/tags/")
        if tag != f"v{expected}":
            problems.append(f"git tag {tag} does not match the version v{expected}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tag", help="the release tag being built, e.g. v1.0.0-rc.3")
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--print", action="store_true", help="print only the version (semver form)")
    args = ap.parse_args()
    if args.print:
        print(found(args.root)["packages/oss-core/pyproject.toml"])
        return 0
    problems = check(args.root, args.tag)
    if problems:
        print("version mismatch:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    version = found(args.root)["packages/oss-core/pyproject.toml"]
    print(f"version {version} everywhere" + (f", matching tag {args.tag}" if args.tag else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
