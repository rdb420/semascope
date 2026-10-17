"""Corpus comparison (ANL-14): the words that set one set of documents apart from another.

Both sides are counted in documents, not occurrences, so one long text cannot
dominate: a word's share of side A is the fraction of A's documents holding it.
Words are ranked by Dunning's log-likelihood ratio (G2) on those counts, signed
by direction: positive when A holds the word more often than B. The other side
is a second search's documents, or the background index (the readable
collections), whose counts come from Solr.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


@dataclass
class Side:
    documents: int
    df: Mapping[str, int]  # documents holding each word
    tf: Mapping[str, int] = field(default_factory=dict)  # occurrences, when counted

    def frequent(self, n: int) -> list[str]:
        """The `n` words most documents hold, the more frequent first among equals."""

        return sorted(self.df, key=lambda w: (-self.df[w], -self.tf.get(w, 0), w))[:n]


@dataclass
class Compared:
    term: str
    a_docs: int
    b_docs: int
    a_share: float
    b_share: float
    g2: float  # signed: > 0 when the word is more typical of A
    weight: int = 0  # occurrences on the side the word is typical of (breaks ties)


def document_frequencies(texts: Iterable[Counter[str]]) -> Side:
    df: Counter[str] = Counter()
    tf: Counter[str] = Counter()
    n = 0
    for counts in texts:
        n += 1
        df.update(counts.keys())
        tf.update(counts)
    return Side(documents=n, df=df, tf=tf)


def _ll(k: float, n: float, expected_share: float) -> float:
    return k * math.log(k / (n * expected_share)) if k > 0 and n > 0 else 0.0


def log_likelihood(a: int, n_a: int, b: int, n_b: int) -> float:
    """G2 for a word in `a` of `n_a` documents against `b` of `n_b`, signed by direction."""

    if n_a <= 0 or n_b <= 0:
        return 0.0
    a, b = min(a, n_a), min(b, n_b)
    p = (a + b) / (n_a + n_b)
    if p <= 0 or p >= 1:
        return 0.0
    g2 = 2.0 * (
        _ll(a, n_a, p) + _ll(n_a - a, n_a, 1 - p) + _ll(b, n_b, p) + _ll(n_b - b, n_b, 1 - p)
    )
    return g2 if a / n_a >= b / n_b else -g2


def compare(a: Side, b: Side, candidates: Iterable[str]) -> list[Compared]:
    out: list[Compared] = []
    for term in dict.fromkeys(candidates):
        ka, kb = int(a.df.get(term, 0)), int(b.df.get(term, 0))
        g2 = round(log_likelihood(ka, a.documents, kb, b.documents), 4)
        out.append(
            Compared(
                term=term,
                a_docs=ka,
                b_docs=kb,
                a_share=round(ka / a.documents, 4) if a.documents else 0.0,
                b_share=round(kb / b.documents, 4) if b.documents else 0.0,
                g2=g2,
                weight=int((a.tf if g2 >= 0 else b.tf).get(term, 0)),
            )
        )
    return out


def split(rows: list[Compared], top: int) -> tuple[list[Compared], list[Compared]]:
    """The words most typical of A, and those most typical of B, strongest first (the more
    frequent first among equals: with few documents many words tie)."""

    more = sorted((r for r in rows if r.g2 > 0), key=lambda r: (-r.g2, -r.weight, r.term))[:top]
    less = sorted((r for r in rows if r.g2 < 0), key=lambda r: (r.g2, -r.weight, r.term))[:top]
    return more, less
