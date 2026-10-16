#!/usr/bin/env python3
"""Clean-room check: flag files that look copied from a GPL legacy tree.

Usage: cleanroom_check.py --legacy <path-to-legacy-checkout> [--threshold 0.6]
                          [--min-legacy-files N]

Compares every Python, XML, PHP, JavaScript and CSS file in this repository
with every file of the same kind in the legacy tree using a normalised
token-shingle Jaccard similarity. Anything above the threshold fails the run.
In CI the legacy path is a scratch checkout of github.com/opensemanticsearch
at pinned commits (scripts/fetch_legacy.sh), outside the repository and
deleted with the runner; it is never part of this repository. A legacy path
that is missing or holds fewer than --min-legacy-files comparable files is an
error, and so is running without --legacy at all, so the check cannot pass by
comparing against nothing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

KINDS = {".py", ".xml", ".php", ".js", ".ts", ".tsx", ".css"}
TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def shingles(path: Path, n: int = 8) -> set[tuple[str, ...]]:
    tokens = TOKEN.findall(path.read_text(encoding="utf-8", errors="ignore"))
    return {tuple(tokens[i : i + n]) for i in range(max(0, len(tokens) - n + 1))}


def collect(root: Path) -> dict[Path, set[tuple[str, ...]]]:
    out: dict[Path, set[tuple[str, ...]]] = {}
    for file in root.rglob("*"):
        if (
            file.suffix in KINDS
            and file.is_file()
            and ".venv" not in file.parts
            and "node_modules" not in file.parts
        ):
            sh = shingles(file)
            if len(sh) >= 20:
                out[file] = sh
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--legacy", type=Path, help="legacy checkout to compare against")
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--threshold", type=float, default=0.6)
    ap.add_argument(
        "--min-legacy-files",
        type=int,
        default=1,
        help="fail unless the legacy tree has at least this many comparable files",
    )
    args = ap.parse_args()
    if args.legacy is None:
        print(
            "ERROR no --legacy path given; nothing would be compared "
            "(scripts/fetch_legacy.sh fetches the pinned legacy tree)",
            file=sys.stderr,
        )
        return 2
    if not args.legacy.is_dir():
        print(f"ERROR legacy path {args.legacy} is not a directory", file=sys.stderr)
        return 2
    ours = collect(args.repo)
    theirs = collect(args.legacy)
    if len(theirs) < max(1, args.min_legacy_files):
        print(
            f"ERROR only {len(theirs)} comparable legacy files in {args.legacy} "
            f"(need {args.min_legacy_files}); nothing meaningful was compared",
            file=sys.stderr,
        )
        return 2
    worst = 0.0
    failures = 0
    top: list[tuple[float, str, str]] = []
    for mine, a in ours.items():
        for other, b in theirs.items():
            if mine.suffix != other.suffix:
                continue
            inter = len(a & b)
            if inter == 0:
                continue
            score = inter / len(a | b)
            worst = max(worst, score)
            top.append(
                (score, str(mine.relative_to(args.repo)), str(other.relative_to(args.legacy)))
            )
            if score >= args.threshold:
                failures += 1
                mine_rel = mine.relative_to(args.repo)
                other_rel = other.relative_to(args.legacy)
                print(f"FAIL {score:.2f} {mine_rel} ~ {other_rel}")
    for score, mine_rel, other_rel in sorted(top, reverse=True)[:5]:
        print(f"  {score:.2f} {mine_rel} ~ {other_rel}")
    print(
        f"compared {len(ours)} files against {len(theirs)}; worst similarity {worst:.2f} "
        f"(threshold {args.threshold:.2f})"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
