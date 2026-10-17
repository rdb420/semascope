"""Word tree (ANL-15): the words that follow (or precede) a word or phrase, as a tree.

Every occurrence of the phrase in the texts (whole words, case and accents
ignored) contributes the words after it, up to `depth` of them and never past
the end of its sentence; occurrences that continue alike share a branch, and a
branch counts its occurrences and documents. Each node keeps its most frequent
children (`branches`); the rest are counted in `more`. With `side="left"` the
tree grows from the phrase backwards, nearest word first.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from oss_server.knowledge.synonyms import fold

TOKEN = re.compile(r"\w+(?:['’]\w+)*")
MAX_DEPTH = 8
MAX_BRANCHES = 20
MAX_OCCURRENCES = 5000


def key(word: str) -> str:
    return fold(word.lower())


def tokens(text: str) -> list[tuple[str, int, int]]:
    """(word, start, end) for every word of the text."""

    return [(m.group(0), m.start(), m.end()) for m in TOKEN.finditer(text)]


def _breaks(text: str, toks: list[tuple[str, int, int]]) -> list[bool]:
    """breaks[i]: a sentence ends between word i and word i + 1."""

    out = []
    for i in range(len(toks) - 1):
        gap = text[toks[i][2] : toks[i + 1][1]]
        out.append(bool(re.search(r"[.!?;。]|\n\s*\n", gap)))
    out.append(True)
    return out


@dataclass
class Node:
    word: str
    count: int = 0
    docs: set[str] = field(default_factory=set)
    forms: Counter[str] = field(default_factory=Counter)
    children: dict[str, Node] = field(default_factory=dict)


@dataclass
class Tree:
    phrase: str
    side: str
    occurrences: int
    documents: int
    truncated: bool
    root: Node


def contexts(
    text: str, phrase: list[str], side: str, depth: int
) -> Iterable[tuple[list[str], str]]:
    """For each occurrence of the phrase (its words as keys): the context words and the
    phrase as written there."""

    toks = tokens(text)
    if not toks or not phrase:
        return
    keys = [key(t[0]) for t in toks]
    stops = _breaks(text, toks)
    n = len(phrase)
    for i in range(len(toks) - n + 1):
        if keys[i : i + n] != phrase:
            continue
        written = text[toks[i][1] : toks[i + n - 1][2]]
        words: list[str] = []
        if side == "right":
            j = i + n - 1
            while len(words) < depth and not stops[j] and j + 1 < len(toks):
                j += 1
                words.append(toks[j][0])
        else:
            j = i
            while len(words) < depth and j > 0 and not stops[j - 1]:
                j -= 1
                words.append(toks[j][0])
        yield words, written


def build(
    texts: Iterable[tuple[str, str]],
    phrase: str,
    *,
    side: str = "right",
    depth: int = 4,
    max_occurrences: int = MAX_OCCURRENCES,
) -> Tree:
    """The tree of the phrase's contexts in (document id, text) pairs."""

    wanted = [key(t[0]) for t in tokens(phrase)]
    root = Node(word=phrase.strip())
    total = 0
    truncated = False
    for doc_id, text in texts:
        if truncated:
            break
        for words, written in contexts(text, wanted, side, depth):
            if total >= max_occurrences:
                truncated = True
                break
            total += 1
            root.count += 1
            root.docs.add(doc_id)
            root.forms[written] += 1
            node = root
            for w in words:
                k = key(w)
                child = node.children.get(k)
                if child is None:
                    child = node.children[k] = Node(word=w)
                child.count += 1
                child.docs.add(doc_id)
                child.forms[w] += 1
                node = child
    if root.forms:
        root.word = root.forms.most_common(1)[0][0]
    return Tree(
        phrase=phrase.strip(),
        side=side,
        occurrences=total,
        documents=len(root.docs),
        truncated=truncated,
        root=root,
    )


def as_dict(node: Node, branches: int, *, sample_docs: int = 3) -> dict[str, Any]:
    """A node and its most frequent children (recursively) as plain data."""

    kids = sorted(node.children.values(), key=lambda c: (-c.count, key(c.word)))
    shown = kids[:branches]
    return {
        "word": node.forms.most_common(1)[0][0] if node.forms else node.word,
        "count": node.count,
        "documents": len(node.docs),
        "example_documents": sorted(node.docs)[:sample_docs] if not node.children else [],
        "children": [as_dict(c, branches, sample_docs=sample_docs) for c in shown],
        "more": sum(c.count for c in kids[branches:]),
    }
