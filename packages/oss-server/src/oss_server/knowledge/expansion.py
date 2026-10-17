"""What a query expands to, and which labels could be added as synonyms.

`expansions(q)` reads the query's words the way the synonym rules do
(`synonyms.words`, lower-cased and accent-folded) and finds every run of
words that is a label of an active pack's concept or individual, as the
generated `synonyms_packs.txt` would expand it: an individual's labels are
equivalent (weight 1), a concept's other labels come in at
`synonyms.CONCEPT_WEIGHT`. Longer matches win over the shorter ones inside
them, as Solr's synonym graph prefers the longest rule.

`distance` and `near` find spelling variants for the suggestion view.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from oss_server.knowledge import synonyms

MAX_LABEL_WORDS = 8


@dataclass
class LabelRef:
    concept_id: int
    label: str
    label_type: str  # pref, alt, hidden


@dataclass
class Index:
    by_form: dict[str, list[LabelRef]] = field(default_factory=dict)
    concepts: dict[int, Any] = field(default_factory=dict)


_cache: dict[tuple[Any, ...], Index] = {}


def key(label: str) -> str:
    return synonyms.fold(synonyms.words(label)).lower()


def typed_labels(concept: Any) -> list[tuple[str, str]]:
    """A concept's labels with their type: pref (every language), alt, hidden."""

    out = [(str(concept.pref_label), "pref")]
    out += [
        (str(p.get("label") or ""), "pref") for p in (concept.extra or {}).get("pref_labels") or []
    ]
    out += [(str(a), "alt") for a in concept.alt_labels or []]
    out += [(str(h), "hidden") for h in concept.hidden_labels or []]
    return [(label, kind) for label, kind in out if label.strip()]


def label_index() -> Index:
    """Every usable label of the active packs' taggable concepts and individuals, by the form
    a query's words take; rebuilt when the set of active packs changes."""

    from oss_server.knowledge.models import Concept, Pack

    marker = tuple(
        Pack.objects.filter(status=Pack.Status.ACTIVE)
        .order_by("pk")
        .values_list("pk", "activated_at", "manifest_hash")
    )
    if marker in _cache:
        return _cache[marker]
    index = Index()
    stops: dict[str, set[str]] = {}
    for concept in (
        Concept.objects.filter(
            pack__status=Pack.Status.ACTIVE,
            taggable=True,
            kind__in=[Concept.Kind.CONCEPT, Concept.Kind.INDIVIDUAL],
        )
        .select_related("pack")
        .iterator()
    ):
        code = (concept.lang or "").split("-")[0].lower()
        if code not in stops:
            stops[code] = synonyms.stopwords({"en", code})
        index.concepts[concept.pk] = concept
        for label, kind in typed_labels(concept):
            clean = " ".join(label.split())
            if not synonyms.usable(clean, stops[code]):
                continue
            form = key(clean)
            if form and len(form.split()) <= MAX_LABEL_WORDS:
                refs = index.by_form.setdefault(form, [])
                if not any(r.concept_id == concept.pk for r in refs):
                    refs.append(LabelRef(concept.pk, clean, kind))
    _cache.clear()
    _cache[marker] = index
    return index


def clear() -> None:
    _cache.clear()


@dataclass
class Expansion:
    phrase: str
    start: int  # word positions in the query
    end: int
    concept: Any
    matched: LabelRef
    weight_of_others: float
    others: list[tuple[str, str]]  # (label, type) the phrase expands to


def expansions(q: str, index: Index | None = None) -> tuple[list[str], list[Expansion]]:
    """The query's words, and the label matches the synonym rules would expand."""

    index = index or label_index()
    written = synonyms.words(q).split()
    keys = [synonyms.fold(w).lower() for w in written]
    found: list[Expansion] = []
    taken: set[int] = set()
    for size in range(min(MAX_LABEL_WORDS, len(keys)), 0, -1):
        for i in range(0, len(keys) - size + 1):
            span = set(range(i, i + size))
            if span & taken:
                continue
            refs = index.by_form.get(" ".join(keys[i : i + size]))
            if not refs:
                continue
            taken |= span
            for ref in refs:
                concept = index.concepts[ref.concept_id]
                weight = 1.0 if concept.kind == "individual" else synonyms.CONCEPT_WEIGHT
                mine = key(ref.label)
                others: list[tuple[str, str]] = []
                seen = {mine}
                for label, kind in typed_labels(concept):
                    k = key(label)
                    if k and k not in seen and k in index.by_form:
                        seen.add(k)
                        others.append((label, kind))
                found.append(
                    Expansion(
                        phrase=" ".join(written[i : i + size]),
                        start=i,
                        end=i + size,
                        concept=concept,
                        matched=ref,
                        weight_of_others=weight,
                        others=others,
                    )
                )
    found.sort(key=lambda e: (e.start, -e.end))
    return written, found


# --- spelling variants --------------------------------------------------------------------------


def distance(a: str, b: str, limit: int = 3) -> int:
    """Edit distance (insertions, deletions, substitutions and swaps of neighbours) between two
    strings, or `limit + 1` once it is certain to exceed `limit`."""

    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def allowed_distance(word: str) -> int:
    """Typing errors a variant of a label may hold: one per four letters, at most three."""

    return max(1, min(3, len(word) // 4))


def near(candidate: str, labels: Iterable[str]) -> int | None:
    """The smallest edit distance from a candidate to any label, if it is a variant of one
    (same words when folded and lower-cased is not a variant: that is the label itself)."""

    c = key(candidate)
    best: int | None = None
    for label in labels:
        k = key(label)
        if not k or k == c:
            if k == c:
                return None
            continue
        d = distance(c, k, allowed_distance(k))
        if d <= allowed_distance(k) and (best is None or d < best):
            best = d
    return best
