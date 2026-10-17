"""Text diff (ANL-13): two texts compared line by line, changed lines word by word.

`difflib.SequenceMatcher` aligns the lines; runs of equal lines keep `context`
lines at each edge and the rest is collapsed to a count. A replaced block of
lines is compared again as words (and the whitespace and punctuation between
them), so a changed word is marked inside its line. Inputs are capped
(`MAX_LINES`, `MAX_CHARS`) and blocks too large for a word diff stay line diffs.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

MAX_CHARS = 400_000
MAX_LINES = 20_000
MAX_WORD_BLOCK = 4000  # tokens per side a word diff takes on
MAX_HUNKS = 2000
PIECE = re.compile(r"\w+|\s+|[^\w\s]")


@dataclass
class Diff:
    ratio: float
    a_lines: int
    b_lines: int
    added: int = 0
    removed: int = 0
    changed: int = 0
    truncated: bool = False
    hunks: list[dict[str, Any]] = field(default_factory=list)


def _cut(text: str) -> tuple[list[str], bool]:
    cut = len(text) > MAX_CHARS
    lines = text[:MAX_CHARS].splitlines()
    if len(lines) > MAX_LINES:
        return lines[:MAX_LINES], True
    return lines, cut


def word_diff(a: str, b: str) -> list[dict[str, str]] | None:
    """The two texts as a sequence of equal, deleted and inserted pieces (None: too large)."""

    pa, pb = PIECE.findall(a), PIECE.findall(b)
    if len(pa) > MAX_WORD_BLOCK or len(pb) > MAX_WORD_BLOCK:
        return None
    out: list[dict[str, str]] = []

    def put(op: str, text: str) -> None:
        if not text:
            return
        if out and out[-1]["op"] == op:
            out[-1]["text"] += text
        else:
            out.append({"op": op, "text": text})

    m = difflib.SequenceMatcher(None, pa, pb, autojunk=False)
    for tag, i1, i2, j1, j2 in m.get_opcodes():
        if tag == "equal":
            put("equal", "".join(pa[i1:i2]))
        else:
            put("delete", "".join(pa[i1:i2]))
            put("insert", "".join(pb[j1:j2]))
    return out


def diff_texts(a: str, b: str, *, context: int = 3, words: bool = True) -> Diff:
    la, cut_a = _cut(a)
    lb, cut_b = _cut(b)
    m = difflib.SequenceMatcher(None, la, lb)
    out = Diff(ratio=0.0, a_lines=len(la), b_lines=len(lb), truncated=cut_a or cut_b)
    ops = m.get_opcodes()
    for n, (tag, i1, i2, j1, j2) in enumerate(ops):
        if len(out.hunks) >= MAX_HUNKS:
            out.truncated = True
            break
        if tag == "equal":
            head = context if n > 0 else 0
            tail = context if n < len(ops) - 1 else 0
            size = i2 - i1
            if size <= head + tail + 1:
                out.hunks.append({"op": "equal", "a_start": i1, "b_start": j1, "lines": la[i1:i2]})
                continue
            if head:
                out.hunks.append(
                    {"op": "equal", "a_start": i1, "b_start": j1, "lines": la[i1 : i1 + head]}
                )
            out.hunks.append(
                {
                    "op": "skip",
                    "a_start": i1 + head,
                    "b_start": j1 + head,
                    "count": size - head - tail,
                }
            )
            if tail:
                out.hunks.append(
                    {
                        "op": "equal",
                        "a_start": i2 - tail,
                        "b_start": j2 - tail,
                        "lines": la[i2 - tail : i2],
                    }
                )
            continue
        hunk: dict[str, Any] = {
            "op": tag,
            "a_start": i1,
            "b_start": j1,
            "a": la[i1:i2],
            "b": lb[j1:j2],
        }
        out.removed += i2 - i1 if tag == "delete" else 0
        out.added += j2 - j1 if tag == "insert" else 0
        if tag == "replace":
            out.changed += max(i2 - i1, j2 - j1)
            if words:
                pieces = word_diff("\n".join(la[i1:i2]), "\n".join(lb[j1:j2]))
                if pieces is not None:
                    hunk["words"] = pieces
        out.hunks.append(hunk)
    out.ratio = round(m.ratio(), 4)
    return out
