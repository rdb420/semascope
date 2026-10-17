"""Words of a text as the analysis views count them.

A counted word is three or more letters of any script (no digits), lower-cased
and accent-folded, and not a stop word of English or of the document's
language: the same rule as the result-set terms view (`api/searchplus.py`).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from oss_server.knowledge.synonyms import fold

WORD = re.compile(r"[^\W\d_]{3,}")


def stop_words(languages: Iterable[str]) -> frozenset[str]:
    """English stop words plus those of the given languages (ISO codes or tags)."""

    from oss_server.api.searchplus import _stopwords

    return _stopwords(frozenset({"en", *((lang or "")[:2] for lang in languages)}))


def counted_words(text: str, stop: frozenset[str]) -> Counter[str]:
    """How often each counted word occurs in the text."""

    out: Counter[str] = Counter()
    for w in WORD.findall(text):
        w = fold(w.lower())
        if w not in stop:
            out[w] += 1
    return out
