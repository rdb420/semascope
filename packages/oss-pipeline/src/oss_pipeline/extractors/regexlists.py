"""User regex lists: a collection's named regular expressions, each filling a facet.

`settings.pipeline.regex_lists` names the lists, either as a mapping or as TSV text:

    {"case_number": ["\\bCASE-\\d{4}-\\d{3,5}\\b"], "ticket": "TCK-\\d+"}

    case_number<TAB>\\bCASE-\\d{4}-\\d{3,5}\\b
    ticket<TAB>TCK-\\d+

Each value a list finds goes in the custom field `x_<name>_ss`, indexed and faceted as
`<name>_ss`: the whole match, or the group named `value` (else the first group) when the
expression has one. Expressions are RE2 (github.com/google/re2/wiki/Syntax): matching
takes time linear in the text, so no expression can backtrack for ever; look-around and
back-references are not available, and `\\w` and `\\b` are ASCII (use `\\pL` for letters
of any script). A list name is lower-case letters, digits and `_` (at most 40) and may not
name one of the index's own fields; an expression may be at most 1,000 characters, a
collection may have 50 expressions, and each keeps at most 200 values per document.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
MAX_PATTERN_CHARS = 1000
MAX_PATTERNS = 50
MAX_VALUES = 200
MAX_VALUE_CHARS = 200
MAX_MATCHES = 10_000  # occurrences kept per list and document (for spans)
RE2_MAX_MEM = 8 << 20  # bytes of automaton memory per expression


class RegexListError(ValueError):
    """A regex list a collection may not have: the reason names the list."""


def _items(raw: Any) -> Iterator[tuple[str, str]]:
    """(name, expression) pairs from a mapping or TSV text."""

    if isinstance(raw, dict):
        for name, value in raw.items():
            values = value if isinstance(value, list) else [value]
            for v in values:
                if not isinstance(v, str):
                    raise RegexListError(f"regex list {name}: expressions are strings")
                yield str(name), v
    elif isinstance(raw, str):
        for n, line in enumerate(raw.splitlines(), start=1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            name, tab, expression = line.partition("\t")
            if not tab or not expression:
                raise RegexListError(f"regex lists line {n}: expected <name><TAB><expression>")
            yield name.strip(), expression
    elif raw is not None:
        raise RegexListError("regex_lists is a mapping of names to expressions, or TSV text")


def compile_lists(raw: Any) -> dict[str, list[Any]]:
    """The lists as compiled RE2 expressions by name; RegexListError when one is not allowed."""

    import re2

    from oss_pipeline.stages.index import custom_field_name

    options = re2.Options()
    options.log_errors = False
    options.max_mem = RE2_MAX_MEM
    out: dict[str, list[Any]] = {}
    count = 0
    for name, expression in _items(raw):
        if not NAME.fullmatch(name) or custom_field_name(f"x_{name}_ss") is None:
            raise RegexListError(f"regex list name {name!r}: lower-case letters, digits and _")
        count += 1
        if count > MAX_PATTERNS:
            raise RegexListError(f"at most {MAX_PATTERNS} expressions per collection")
        if len(expression) > MAX_PATTERN_CHARS:
            raise RegexListError(f"regex list {name}: an expression is {MAX_PATTERN_CHARS} chars")
        try:
            out.setdefault(name, []).append(re2.compile(expression, options))
        except re2.error as exc:
            reason = exc.args[0] if exc.args else exc
            if isinstance(reason, bytes):
                reason = reason.decode("utf-8", "replace")
            raise RegexListError(f"regex list {name}: {reason}") from exc
    return out


def problems(raw: Any) -> list[str]:
    """Why a collection's regex lists are refused (empty when they are fine)."""

    try:
        compile_lists(raw)
    except RegexListError as exc:
        return [str(exc)]
    return []


def value_group(pattern: Any) -> int:
    """The group that holds the value: `value`, else the first group, else the whole match."""

    named = dict(pattern.groupindex or {})
    if "value" in named:
        return int(named["value"])
    return 1 if pattern.groups else 0


def find(lists: dict[str, list[Any]], text: str) -> dict[str, list[tuple[str, int, int]]]:
    """For each list, its (value, start, end) matches, at most MAX_VALUES distinct values."""

    out: dict[str, list[tuple[str, int, int]]] = {}
    for name, patterns in lists.items():
        seen: set[str] = set()
        found: list[tuple[str, int, int]] = []
        for pattern in patterns:
            group = value_group(pattern)
            for m in pattern.finditer(text):
                if len(found) >= MAX_MATCHES:
                    break
                value = (m.group(group) or "").strip()[:MAX_VALUE_CHARS]
                if not value:
                    continue
                if value not in seen:
                    if len(seen) >= MAX_VALUES:
                        break
                    seen.add(value)
                found.append((value, m.start(group), m.end(group)))
        out[name] = found
    return out
