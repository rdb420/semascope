#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6,<7"]
# ///
"""Derive the demo corpus's relevance judgements from the corpus text.

Reads `judgement-queries.yaml` (each query's text and the names that show a
text is about it), counts those names in every text of `corpus/` (whole words,
any case) and grades each text by a fixed rule:

    3  highly relevant   at least 20 mentions, and at least 100 of them or at
                         least 20 per 100,000 characters (a short text that is
                         about the subject, like the Dhammapada and the Buddha)
    2  relevant          at least 20 mentions
    1  partly relevant   5 to 19 mentions
    0  not relevant      fewer than 5 (left out of the file)

It then checks the grades against `golden.yaml`: every text that golden.yaml
expects to be tagged with a query's `golden` individual must be graded at
least 1, and every text that lists it as absent must be graded 0. A failed
check stops the script, so the judgements cannot quietly disagree with the
tagging gold standard.

The output, `judgements.yaml`, is the file `oss eval` reads; it records the
mention counts behind every grade. Run after `build.py`:

    uv run packs/demo/judge.py            # check and print
    uv run packs/demo/judge.py --write    # also rewrite judgements.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
PER = 100_000  # characters, for the density rule


def grade(count: int, chars: int) -> int:
    density = count * PER / max(chars, 1)
    if count >= 20 and (count >= 100 or density >= 20):
        return 3
    if count >= 20:
        return 2
    if count >= 5:
        return 1
    return 0


def mentions(text: str, terms: list[str]) -> int:
    return sum(
        len(re.findall(r"\b" + re.escape(t) + r"\b", text, flags=re.IGNORECASE)) for t in terms
    )


def judge(
    queries: list[dict[str, Any]], corpus: dict[str, str], golden: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    out: list[dict[str, Any]] = []
    problems: list[str] = []
    for q in queries:
        counts = {slug: mentions(text, q["terms"]) for slug, text in corpus.items()}
        grades = {slug: grade(n, len(corpus[slug])) for slug, n in counts.items()}
        ranked = sorted(grades.items(), key=lambda x: -x[1])
        relevant = {f"{slug}.txt": g for slug, g in ranked if g}
        evidence = {f"{slug}.txt": n for slug, n in sorted(counts.items()) if n}
        for entity in q.get("golden", []):
            for slug, spec in golden.items():
                if slug not in corpus:
                    continue
                expected = entity in (spec.get("entities") or [])
                absent = entity in (spec.get("absent") or [])
                if expected and not grades[slug]:
                    problems.append(f"{q['id']}: golden.yaml tags {slug} with {entity}, graded 0")
                if absent and grades[slug]:
                    problems.append(f"{q['id']}: golden.yaml says {entity} is absent from {slug}")
        out.append(
            {
                "id": q["id"],
                "query": q["query"],
                "relevant": relevant,
                "evidence": {"terms": q["terms"], "mentions": evidence},
            }
        )
    return out, problems


def pinned_manifest(corpus_dir: Path) -> str:
    """The hash of corpus.sha256 when every text matches it (the hash benchmark reports
    cite), else "unpinned"."""

    pins = HERE / "corpus.sha256"
    for line in pins.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        path = corpus_dir / Path(name).name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            print(f"warning: {path.name} does not match corpus.sha256", file=sys.stderr)
            return "unpinned"
    return hashlib.sha256(pins.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", type=Path, default=HERE / "corpus")
    ap.add_argument("--write", action="store_true", help="rewrite judgements.yaml")
    args = ap.parse_args()
    spec = yaml.safe_load((HERE / "judgement-queries.yaml").read_text(encoding="utf-8"))
    golden = yaml.safe_load((HERE / "golden.yaml").read_text(encoding="utf-8"))["texts"]
    corpus = {p.stem: p.read_text(encoding="utf-8") for p in sorted(args.corpus.glob("*.txt"))}
    if not corpus:
        print(f"no texts in {args.corpus}; run build.py first", file=sys.stderr)
        return 2
    manifest = pinned_manifest(args.corpus)
    judged, problems = judge(spec["queries"], corpus, golden)
    for q in judged:
        grades = ", ".join(f"{k.removesuffix('.txt')} {v}" for k, v in q["relevant"].items())
        print(f"{q['id']}  {q['query']!r}: {grades or 'nothing relevant'}")
    if problems:
        print("\n".join(["", "disagrees with golden.yaml:", *problems]), file=sys.stderr)
        return 1
    if args.write:
        doc = {
            "format": "semascope-judgements/1",
            "name": "demo",
            "collection": "demo",
            "k": 10,
            "grades": {
                3: "highly relevant: the text is about it",
                2: "relevant: the text discusses it",
                1: "partly relevant: the text mentions it",
            },
            "provenance": {
                "derived_by": "packs/demo/judge.py from judgement-queries.yaml and golden.yaml",
                "corpus_texts": len(corpus),
                "corpus_manifest_sha256": manifest,
                "rule": "mentions of the query's terms per text: 3 if >= 20 and (>= 100 or "
                ">= 20 per 100k characters), 2 if >= 20, 1 if >= 5",
            },
            "queries": judged,
        }
        header = (
            "# Relevance judgements for the demo corpus, generated by packs/demo/judge.py.\n"
            "# Do not edit by hand: change judgement-queries.yaml and rerun the script.\n"
            "# `relevant` keys match the end of a document's URI (or its id or full URI).\n"
        )
        text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)
        (HERE / "judgements.yaml").write_text(header + text, encoding="utf-8")
        print("wrote judgements.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
