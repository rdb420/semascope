"""Browser search (SRCH-15): the OpenSearch description and suggestions; the analytics overview."""

from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: S405 - parsing our own output
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server import services as server_services
from oss_server.api import analytics, root
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, CollectionRole, Document, Source

A, D = "a" * 32, "d" * 32


@pytest.fixture
def world(db: None, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    a = Collection.objects.create(slug="a", name="a")
    b = Collection.objects.create(slug="b", name="b")
    reader = User.objects.create_user("reader")
    CollectionRole.objects.create(user=reader, collection=a, role="reader")
    s = Source.objects.create(collection=a, name="s", connector="filesystem")
    sb = Source.objects.create(collection=b, name="s", connector="filesystem")
    doc = Document.objects.create(
        id=A, collection=a, source=s, uri="u:a", content_hash="h", text="word " * 2000
    )
    hidden = Document.objects.create(id=D, collection=b, source=sb, uri="u:d", content_hash="h")
    return {"reader": reader, "doc": doc, "hidden": hidden}


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user, 't')}"}


# --- OpenSearch --------------------------------------------------------------------------------


def test_opensearch_description_uses_the_host_the_browser_used(client: Client) -> None:
    resp = client.get("/api/opensearch.xml", HTTP_HOST="localhost:8443", secure=True)
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/opensearchdescription+xml"
    ns = {"os": "http://a9.com/-/spec/opensearch/1.1/"}
    tree = ET.fromstring(resp.content)  # noqa: S314 - our own output
    assert tree.findtext("os:ShortName", namespaces=ns) == "Semascope"
    urls = {u.get("type"): u.get("template") for u in tree.findall("os:Url", ns)}
    assert urls["text/html"] == "https://localhost:8443/?q={searchTerms}"
    assert urls["application/x-suggestions+json"] == (
        "https://localhost:8443/api/opensearch/suggest?q={searchTerms}"
    )


def test_opensearch_suggestions_need_a_session_and_use_the_suggestions_format(
    world: dict[str, Any], client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TitleSolr:
        def select(self, collection: str, **params: Any) -> dict[str, Any]:
            self.params = params
            return {"response": {"docs": [{"title_txt": "The Odyssey"}]}}

    fake = TitleSolr()
    monkeypatch.setattr(root, "solr_client", lambda: fake)
    assert client.get("/api/opensearch/suggest?q=odys").status_code == 401
    resp = client.get("/api/opensearch/suggest?q=odys", headers=_auth(world["reader"]))
    assert resp.json() == ["odys", ["The Odyssey"]]
    assert 'collection_s:("a")' in fake.params["fq"]
    assert client.get("/api/opensearch/suggest?q=", headers=_auth(world["reader"])).json() == [
        "",
        [],
    ]


# --- analytics ---------------------------------------------------------------------------------


def test_overview_query_is_read_scoped_and_parsed(
    world: dict[str, Any], client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    class OverviewSolr:
        def select(self, collection: str, **params: Any) -> dict[str, Any]:
            self.params = params
            return {
                "response": {"numFound": 3},
                "facet_counts": {
                    "facet_fields": {
                        "persons_ss": ["Odysseus", 2, "Athena", 1],
                        "concept_labels_ss": ["Flood", 3],
                        "collection_s": ["a", 3],
                        "language_s": ["en", 3],
                    },
                    "facet_ranges": {
                        "modified": {
                            "counts": ["2020-01-01T00:00:00Z", 2, "2021-01-01T00:00:00Z", 0]
                        },
                        "indexed": {"counts": ["2026-10-16T00:00:00Z", 3]},
                    },
                    "facet_pivot": {
                        "collection_concepts": [
                            {"value": "a", "count": 3, "pivot": [{"value": "Flood", "count": 3}]}
                        ],
                        "collection_entities": [{"value": "a", "count": 3}],
                    },
                },
            }

    fake = OverviewSolr()
    monkeypatch.setattr(server_services, "solr_client", lambda: fake)
    auth = _auth(world["reader"])
    out = client.get("/api/analytics/overview?top=1&gap=+1MONTH", headers=auth).json()
    assert 'collection_s:("a")' in fake.params["fq"]
    assert fake.params["f.modified_dt.facet.range.gap"] == "+1MONTH"
    assert out["documents"] == 3 and out["top"]["people"] == [{"value": "Odysseus", "count": 2}]
    assert out["modified"] == [{"value": "2020-01", "count": 2}]  # empty buckets dropped
    assert out["indexed"] == [{"value": "2026-10-16", "count": 3}]
    assert out["per_collection"] == [
        {
            "collection": "a",
            "documents": 3,
            "concepts": [{"value": "Flood", "count": 3}],
            "entities": [],
        }
    ]
    client.get("/api/analytics/overview?gap=;drop", headers=auth)
    assert fake.params["f.modified_dt.facet.range.gap"] == "+1YEAR"  # an invalid gap is ignored
    assert client.get("/api/analytics/overview?collection=b", headers=auth).status_code == 403
    assert analytics.overview_params(None, None, 5, "+1YEAR")["fq"] == []
