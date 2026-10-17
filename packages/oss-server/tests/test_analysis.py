"""Analysis views (ADR-0012: ANL-12 clusters, ANL-13 diff, ANL-14 comparison, ANL-15 word
tree): the pure functions, and the routes over a fake Solr, read-scoped."""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server import services
from oss_server.analysis import clusters, compare, diff, wordtree
from oss_server.analysis.text import counted_words
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, CollectionRole, Document, Source

SEA = "ship sail wave harbour oar mast storm voyage sea anchor "
WAR = "spear shield battle chariot siege wall bronze helmet war troop "


# --- clusters ----------------------------------------------------------------------------------


def test_term_matrix_keeps_words_shared_by_some_documents() -> None:
    counts = [
        Counter({"ship": 3, "sea": 1, "only": 1}),
        Counter({"ship": 1, "sea": 2}),
        Counter({"spear": 2, "sea": 1}),
        Counter({"spear": 1, "shield": 1, "sea": 1}),
        Counter({"shield": 2, "sea": 1}),
    ]
    x, vocab = clusters.term_matrix(counts)
    assert "only" not in vocab  # in one document: says nothing about groups
    assert "sea" not in vocab  # in every document: says nothing either
    assert set(vocab) == {"ship", "spear", "shield"}
    assert np.allclose(np.linalg.norm(x, axis=1), 1.0)


def test_kmeans_separates_two_topics_and_is_deterministic() -> None:
    texts = [SEA * (i + 1) + "voyage " * i for i in range(4)] + [
        WAR * (i + 1) + "siege " * i for i in range(4)
    ]
    counts = [counted_words(t, frozenset()) for t in texts]
    x, vocab = clusters.term_matrix(counts)
    labels = clusters.kmeans(x, 2)
    assert len(set(labels[:4])) == 1 and len(set(labels[4:])) == 1
    assert labels[0] != labels[4]
    assert list(labels) == list(clusters.kmeans(x, 2))
    found = clusters.describe(x, labels, x, vocab, top=5)
    assert [len(c.members) for c in found] == [4, 4]
    words = {t for c in found for t, _ in c.terms}
    assert {"siege", "voyage"} & words
    for c in found:
        assert c.similarity == sorted(c.similarity, reverse=True)
        assert 0 < c.coherence <= 1


def test_kmeans_with_more_clusters_than_documents_and_identical_rows() -> None:
    x = clusters.normalise(np.array([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]))
    labels = clusters.kmeans(x, 5)
    assert len(labels) == 3 and max(labels) <= 2
    assert clusters.auto_k(1) == 1 and clusters.auto_k(3) == 2 and clusters.auto_k(200) == 10


# --- comparison --------------------------------------------------------------------------------


def test_log_likelihood_is_signed_and_zero_for_equal_shares() -> None:
    assert compare.log_likelihood(5, 10, 5, 10) == pytest.approx(0.0)
    assert compare.log_likelihood(9, 10, 1, 10) > 5
    assert compare.log_likelihood(1, 10, 9, 10) < -5
    assert compare.log_likelihood(0, 0, 3, 10) == 0.0


def test_compare_splits_words_typical_of_each_side() -> None:
    a = compare.document_frequencies([Counter({"ship": 1, "sea": 1})] * 8)
    b = compare.document_frequencies([Counter({"spear": 1, "sea": 1})] * 8)
    more, less = compare.split(compare.compare(a, b, ["ship", "spear", "sea"]), 5)
    assert [r.term for r in more] == ["ship"]
    assert [r.term for r in less] == ["spear"]
    assert more[0].a_share == 1.0 and more[0].b_share == 0.0


# --- word tree ---------------------------------------------------------------------------------


def test_word_tree_branches_and_stops_at_sentence_ends() -> None:
    texts = [
        ("d1", "The wine-dark sea rolled. The wine-dark sea rolled on and on."),
        ("d2", "Over the wine-dark Sea we sailed, and the WINE-DARK sea rolled."),
    ]
    tree = wordtree.build(texts, "wine-dark sea", depth=3)
    assert tree.occurrences == 4 and tree.documents == 2
    root = wordtree.as_dict(tree.root, branches=5)
    kids = {c["word"]: c for c in root["children"]}
    assert kids["rolled"]["count"] == 3
    assert kids["rolled"]["children"][0]["word"] == "on"  # only one "rolled" goes on
    assert kids["we"]["count"] == 1
    # "rolled." ends the sentence: the first occurrence contributes one word only
    assert kids["rolled"]["children"][0]["count"] == 1


def test_word_tree_to_the_left_and_capped() -> None:
    text = "red ship. blue ship. red ship. old red ship"
    tree = wordtree.build([("d", text)], "ship", side="left", depth=2)
    root = wordtree.as_dict(tree.root, branches=1)
    assert root["children"][0]["word"] == "red" and root["children"][0]["count"] == 3
    assert root["more"] == 1  # "blue" left out at one branch
    capped = wordtree.build([("d", text)], "ship", max_occurrences=2)
    assert capped.occurrences == 2 and capped.truncated


# --- diff --------------------------------------------------------------------------------------


