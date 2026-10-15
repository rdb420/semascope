#!/usr/bin/env python3
"""Clean-room check: flag files that look copied from a GPL legacy tree.

Usage: cleanroom_check.py --legacy <path-to-legacy-checkout> [--threshold 0.6]

Compares every Python, XML, PHP, JavaScript and CSS file in this repository
with every file of the same kind in the legacy tree using a normalised
token-shingle Jaccard similarity. Anything above the threshold fails the run.
In CI the legacy path is a scratch checkout of github.com/opensemanticsearch
that is deleted afterwards; it is never part of this repository.
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
    args = ap.parse_args()
    if args.legacy is None:
        print("no --legacy path given; nothing compared")
        return 0
    ours = collect(args.repo)
    theirs = collect(args.legacy)
    worst = 0.0
    failures = 0
    for mine, a in ours.items():
        for other, b in theirs.items():
            if mine.suffix != other.suffix:
                continue
            inter = len(a & b)
            if inter == 0:
                continue
            score = inter / len(a | b)
            worst = max(worst, score)
            if score >= args.threshold:
                failures += 1
                mine_rel = mine.relative_to(args.repo)
                other_rel = other.relative_to(args.legacy)
                print(f"FAIL {score:.2f} {mine_rel} ~ {other_rel}")
    print(f"compared {len(ours)} files against {len(theirs)}; worst similarity {worst:.2f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
