"""Keyword search: query fields and languages, operators, filters, dates, sorting, caps and
the facet lookups (docs/search.md). Solr is faked; tests/integration/test_search_languages.py
runs the same features against a real stack."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client

from oss_pipeline.languages import LANGUAGES
from oss_server.api import search as search_mod
from oss_server.api.schemas import SearchIn
from oss_server.api.search import NEWEST_FIRST, build_params, prepare_query, query_fields

# --- query fields ------------------------------------------------------------------------------


def test_keyword_search_queries_every_languages_stemmed_fields() -> None:
    qf = build_params(SearchIn(q="Kinder"))["qf"].split()
    fields = {f.split("^")[0] for f in qf}
    assert {"title_txt", "text_txt"} <= fields
    for code in LANGUAGES:
        assert {f"title_txt_{code}", f"text_txt_{code}"} <= fields, code
    # an exact match weighs more than a stemmed one
    weight = {f.split("^")[0]: float(f.split("^")[1]) for f in qf}
    assert (
        weight["text_txt"] > weight["text_txt_de"] and weight["title_txt"] > weight["title_txt_en"]
    )


def test_stemming_off_and_the_exact_alias_use_the_neutral_fields_only() -> None:
    off = build_params(SearchIn(q="Kinder", stemming=False))
    assert {f.split("^")[0] for f in off["qf"].split()} == {"title_txt", "text_txt", "uri_s"}
    on = query_fields(True)
    assert {f.split("^")[0] for f in on["f.exact.qf"].split()} == {"title_txt", "text_txt"}
    assert "text_txt_fr" in on["f.text.qf"] and "title_txt_fr" in on["f.title.qf"]
    assert "text_txt" not in {f.split("^")[0] for f in on["f.title.qf"].split()}


def test_solrconfig_defaults_match_the_api_query_fields() -> None:
    from oss_server.services import configset_dir

    conf = (configset_dir("documents") / "solrconfig.xml").read_text()
    for name, value in query_fields(True).items():
        assert f'<str name="{name}">{value}</str>' in conf, name


# --- operators ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        # a lone OR chain: Solr would unwrap the group, so mm is relaxed to one clause
        ("Odysseus OR Arjuna", ("Odysseus OR Arjuna", "1")),
        ("a OR b OR c", ("a OR b OR c", "1")),
        # OR joins its neighbours; the other words stay required
        ("odysseus ithaca OR penelope", ("odysseus (ithaca OR penelope)", None)),
        ("a AND b OR c", ("a AND (b OR c)", None)),
        ('"sailed home" OR title:(a b) c', ('("sailed home" OR title:(a b)) c', None)),
        ("x OR -y z", ("(x OR -y) z", None)),
        # nothing to do
        ("odysseus ithaca", ("odysseus ithaca", None)),
        ("(a OR b) c", ("(a OR b) c", None)),
        ("odysseus or arjuna", ("odysseus or arjuna", None)),  # lower case: words
        ('"Odysseus OR Arjuna"', ('"Odysseus OR Arjuna"', None)),  # inside a phrase
        ('broken "quote OR x', ('broken "quote OR x', None)),  # unbalanced: left to Solr
    ],
)
def test_explicit_or_is_an_alternative_not_two_required_words(
    q: str, expected: tuple[str, str | None]
) -> None:
    assert prepare_query(q) == expected


def test_the_operator_switch() -> None:
    assert prepare_query("a b", "all") == ("a b", "100%")
    assert prepare_query("a b", "any") == ("a b", "1")
    assert prepare_query('a "b', "phrase") == ('"a \\"b"', None)
    assert prepare_query('"a b"', "phrase") == ('"a b"', None)
    assert build_params(SearchIn(q="Odysseus OR Arjuna"))["mm"] == "1"
    assert "mm" not in build_params(SearchIn(q="Odysseus Arjuna"))  # solrconfig's default


# --- filters, dates, sorting --------------------------------------------------------------------


def test_excluded_values_are_negative_filters_tagged_for_their_facet() -> None:
    params = build_params(
        SearchIn(
            filters={"language_s": ["en"]},
            exclude={"persons_ss": ["Zeus", 'x" OR *:* OR "y'], "bad field": ["v"]},
        )
    )
    assert '{!tag=persons_ss}(*:* -persons_ss:("Zeus" OR "x\\" OR *:* OR \\"y"))' in params["fq"]
    assert '{!tag=language_s}language_s:("en")' in params["fq"]
    assert not any("bad field" in f for f in params["fq"])


def test_a_date_range_filters_and_narrows_the_histogram() -> None:
    req = SearchIn.model_validate(
        {
            "histogram": "modified_dt",
            "histogram_gap": "+1MONTH",
            "dates": {"field": "modified_dt", "start": "1920-01-01", "end": "1921-01-01"},
        }
    )
    params = build_params(req)
    assert "modified_dt:[1920-01-01T00:00:00Z TO 1921-01-01T00:00:00Z}" in params["fq"]
    assert params["facet.range.start"] == "1920-01-01T00:00:00Z"
    assert params["facet.range.end"] == "1921-01-01T00:00:00Z"
    assert params["facet.range.gap"] == "+1MONTH"
    open_end = build_params(SearchIn.model_validate({"dates": {"start": "2020-05-01"}}))
    assert "modified_dt:[2020-05-01T00:00:00Z TO *}" in open_end["fq"]
    # only date fields; anything else is ignored
    other = SearchIn.model_validate({"dates": {"field": "size_l", "start": "2020-01-01"}})
    assert not any("size_l" in f for f in build_params(other)["fq"])


@pytest.mark.parametrize("value", ["yesterday", "2020-13-01", "1920-01-01T00:00:00Z} OR *:*"])
def test_dates_must_be_iso_dates(value: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SearchIn.model_validate({"dates": {"start": value}})


def test_a_search_without_words_is_newest_first_and_sorts_are_allow_listed() -> None:
    assert build_params(SearchIn(q="*:*"))["sort"] == NEWEST_FIRST
    assert build_params(SearchIn(q=""))["sort"] == NEWEST_FIRST
    assert "sort" not in build_params(SearchIn(q="flood"))  # relevance
    assert build_params(SearchIn(q="flood", sort="modified_dt asc"))["sort"] == "modified_dt asc"
    assert build_params(SearchIn(q="*:*", sort="title_s desc"))["sort"] == "title_s desc"
    assert build_params(SearchIn(q="*:*", sort="id desc; drop"))["sort"] == NEWEST_FIRST


def test_show_more_lists_more_values_of_one_facet() -> None:
    params = build_params(
        SearchIn(facets=["persons_ss", "places_ss"], facet_more=["persons_ss", "x} bad"])
    )
    assert params["f.persons_ss.facet.limit"] == 100
    assert "f.places_ss.facet.limit" not in params
    assert not any("bad" in k for k in params)


# --- caps --------------------------------------------------------------------------------------


def test_a_page_starts_at_page_times_the_rows_actually_returned() -> None:
    params = build_params(SearchIn(page=3, page_size=100))
    assert params["rows"] == 100 and params["start"] == 200
    count = build_params(SearchIn(page_size=0))
    assert count["rows"] == 0 and count["hl"] is False


@pytest.mark.parametrize(
    "body",
    [{"page_size": 101}, {"page": 0}, {"page": 501}, {"facet_limit": 0}, {"facet_limit": -1}],
)
def test_paging_and_facet_limits_are_bounded(body: dict[str, Any]) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SearchIn.model_validate(body)


def test_pages_beyond_ten_thousand_hits_are_refused() -> None:
    from ninja.errors import HttpError

    with pytest.raises(HttpError) as err:
        build_params(SearchIn(page=102, page_size=100))
    assert err.value.status_code == 422
    build_params(SearchIn(page=101, page_size=100))  # starts at 10,000: still allowed


# --- hybrid fusion beyond 100 keyword hits -------------------------------------------------------


class ManyDocs:
    """150 documents; the chunks rank them in reverse, keyword search in order."""

    ids = [f"d{i:03d}" for i in range(150)]

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        self.calls.append({"collection": collection, **params})
        if collection == "chunks":
            docs = [{"id": f"{d}-0", "document_id": d, "text_txt": d} for d in self.ids[::-1]]
            return {"response": {"numFound": len(docs), "docs": docs[: params["rows"]]}}
        q = str(params["q"])
        if q.startswith("{!terms f=id}"):
            ids = q.removeprefix("{!terms f=id}").split(",")
        else:
            start, rows = int(params.get("start", 0)), int(params["rows"])
            ids = self.ids[start : start + rows]
        docs = [{"id": d, "title_txt": d, "collection_s": "a"} for d in ids]
        return {"response": {"numFound": len(docs), "docs": docs}, "facet_counts": {}}


class Embedder:
    model_name = "m"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384]


def test_hybrid_fuses_the_whole_keyword_list_not_its_first_hundred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(search_mod, "query_embedder", lambda: Embedder())
    monkeypatch.setattr(search_mod, "pack_facets", lambda: [])
    monkeypatch.setattr(search_mod, "path_facets", lambda *a: [])
    solr = ManyDocs()
    run: Any = search_mod.search
    out = run(solr, "documents", SearchIn(q="flood", mode="hybrid", page_size=50))
    keyword = [c for c in solr.calls if c["collection"] == "documents" and c.get("fl") == "id"]
    assert keyword and keyword[0]["rows"] >= 150 and keyword[0]["start"] == 0
    assert out.total == 150
    # every document is fused from both ranks, those beyond the hundredth keyword hit too
    n = len(ManyDocs.ids)
    fused = {d: 1 / (60 + n - 1 - i) + 1 / (60 + i) for i, d in enumerate(ManyDocs.ids)}
    assert "d149" in {h.id for h in out.hits}  # the last keyword hit, the first semantic one
    for hit in out.hits:
        assert hit.score == round(fused[hit.id], 5), hit.id


def test_semantic_candidates_are_capped() -> None:
    assert search_mod.MAX_FUSED == 1000
    # the largest page asks for at most MAX_FUSED candidates
    assert min(max(200, 100 * 20), search_mod.MAX_FUSED) == 1000


# --- facet lookups -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_pack_facets_are_looked_up_once_per_active_pack_set(
    django_assert_max_num_queries: Any,
) -> None:
    from oss_server.knowledge.models import Concept, Pack

    search_mod.clear_facet_caches()
    pack = Pack.objects.create(
        pack_id="p",
        version="1",
        title="P",
        base_iri="https://example.org/p#",
        status=Pack.Status.ACTIVE,
        manifest_hash="m",
        graph_iri="urn:p",
    )
    Concept.objects.create(
        pack=pack, iri="https://example.org/p#a", kind="concept", pref_label="A", scheme_slug="s"
    )
    first = search_mod.pack_facets()
    assert "s_paths" in first
    with django_assert_max_num_queries(1):  # the active-pack key only
        assert search_mod.pack_facets() == first
    pack.status = Pack.Status.RETIRED
    pack.save()
    assert "s_paths" not in search_mod.pack_facets()


class Luke:
    def __init__(self, fields: list[str]) -> None:
        self.fields = fields
        self.gets = 0

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        assert path.endswith("/admin/luke")
        self.gets += 1
        return {"fields": {f: {} for f in self.fields}}


@pytest.mark.django_db
def test_folder_path_fields_become_tree_facets_whatever_their_name() -> None:
    from oss_server.knowledge.models import Concept, Pack

    search_mod.clear_facet_caches()
    pack = Pack.objects.create(
        pack_id="p",
        version="1",
        title="P",
        base_iri="https://example.org/p#",
        manifest_hash="m",
        graph_iri="urn:p",
    )
    Concept.objects.create(
        pack=pack, iri="https://example.org/p#a", kind="concept", pref_label="A", scheme_slug="s"
    )
    solr: Any = Luke(["text_txt", "folder_paths", "s_paths", "bad name_paths", "x_owner_s"])
    assert search_mod.path_facets(solr, "documents") == ["folder_paths"]
    assert search_mod.path_facets(solr, "documents") == ["folder_paths"]
    assert solr.gets == 1  # cached
    params = build_params(SearchIn(facets=["folder_paths"], filters={"folder_paths": ["a/b"]}))
    assert params["facet.field"] == ["{!ex=folder_paths}folder_paths"]
    assert '{!tag=folder_paths}folder_paths:("a/b")' in params["fq"]


@pytest.mark.django_db
def test_a_search_that_asks_for_no_facets_gets_none(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class Solr:
        def select(self, collection: str, **params: Any) -> dict[str, Any]:
            calls.append(params)
            return {"response": {"numFound": 0, "docs": []}}

    monkeypatch.setattr(search_mod, "pack_facets", lambda: ["s_paths"])
    monkeypatch.setattr(search_mod, "path_facets", lambda *a: ["folder_paths"])
    run: Any = search_mod.search
    run(Solr(), "documents", SearchIn(facets=[]))
    assert "facet.field" not in calls[0]
    run(Solr(), "documents", SearchIn(facets=["language_s"]))
    assert calls[1]["facet.field"] == [
        "{!ex=language_s}language_s",
        "{!ex=s_paths}s_paths",
        "{!ex=folder_paths}folder_paths",
    ]


# --- the API -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_api_refuses_oversized_pages(client: Client) -> None:
    from django.contrib.auth.models import User

    from oss_server.api.auth import issue_token

    user = User.objects.create_user("root", password="x" * 16, is_staff=True)
    auth = {"Authorization": f"Bearer {issue_token(user, 'test')}"}
    for body in ({"page_size": 1000}, {"facet_limit": -1}, {"page": 0}):
        r = client.post("/api/search", data=body, content_type="application/json", headers=auth)
        assert r.status_code == 422, (body, r.content)
