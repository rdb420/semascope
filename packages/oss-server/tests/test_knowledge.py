from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_core.pack import load_pack
from oss_server.api.auth import issue_token
from oss_server.knowledge.importer import derive, dictionary_entries, hierarchy, import_pack
from oss_server.knowledge.models import Concept, Pack

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "packs" / "base"


def test_derive_base_pack_vocabulary() -> None:
    info = load_pack(BASE)
    rows = derive(BASE, info)
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    assert by_kind["scheme"] == 1 and by_kind["concept"] == 11 and by_kind["class"] >= 19
    person = next(r for r in rows if r["iri"].endswith("#Person"))
    assert person["kind"] == "class" and person["path"] == "entity/person" and person["depth"] == 1
    assert not person["taggable"]
    mail = next(r for r in rows if r["iri"].endswith("content-type-group#mail"))
    assert mail["alt_labels"] == ["Email"] and mail["scheme_slug"] == "content-type-group"
    assert not mail["taggable"], "the base pack declares tag_schemes: []"
    troy = next(r for r in rows if r["iri"].endswith("entity-troy"))
    assert (
        troy["kind"] == "individual"
        and troy["type_label"] == "Place"
        and troy["pref_label"] == "Troy"
    )


@pytest.mark.django_db
def test_import_base_pack_runs_cq_tests_and_builds_dictionary(
    tmp_path: Path, settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.knowledge import store as store_mod

    monkeypatch.setattr(store_mod, "_store", None)
    import pyoxigraph as ox

    monkeypatch.setattr(store_mod, "store", lambda: _memory_store(ox))
    pack = import_pack(BASE)
    assert pack.status == Pack.Status.ACTIVE
    assert (
        pack.counts["triples"] > 100
        and pack.counts["terms"] == Concept.objects.filter(pack=pack).count()
    )
    assert pack.cq_results["tests"] == 3 and pack.cq_results["passed"] == 3, pack.cq_results
    assert dictionary_entries(pack) == [], "the base pack tags nothing by policy"
    tree = hierarchy(pack, "classes")
    entity = next(n for n in tree if n["label"] == "Entity")
    assert any(child["label"] == "Person" for child in entity["children"])
    # a second import of the same bundle is a no-op
    assert import_pack(BASE).pk == pack.pk


_MEM: object | None = None


def _memory_store(ox: object) -> object:
    global _MEM
    if _MEM is None:
        _MEM = ox.Store()  # type: ignore[attr-defined]
    return _MEM


@pytest.mark.django_db
def test_knowledge_api(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    import pyoxigraph as ox

    from oss_server.knowledge import store as store_mod

    monkeypatch.setattr(store_mod, "store", lambda: _memory_store(ox))
    user = User.objects.create_user("k", password="z" * 16)
    auth = {"Authorization": f"Bearer {issue_token(user, 't')}"}
    import_pack(BASE)
    packs = client.get("/api/knowledge/packs", headers=auth).json()
    assert packs[0]["pack_id"] == "semascope-base" and packs[0]["status"] == "active"
    detail = client.get("/api/knowledge/packs/semascope-base", headers=auth).json()
    assert detail["schemes"][0]["slug"] == "content-type-group"
    concepts = client.get(
        "/api/knowledge/packs/semascope-base/concepts?scheme=content-type-group", headers=auth
    ).json()
    assert len(concepts) == 10
    one = client.get("/api/knowledge/concepts", {"iri": concepts[0]["iri"]}, headers=auth).json()
    assert one["pref_label"] == concepts[0]["pref_label"]
    assert (
        client.post(
            "/api/knowledge/packs/semascope-base/sparql",
            data={"query": "SELECT * WHERE {?s ?p ?o}"},
            content_type="application/json",
            headers=auth,
        ).status_code
        == 403
    )


@pytest.mark.django_db
def test_stage_context_carries_active_pack_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    import pyoxigraph as ox

    from oss_server.catalogue.models import Collection
    from oss_server.knowledge import store as store_mod
    from oss_server.knowledge.models import PackBinding
    from oss_server.pipeline.tasks import build_context

    monkeypatch.setattr(store_mod, "store", lambda: _memory_store(ox))
    import_pack(BASE)
    c = Collection.objects.create(slug="ctx", name="ctx")
    ctx = build_context(c)
    assert ctx.pack_versions == {"semascope-base": "0.1.0"}
    assert ctx.settings["solr_entities"] == "entities"
    PackBinding.objects.create(collection=c, pack_id="not-imported")
    assert build_context(c).pack_versions == {}, "a binding to an inactive pack yields nothing"
