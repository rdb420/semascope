"""References to laws, regulations and cases: the base pack's `LegalReference`.

The default patterns, written for this product, find:

- named statutes: `Privacy Act 1988 (Cth)`, `Corporations Regulations 2001`,
  `Residential Tenancies Act`, `Civil Procedure Rules`;
- a provision of one: `section 12(1)(a) of the Privacy Act 1988`, `s 52 of the Competition
  and Consumer Act 2010`;
- European Union instruments: `Regulation (EU) 2016/679`, `Directive 2009/24/EC`;
- the United States Code and Code of Federal Regulations: `42 U.S.C. § 1983`, `17 CFR 240.10b-5`;
- paragraphs of a code by its abbreviation: `§ 823 BGB`, `§§ 433 ff. HGB`, `Art. 3 GG`;
- medium-neutral case citations: `[2020] HCA 5`, `[2019] EWCA Civ 123`.

A collection adds its own patterns with `law_patterns` (a list of RE2 expressions; the
match, or its `value` group, is the reference) and can switch the defaults off with
`law_defaults: false`. Values keep their wording, with runs of white space made single.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_CAP = r"[A-Z][A-Za-z'’-]+"
_JOIN = r"(?:of|and|for|the|on|in|to|&)"
# A statute's title: capitalised words (small joining words allowed between them), then the
# kind of instrument, an optional year and an optional jurisdiction in brackets.
_STATUTE = (
    rf"{_CAP}(?:\s(?:{_JOIN}\s){{0,2}}{_CAP}){{0,8}}"
    r"\s(?:Act|Acts|Regulations?|Ordinance|Code|Rules|Statute|Bill)"
    r"(?:\s(?:19|20)\d{2})?(?:\s\((?:Cth|NSW|Vic|Qld|WA|SA|Tas|ACT|NT|UK|NZ|Scot|Ireland)\))?"
)
_PROVISION = r"(?:section|sections|sec\.|s\.?|ss\.?|clause|rule|regulation|reg\.|article|art\.)"
DEFAULT_PATTERNS: tuple[str, ...] = (
    rf"\b{_PROVISION}\s?\d+[A-Z]{{0,3}}(?:\(\w{{1,4}}\)){{0,4}}\s(?:of|in)\s(?:the\s)?{_STATUTE}",
    rf"\b{_STATUTE}",
    r"\b(?:Regulation|Directive|Decision)\s(?:\((?:EU|EC|EEC|Euratom)\)\s)?(?:No\s)?"
    r"\d{2,4}/\d{1,4}(?:/(?:EU|EC|EEC))?",
    r"\b\d{1,2}\sU\.?S\.?C\.?\s(?:§{1,2}\s?)?\d+[a-z]?(?:\(\w{1,4}\)){0,4}",
    r"\b\d{1,2}\sC\.?F\.?R\.?\s(?:§{1,2}\s?)?\d+(?:\.\d+[a-z]?(?:-\d+)?)?",
    r"(?:§{1,2}|\bArt\.)\s?\d+[a-z]?(?:\s(?:Abs\.|para\.)\s?\d+)?(?:\sff?\.)?\s[A-Z][A-Za-z]{1,6}G?\b",
    r"\[(?:19|20)\d{2}\]\s[A-Z][A-Za-z]{1,8}(?:\s(?:Civ|Crim|Admin|Ch|Fam|QB|KB|Comm))?\s\d{1,5}",
)
_COMPILED = [re.compile(p) for p in DEFAULT_PATTERNS]
_SPACE = re.compile(r"\s+")
# capitalised words that start a sentence rather than a title: `Under the Privacy Act`
STARTERS = frozenset(
    "A According An And As At Before By For From If In Of On Our Pursuant See Since That The "
    "Their These This Those To Under Unlike When Where Whereas While With".split()
)
_KINDS = frozenset("Act Acts Regulation Regulations Ordinance Code Rules Statute Bill".split())


def _trim(value: str, start: int) -> tuple[str, int] | None:
    """The reference without the sentence words in front of a title; None if nothing is left
    of the title (`The Act`)."""

    words = value.split(" ")
    dropped = 0
    while words and (words[0] in STARTERS or words[0].lower() in ("of", "and", "the", "for")):
        start += len(words[0]) + 1
        words = words[1:]
        dropped += 1
    if not words or (dropped and words[0] in _KINDS):
        return None
    return " ".join(words), start


def _overlaps(span: tuple[int, int], taken: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in taken)


def find(
    text: str, extra: Iterable[Any] = (), *, defaults: bool = True
) -> list[tuple[str, int, int]]:
    """(reference, start, end) of each legal reference; the first pattern to claim a passage
    wins, so `section 12 of the Privacy Act 1988` is one reference, not two."""

    found: list[tuple[str, int, int]] = []
    taken: list[tuple[int, int]] = []
    patterns: list[Any] = [*(_COMPILED if defaults else []), *extra]
    for pattern in patterns:
        for m in pattern.finditer(text):
            group = int(dict(getattr(pattern, "groupindex", None) or {}).get("value", 0))
            value = m.group(group)
            if not value:
                continue
            span = (m.start(group), m.end(group))
            if _overlaps(span, taken):
                continue
            trimmed = _trim(_SPACE.sub(" ", value).strip(" ,;."), span[0])
            if trimmed is None:
                continue
            taken.append(span)
            found.append((trimmed[0], trimmed[1], span[1]))
    return sorted(found, key=lambda f: f[1])
