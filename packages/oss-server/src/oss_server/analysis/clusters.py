"""Clusters of documents (ANL-12): spherical k-means, labelled by significant terms.

Each document is a vector: its TF-IDF weights over the words the set holds
(`term_matrix`), or the mean of its passages' embeddings (the API builds those;
chunk vectors are indexed but not stored, so they cannot be read back). Vectors
are scaled to unit length and grouped by k-means on cosine similarity, seeded by
k-means++ with a fixed seed so the same documents give the same clusters.

A cluster is labelled by the words whose mean TF-IDF weight in it most exceeds
their mean weight in the other clusters.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

Matrix = NDArray[np.float64]

MAX_VOCABULARY = 3000
MAX_K = 20
ITERATIONS = 60
SEED = 20261017


@dataclass
class Cluster:
    members: list[int]  # row indices, closest to the centre first
    similarity: list[float]  # each member's cosine similarity to the centre
    terms: list[tuple[str, float]] = field(default_factory=list)
    coherence: float = 0.0  # mean similarity of the members to the centre


def auto_k(n: int) -> int:
    """A number of clusters for n documents when the caller names none: about sqrt(n/2)."""

    if n < 4:
        return 1 if n < 2 else 2
    return max(2, min(10, round(math.sqrt(n / 2))))


def term_matrix(
    counts: Sequence[Counter[str]], max_vocabulary: int = MAX_VOCABULARY
) -> tuple[Matrix, list[str]]:
    """Unit-length TF-IDF rows (log-scaled term frequency) over the most informative words.

    A word in a single document says nothing about groups, and one in nearly every
    document says nothing either: with five or more documents the vocabulary keeps
    the words in at least two and at most 90% of them, by document frequency.
    """

    n = len(counts)
    df: Counter[str] = Counter()
    for c in counts:
        df.update(c.keys())
    if n >= 5:
        keep = [w for w, d in df.items() if 2 <= d <= max(2, int(0.9 * n))]
    else:
        keep = list(df)
    keep.sort(key=lambda w: (-df[w], w))
    vocab = sorted(keep[:max_vocabulary])
    index = {w: i for i, w in enumerate(vocab)}
    x = np.zeros((n, len(vocab)), dtype=np.float64)
    for row, c in enumerate(counts):
        for w, tf in c.items():
            col = index.get(w)
            if col is not None:
                x[row, col] = (1.0 + math.log(tf)) * math.log((1 + n) / (1 + df[w]) + 1.0)
    return normalise(x), vocab


def normalise(x: Matrix) -> Matrix:
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    out: Matrix = x / norms
    return out


def _seeds(x: Matrix, k: int, rng: np.random.Generator) -> Matrix:
    """k-means++: each next centre drawn with probability by its distance to the chosen."""

    n = x.shape[0]
    centres = [int(rng.integers(n))]
    dist = 1.0 - x @ x[centres[0]]
    for _ in range(1, k):
        weights = np.clip(dist, 0.0, None) ** 2
        total = float(weights.sum())
        if total <= 0:  # every row equals a centre: take the next unused row
            pick = next(i for i in range(n) if i not in centres)
        else:
            pick = int(rng.choice(n, p=weights / total))
        centres.append(pick)
        dist = np.minimum(dist, 1.0 - x @ x[pick])
    seeded: Matrix = x[centres].copy()
    return seeded


def kmeans(x: Matrix, k: int, *, seed: int = SEED, iterations: int = ITERATIONS) -> NDArray[Any]:
    """Cluster labels for the unit-length rows of x (spherical k-means)."""

    n = x.shape[0]
    k = max(1, min(k, n))
    if k == 1:
        return np.zeros(n, dtype=np.int64)
    rng = np.random.default_rng(seed)
    centres = _seeds(x, k, rng)
    labels = np.full(n, -1, dtype=np.int64)
    for _ in range(iterations):
        new = np.argmax(x @ centres.T, axis=1)
        if np.array_equal(new, labels):
            break
        labels = new
        for j in range(k):
            members = x[labels == j]
            if len(members):
                centre = members.sum(axis=0)
                norm = float(np.linalg.norm(centre))
                centres[j] = centre / norm if norm else centre
            else:  # an empty cluster takes the row farthest from its centre
                sims = np.einsum("ij,ij->i", x, centres[labels])
                far = int(np.argmin(sims))
                centres[j] = x[far]
                labels[far] = j
    return labels


def describe(
    x: Matrix, labels: NDArray[Any], terms: Matrix | None, vocab: Sequence[str], top: int = 8
) -> list[Cluster]:
    """The clusters, largest first, with their members ordered by closeness to the centre
    and their labelling terms (from `terms`, the TF-IDF rows, which may differ from x)."""

    out: list[Cluster] = []
    for j in sorted(set(int(v) for v in labels)):
        rows = [i for i, v in enumerate(labels) if int(v) == j]
        centre = x[rows].sum(axis=0)
        norm = float(np.linalg.norm(centre))
        centre = centre / norm if norm else centre
        sims = [float(x[i] @ centre) for i in rows]
        order = sorted(range(len(rows)), key=lambda m: -sims[m])
        cluster = Cluster(
            members=[rows[m] for m in order],
            similarity=[round(sims[m], 4) for m in order],
            coherence=round(sum(sims) / len(sims), 4),
        )
        if terms is not None and terms.shape[1]:
            inside = terms[rows].mean(axis=0)
            others = [i for i in range(terms.shape[0]) if int(labels[i]) != j]
            outside = terms[others].mean(axis=0) if others else np.zeros(terms.shape[1])
            score = inside - outside
            best = np.argsort(-score)[:top]
            cluster.terms = [
                (vocab[int(c)], round(float(score[int(c)]), 4)) for c in best if score[int(c)] > 0
            ]
        out.append(cluster)
    out.sort(key=lambda c: (-len(c.members), c.members[0] if c.members else 0))
    return out