def test_diff_marks_changed_words_and_collapses_equal_runs() -> None:
    a = "\n".join(f"line {i}" for i in range(30)) + "\nthe quick brown fox\nend"
    b = "\n".join(f"line {i}" for i in range(30)) + "\nthe quick red fox\nend\nadded"
    d = diff.diff_texts(a, b, context=2)
    ops = [h["op"] for h in d.hunks]
    assert ops[0] == "skip" and "replace" in ops and "insert" in ops
    replaced = next(h for h in d.hunks if h["op"] == "replace")
    assert {"op": "delete", "text": "brown"} in replaced["words"]
    assert {"op": "insert", "text": "red"} in replaced["words"]
    assert d.added == 1 and d.changed == 1 and 0.9 < d.ratio < 1
    assert not d.truncated


def test_diff_caps_its_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diff, "MAX_LINES", 10)
    d = diff.diff_texts("\n".join("x" * 20), "y")
    assert d.truncated and d.a_lines == 10
    assert diff.word_diff("a " * 5000, "b") is None


# --- routes ------------------------------------------------------------------------------------

A_DOCS = ["a" * 31 + str(i) for i in range(4)]
B_DOC = "b" * 32


class AnalysisSolr:
    """A search's hits: every document of collection a, plus one of b (a stale index)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        self.calls.append(params)
        if "facet.query" in params:
            return {
                "response": {"numFound": 100},
                "facet_counts": {"facet_queries": {q: 50 for q in params["facet.query"]}},
            }
        docs = [{"id": i, "collection_s": "a", "uri_s": i} for i in [*A_DOCS, B_DOC]]
        return {"response": {"numFound": len(docs), "docs": docs}}


@pytest.fixture
def world(db: None, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    solr = AnalysisSolr()
    monkeypatch.setattr(services, "solr_client", lambda: solr)
    a = Collection.objects.create(slug="a", name="a")
    b = Collection.objects.create(slug="b", name="b")
    reader = User.objects.create_user("reader")
    CollectionRole.objects.create(user=reader, collection=a, role="reader")
    sa = Source.objects.create(collection=a, name="s", connector="filesystem")
    sb = Source.objects.create(collection=b, name="s", connector="filesystem")
    for n, did in enumerate(A_DOCS):
        Document.objects.create(
            id=did,
            collection=a,
            source=sa,
            uri=f"u:{did}",
            content_hash="h",
            title=f"doc {n}",
            text=(SEA if n < 2 else WAR) * 5 + "The wine-dark sea rolled on.",
            language="en",
        )
    Document.objects.create(
        id=B_DOC, collection=b, source=sb, uri="u:b", content_hash="h", text=SEA * 9
    )
    return {"reader": reader, "solr": solr}


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user, 't')}"}


def test_clusters_route_groups_the_readable_hits(client: Client, world: dict[str, Any]) -> None:
    resp = client.post(
        "/api/analytics/clusters",
        data={"search": {"q": "sea"}, "k": 2},
        content_type="application/json",
        headers=_auth(world["reader"]),
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["documents"] == 4  # the stale hit in b is dropped
    groups = sorted(sorted(d["id"] for d in c["documents"]) for c in body["clusters"])
    assert groups == [A_DOCS[:2], A_DOCS[2:]]
    assert all(c["terms"] for c in body["clusters"])
    # Solr was asked for collection a only
    assert any('collection_s:("a")' in str(c.get("fq")) for c in world["solr"].calls)


def test_compare_route_against_the_index(client: Client, world: dict[str, Any]) -> None:
    resp = client.post(
        "/api/analytics/compare",
        data={"search": {"q": "sea"}, "top": 5},
        content_type="application/json",
        headers=_auth(world["reader"]),
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["background"] == "index" and body["a_documents"] == 4
    assert body["b_documents"] == 100
    assert body["more"] and all(t["g2"] > 0 for t in body["more"])


def test_compare_route_against_another_search_needs_its_collection(
    client: Client, world: dict[str, Any]
) -> None:
    resp = client.post(
        "/api/analytics/compare",
        data={"search": {"q": "sea"}, "against": {"q": "war", "collection": "b"}},
        content_type="application/json",
        headers=_auth(world["reader"]),
    )
    assert resp.status_code == 403


def test_word_tree_route(client: Client, world: dict[str, Any]) -> None:
    resp = client.post(
        "/api/analytics/wordtree",
        data={"search": {"q": "sea"}, "phrase": "wine-dark sea"},
        content_type="application/json",
        headers=_auth(world["reader"]),
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["occurrences"] == 4 and body["root"]["children"][0]["word"] == "rolled"
    assert set(body["titles"]) == set(A_DOCS)
    empty = client.post(
        "/api/analytics/wordtree",
        data={"search": {"q": "sea"}, "phrase": "..."},
        content_type="application/json",
        headers=_auth(world["reader"]),
    )
    assert empty.status_code == 422


def test_diff_route_needs_reader_on_both(client: Client, world: dict[str, Any]) -> None:
    ok = client.get(
        f"/api/analytics/diff?a={A_DOCS[0]}&b={A_DOCS[2]}", headers=_auth(world["reader"])
    )
    assert ok.status_code == 200, ok.content
    assert ok.json()["a"]["title"] == "doc 0" and ok.json()["ratio"] < 1
    hidden = client.get(
        f"/api/analytics/diff?a={A_DOCS[0]}&b={B_DOC}", headers=_auth(world["reader"])
    )
    assert hidden.status_code == 403
