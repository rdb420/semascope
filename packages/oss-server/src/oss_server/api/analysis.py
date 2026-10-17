"""Analysis views over a search's results (ADR-0012, parity rows ANL-12 to ANL-15).

- `POST /analytics/clusters`  groups of similar documents, labelled by their words
- `POST /analytics/compare`   words that set a search apart from another or the index
- `POST /analytics/wordtree`  what follows (or precedes) a word in the results
- `GET  /analytics/diff`      two documents compared line by line and word by word

Every view reads at most a few hundred documents (`sample`), each cut to its
first characters, from the search's hits in the collections the caller may
read; the documents are checked against that scope again in Postgres. The
diff needs reader on both documents' collections.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import Field

from oss_server import services
from oss_server.analysis import clusters as clustering
from oss_server.analysis import compare as comparing
from oss_server.analysis import diff as diffing
from oss_server.analysis import hits, wordtree
from oss_server.analysis.text import counted_words, stop_words
from oss_server.api.access import check, read_scope
from oss_server.api.schemas import SearchIn
from oss_server.catalogue.models import Document

log = logging.getLogger(__name__)

TEXT_CHARS = 100_000  # characters of each document the term views read
TREE_CHARS = 500_000  # the word tree reads more: a word may be rare
MAX_SAMPLE = 500
MAX_VECTOR_SAMPLE = 100
MAX_SHOWN = 50  # documents listed per cluster
BACKGROUND_CANDIDATES = 300  # words of a search weighed against the index (one Solr request)
SEARCH_CANDIDATES = 20000  # words of each side weighed when two searches are compared


class TermWeight(Schema):
    term: str
    weight: float


class ClusterDocument(Schema):
    id: str
    title: str
    collection: str
    similarity: float


class ClusterOut(Schema):
    id: int
    size: int
    coherence: float
    terms: list[TermWeight]
    documents: list[ClusterDocument]


class ClustersIn(Schema):
    search: SearchIn = SearchIn()
    k: int | None = Field(default=None, ge=1, le=clustering.MAX_K)
    sample: int = Field(default=200, ge=2, le=MAX_SAMPLE)
    features: Literal["terms", "vectors"] = "terms"
    top_terms: int = Field(default=8, ge=1, le=30)


class ClustersOut(Schema):
    documents: int
    k: int
    features: str
    clusters: list[ClusterOut]
    vector_error: str | None = None


class CompareIn(Schema):
    search: SearchIn
    against: SearchIn | None = None  # None: the index (the readable collections searched)
    top: int = Field(default=30, ge=1, le=200)
    sample: int = Field(default=100, ge=1, le=MAX_SAMPLE)


class CompareTerm(Schema):
    term: str
    a_docs: int
    b_docs: int
    a_share: float
    b_share: float
    g2: float


class CompareOut(Schema):
    a_documents: int
    b_documents: int
    background: Literal["search", "index"]
    more: list[CompareTerm]  # more typical of the search
    less: list[CompareTerm]  # more typical of the other side


class WordTreeIn(Schema):
    search: SearchIn
    phrase: str = Field(min_length=1, max_length=100)
    side: Literal["right", "left"] = "right"
    depth: int = Field(default=4, ge=1, le=wordtree.MAX_DEPTH)
    branches: int = Field(default=8, ge=1, le=wordtree.MAX_BRANCHES)
    sample: int = Field(default=100, ge=1, le=200)


class WordTreeOut(Schema):
    phrase: str
    side: str
    sampled: int
    occurrences: int
    documents: int
    truncated: bool
    root: dict[str, Any]
    titles: dict[str, str]


class DiffDocument(Schema):
    id: str
    title: str
    collection: str


class DiffOut(Schema):
    a: DiffDocument
    b: DiffDocument
    ratio: float
    a_lines: int
    b_lines: int
    added: int
    removed: int
    changed: int
    truncated: bool
    hunks: list[dict[str, Any]]


def _title(doc: Document) -> str:
    return str(doc.title or doc.uri or doc.id)


def _vectors(docs: list[Document]) -> Any:
    """One unit vector per document: the mean of up to two passages' embeddings."""

    import numpy as np

    from oss_server.api import search as search_mod
    from oss_server.api.similar import centroid, passages

    embedder = search_mod.query_embedder()
    rows = []
    for doc in docs:
        texts = passages(str(getattr(doc, "head", "") or ""), n=2, size=800) or [_title(doc)]
        rows.append(centroid([list(v) for v in embedder.embed(texts)]))
    return clustering.normalise(np.array(rows, dtype=np.float64))


def cluster_results(request: HttpRequest, payload: ClustersIn) -> ClustersOut:
    """The search's first `sample` hits grouped into `k` clusters (default: about the square
    root of half their number) by their words (`features=terms`, TF-IDF) or their meaning
    (`features=vectors`, at most 100 documents), each labelled by its most typical words."""

    allowed = read_scope(request, payload.search.collection)
    sample = payload.sample
    if payload.features == "vectors":
        sample = min(sample, MAX_VECTOR_SAMPLE)
    docs = hits.sample_texts(payload.search, allowed, sample, TEXT_CHARS)
    stop = stop_words({d.language for d in docs})
    counts = [counted_words(str(getattr(d, "head", "") or ""), stop) for d in docs]
    terms, vocab = clustering.term_matrix(counts)
    features = payload.features
    vector_error = None
    x = terms
    if features == "vectors" and docs:
        try:
            x = _vectors(docs)
        except Exception as exc:  # noqa: BLE001 - no embedder: say so and group by words
            log.info("clusters: no vectors (%s); grouping by words", exc)
            vector_error = str(exc)[:300]
            features = "terms"
    k = min(payload.k or clustering.auto_k(len(docs)), max(1, len(docs)))
    out = ClustersOut(
        documents=len(docs), k=k, features=features, clusters=[], vector_error=vector_error
    )
    if not docs:
        out.k = 0
        return out
    labels = clustering.kmeans(x, k)
    for n, c in enumerate(clustering.describe(x, labels, terms, vocab, payload.top_terms)):
        out.clusters.append(
            ClusterOut(
                id=n + 1,
                size=len(c.members),
                coherence=c.coherence,
                terms=[TermWeight(term=t, weight=w) for t, w in c.terms],
                documents=[
                    ClusterDocument(
                        id=docs[i].id,
                        title=_title(docs[i]),
                        collection=docs[i].collection.slug,
                        similarity=s,
                    )
                    for i, s in zip(c.members[:MAX_SHOWN], c.similarity[:MAX_SHOWN], strict=False)
                ],
            )
        )
    out.k = len(out.clusters)
    return out


