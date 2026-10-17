"""The ranking-explain view (ANL-16, APP-07): keyword rank, score and clauses, the explanation
whole for staff and without corpus statistics for others; nearest passages; fused shares."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server import services
from oss_server.api import explain
from oss_server.api import search as search_mod
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, CollectionRole, Document, Source

DOC, OTHER, HIDDEN = "a" * 32, "b" * 32, "c" * 32
TREE = {
    "match": True,
    "value": 3.5,
    "description": "sum of:",
    "details": [
        {
            "match": True,
            "value": 2.5,
            "description": "weight(text_txt:odysseus in 4) [SchemaSimilarity], result of:",
            "details": [
                {
                    "value": 1.9,
                    "description": "idf, computed as log(1 + (N - n + 0.5) / (n + 0.5)) from:",
                    "details": [
                        {"value": 12, "description": "n, number of documents containing term"}
                    ],
                },
                {"value": 1.3, "description": "tf, computed as freq / (freq + k1 * ...) from:"},
            ],
        },
        {
            "match": True,
            "value": 1.0,
            "description": "weight(title_txt:odysseus in 4) [SchemaSimilarity], result of:",
        },
    ],
}


class ExplainSolr:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        self.calls.append({"collection": collection, **params})
        q = str(params.get("q", ""))
        if q.startswith("{!knn"):
            if any("document_id:" in f for f in params.get("fq", [])):
                return {
                    "response": {
                        "docs": [
                            {"id": "ch2", "text_txt": "Odysseus came", "page_i": 2, "score": 0.81}
                        ]
                    }
                }
            chunks = [
                {"id": "ch1", "document_id": OTHER, "score": 0.9},
                {"id": "ch2", "document_id": DOC, "score": 0.81},
            ]
            return {"response": {"docs": chunks}}
        if params.get("debugQuery"):
            return {
                "response": {"docs": [{"id": DOC, "score": 3.5}]},
                "debug": {"explain": {DOC: TREE}},
            }
        return {"response": {"docs": [{"id": DOC}, {"id": OTHER}]}}


class Embedder:
    model_name = "bge-small"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]


@pytest.fixture
def world(db: None, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    solr = ExplainSolr()
    monkeypatch.setattr(services, "solr_client", lambda: solr)
    monkeypatch.setattr(search_mod, "query_embedder", lambda: Embedder())
    a = Collection.objects.create(slug="a", name="a")
    b = Collection.objects.create(slug="b", name="b")
    reader = User.objects.create_user("reader")
    CollectionRole.objects.create(user=reader, collection=a, role="reader")
    s = Source.objects.create(collection=a, name="s", connector="filesystem")
    sb = Source.objects.create(collection=b, name="s", connector="filesystem")
    Document.objects.create(
        id=DOC, collection=a, source=s, uri="u:a", content_hash="h", title="Odyssey"
    )
    Document.objects.create(id=HIDDEN, collection=b, source=sb, uri="u:c", content_hash="h")
    return {
        "reader": reader,
        "staff": User.objects.create_user("staff", is_staff=True),
        "solr": solr,
    }


def _post(client: Client, user: User, body: dict[str, Any]) -> Any:
    return client.post(
        "/api/explain",
        body,
        "application/json",
        headers={"Authorization": f"Bearer {issue_token(user, 't')}"},
    )


def test_clauses_and_statistics_free_tree() -> None:
    found = explain.clauses(TREE)
    assert [(c.clause, c.weight) for c in found] == [
        ("text_txt:odysseus", 2.5),
        ("title_txt:odysseus", 1.0),
    ]
    stripped = explain.without_statistics(TREE)
    text = str(stripped)
    assert "idf" not in text and "number of documents" not in text and "tf, computed" in text


def test_keyword_explain_for_a_reader(client: Client, world: dict[str, Any]) -> None:
    resp = _post(client, world["reader"], {"search": {"q": "odysseus"}, "doc_id": DOC})
    assert resp.status_code == 200, resp.content
    body = resp.json()
    kw = body["keyword"]
    assert kw["rank"] == 1 and kw["score"] == 3.5 and kw["matched"]
    assert kw["clauses"][0]["clause"] == "text_txt:odysseus"
    assert "idf" not in str(kw["explanation"]) and not body["full"]
    assert body["semantic"] is None and body["fused"] is None
    probe = next(c for c in world["solr"].calls if c.get("debugQuery"))
    assert f"id:{DOC}" in " ".join(probe["fq"]) or f'id:"{DOC}"' in " ".join(probe["fq"])
    assert any('collection_s:("a")' in f for f in probe["fq"])


def test_staff_get_the_whole_tree_and_hybrid_shares(client: Client, world: dict[str, Any]) -> None:
    resp = _post(
        client, world["staff"], {"search": {"q": "odysseus", "mode": "hybrid"}, "doc_id": DOC}
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["full"] and "idf" in str(body["keyword"]["explanation"])
    sem = body["semantic"]
    assert sem["rank"] == 2 and sem["candidates"] == 2
    assert sem["passages"][0] == {
        "text": "Odysseus came",
        "page": 2,
        "similarity": 0.81,
        "chunk_rank": 2,
    }
    fused = body["fused"]
    assert fused["keyword_share"] == pytest.approx(1 / 60, abs=1e-6)
    assert fused["semantic_share"] == pytest.approx(1 / 61, abs=1e-6)
    # both documents are first in one list and second in the other: a tie, which keeps the
    # semantic order, as the hybrid search's own fusion does
    assert fused["rank"] == 2 and fused["score"] == pytest.approx(1 / 60 + 1 / 61, abs=1e-6)


def test_explain_needs_reader_on_the_document_and_a_query(
    client: Client, world: dict[str, Any]
) -> None:
    assert (
        _post(client, world["reader"], {"search": {"q": "x"}, "doc_id": HIDDEN}).status_code == 403
    )
    assert (
        _post(client, world["reader"], {"search": {"q": "*:*"}, "doc_id": DOC}).status_code == 422
    )
    assert (
        _post(client, world["reader"], {"search": {"q": "x"}, "doc_id": "f" * 32}).status_code
        == 404
    )