def _side(
    request: HttpRequest, search: SearchIn, sample: int
) -> tuple[list[str] | None, comparing.Side]:
    allowed = read_scope(request, search.collection)
    docs = hits.sample_texts(search, allowed, sample, TEXT_CHARS)
    stop = stop_words({d.language for d in docs})
    side = comparing.document_frequencies(
        counted_words(str(getattr(d, "head", "") or ""), stop) for d in docs
    )
    return allowed, side


def _term(r: comparing.Compared) -> dict[str, Any]:
    return {k: v for k, v in vars(r).items() if k != "weight"}


def compare_results(request: HttpRequest, payload: CompareIn) -> CompareOut:
    """The words most typical of a search's documents against a second search's
    (`against`), or against the whole of the readable collections searched (no `against`),
    ranked by log-likelihood on the documents that hold them."""

    from oss_server.api.searchplus import _background

    allowed, a = _side(request, payload.search, payload.sample)
    top_a = a.frequent(BACKGROUND_CANDIDATES)
    if payload.against is not None:
        _, b = _side(request, payload.against, payload.sample)
        # both sides are counted here: weigh every word either side holds
        candidates = a.frequent(SEARCH_CANDIDATES) + b.frequent(SEARCH_CANDIDATES)
        background: Literal["search", "index"] = "search"
    else:
        candidates = top_a
        total, found = (
            _background(services.solr_client(), candidates, allowed, payload.search.collection)
            if candidates
            else (0, {})
        )
        b = comparing.Side(documents=total, df=found)
        background = "index"
    more, less = comparing.split(comparing.compare(a, b, candidates), payload.top)
    return CompareOut(
        a_documents=a.documents,
        b_documents=b.documents,
        background=background,
        more=[CompareTerm(**_term(r)) for r in more],
        less=[CompareTerm(**_term(r)) for r in less],
    )


def word_tree(request: HttpRequest, payload: WordTreeIn) -> WordTreeOut:
    """The words that follow (`side=right`) or precede (`side=left`) a word or phrase in the
    search's first `sample` hits, as a tree of shared continuations up to `depth` words,
    never crossing a sentence end."""

    if not wordtree.tokens(payload.phrase):
        raise HttpError(422, "the phrase holds no word")
    allowed = read_scope(request, payload.search.collection)
    docs = hits.sample_texts(payload.search, allowed, payload.sample, TREE_CHARS)
    tree = wordtree.build(
        ((d.id, str(getattr(d, "head", "") or "")) for d in docs),
        payload.phrase,
        side=payload.side,
        depth=payload.depth,
    )
    root = wordtree.as_dict(tree.root, payload.branches)
    titles = {d.id: _title(d) for d in docs if d.id in tree.root.docs}
    return WordTreeOut(
        phrase=tree.phrase,
        side=tree.side,
        sampled=len(docs),
        occurrences=tree.occurrences,
        documents=tree.documents,
        truncated=tree.truncated,
        root=root,
        titles=titles,
    )


def _readable(request: HttpRequest, doc_id: str) -> Document:
    doc = get_object_or_404(
        Document.objects.select_related("collection").only(
            "id", "title", "uri", "text", "collection__slug"
        ),
        pk=doc_id,
    )
    check(request, "reader", doc.collection.slug)
    return doc


def diff_documents(
    request: HttpRequest, a: str, b: str, context: int = 3, words: bool = True
) -> DiffOut:
    """Document `b` against document `a`: equal runs collapsed to `context` lines at their
    edges, inserted and deleted lines, and replaced lines marked word by word."""

    left, right = _readable(request, a), _readable(request, b)
    d = diffing.diff_texts(left.text, right.text, context=max(0, min(context, 20)), words=words)
    return DiffOut(
        a=DiffDocument(id=left.id, title=_title(left), collection=left.collection.slug),
        b=DiffDocument(id=right.id, title=_title(right), collection=right.collection.slug),
        ratio=d.ratio,
        a_lines=d.a_lines,
        b_lines=d.b_lines,
        added=d.added,
        removed=d.removed,
        changed=d.changed,
        truncated=d.truncated,
        hunks=d.hunks,
    )


def mount(router: Router) -> None:
    router.post("/analytics/clusters", response=ClustersOut, tags=["analytics"])(cluster_results)
    router.post("/analytics/compare", response=CompareOut, tags=["analytics"])(compare_results)
    router.post("/analytics/wordtree", response=WordTreeOut, tags=["analytics"])(word_tree)
    router.get("/analytics/diff", response=DiffOut, tags=["analytics"])(diff_documents)
