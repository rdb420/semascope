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
    import pyoxigraph as ox

    from oss_server.knowledge import store as store_mod

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
    # a second import of the same bundle is a no-op, unless forced
    first_concept_ids = set(Concept.objects.filter(pack=pack).values_list("id", flat=True))
    assert import_pack(BASE).pk == pack.pk
    assert set(Concept.objects.filter(pack=pack).values_list("id", flat=True)) == first_concept_ids
    assert import_pack(BASE, force=True).pk == pack.pk
    assert set(Concept.objects.filter(pack=pack).values_list("id", flat=True)) != first_concept_ids


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
    user = User.objects.create_user("k", password="z" * 16, is_staff=True)
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
    plain_user = User.objects.create_user("plain", password="z" * 16)
    plain = {"Authorization": f"Bearer {issue_token(plain_user, 't')}"}
    assert (
        client.post(
            "/api/knowledge/packs/semascope-base/sparql",
            data={"query": "SELECT * WHERE {?s ?p ?o}"},
            content_type="application/json",
            headers=plain,
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


@pytest.mark.django_db
def test_reconciliation_api(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    import pyoxigraph as ox

    from oss_server.knowledge import store as store_mod

    monkeypatch.setattr(store_mod, "store", lambda: _memory_store(ox))
    import_pack(REPO / "packs" / "demo" / "pack")
    manifest = client.get("/api/reconcile").json()
    assert manifest["versions"] == ["0.2"] and manifest["name"] == "Semascope"
    body = {
        "queries": {
            "q0": {"query": "Ulysses"},
            "q1": {"query": "jove", "limit": 1},
            "q2": {"query": "zzz"},
        }
    }
    out = client.post("/api/reconcile", data=body, content_type="application/json").json()
    assert out["q0"]["result"][0]["name"] == "Odysseus" and out["q0"]["result"][0]["match"] is True
    assert out["q1"]["result"][0]["name"] == "Zeus"
    assert out["q2"]["result"] == []
    form = client.post("/api/reconcile", data={"queries": '{"a": {"query": "Troy"}}'})
    assert form.json()["a"]["result"][0]["id"].endswith("#troy")


@pytest.mark.django_db
def test_deltas_export_as_bundle_fragment(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    import pyoxigraph as ox

    from oss_server.api import knowledge as kapi
    from oss_server.catalogue.models import Collection
    from oss_server.knowledge import store as store_mod

    monkeypatch.setattr(store_mod, "store", lambda: _memory_store(ox))
    monkeypatch.setattr(kapi, "_apply_draft_label", lambda *a, **k: None)
    user = User.objects.create_user("cur", password="w" * 16, is_staff=True)
    auth = {"Authorization": f"Bearer {issue_token(user, 't')}"}
    import_pack(REPO / "packs" / "demo" / "pack")
    Collection.objects.create(slug="texts", name="texts")
    r = client.post(
        "/api/knowledge/packs/semascope-demo/deltas",
        data={
            "collection": "texts",
            "kind": "add_label",
            "params": {"iri": "https://semascope.dev/ontology/demo#odysseus", "label": "Odusseus"},
            "reason": "Chapman's spelling",
        },
        content_type="application/json",
        headers=auth,
    )
    assert r.status_code == 201, r.content
    r = client.post(
        "/api/knowledge/packs/semascope-demo/deltas",
        data={
            "collection": "texts",
            "kind": "new_concept",
            "params": {
                "label": "Oracle",
                "scheme_slug": "themes",
                "definition": "Prophecy and its interpreters.",
            },
        },
        content_type="application/json",
        headers=auth,
    )
    assert r.status_code == 201, r.content
    bad = client.post(
        "/api/knowledge/packs/semascope-demo/deltas",
        data={"collection": "texts", "kind": "note", "params": {"iri": "https://nope"}},
        content_type="application/json",
        headers=auth,
    )
    assert bad.status_code == 422
    preview = client.get("/api/knowledge/packs/semascope-demo/deltas/export", headers=auth).json()
    assert all(
        d["status"] == "draft"
        for d in client.get("/api/knowledge/packs/semascope-demo/deltas", headers=auth).json()
    )
    files = client.post("/api/knowledge/packs/semascope-demo/deltas/export", headers=auth).json()
    assert files["taxonomy.delta.ttl"] == preview["taxonomy.delta.ttl"]
    assert 'skos:altLabel "Odusseus"@en' in files["taxonomy.delta.ttl"]
    assert "themes#oracle> a skos:Concept" in files["taxonomy.delta.ttl"]
    import yaml

    model = yaml.safe_load(files["model.delta.yaml"])
    assert model["terms"][0]["status"] == "draft" and model["terms"][0]["support"] == "proposed"
    assert all(
        d["status"] == "exported"
        for d in client.get("/api/knowledge/packs/semascope-demo/deltas", headers=auth).json()
    )


def test_slugify_folds_accents() -> None:
    from oss_server.knowledge.importer import slugify

    assert slugify("Väinämöinen") == "vainamoinen"
    assert slugify("Hero's journey") == "hero-s-journey"


def test_demo_pack_places_carry_coordinates() -> None:
    demo = REPO / "packs" / "demo" / "pack"
    rows = derive(demo, load_pack(demo))
    troy = next(r for r in rows if r["iri"].endswith("#troy"))
    assert abs(troy["extra"]["lat"] - 39.957) < 0.01 and abs(troy["extra"]["lon"] - 26.239) < 0.01
    asgard = next(r for r in rows if r["iri"].endswith("#asgard"))
    assert "lat" not in asgard["extra"]


def test_polyhierarchy_keeps_every_path_and_related_concepts(tmp_path: Path) -> None:
    """SEMA-071: a concept under two broader concepts has a path through each; skos:related
    is kept in both directions; the tree shows the concept under both parents."""

    import shutil

    pack = tmp_path / "pack"
    shutil.copytree(REPO / "packs" / "demo" / "pack", pack)
    with (pack / "taxonomy.ttl").open("a", encoding="utf-8") as fh:
        fh.write("\nth:descent skos:broader th:journey .\nth:flood skos:related th:creation .\n")
    rows = {r["iri"]: r for r in derive(pack, load_pack(pack, force=True))}
    th = "https://semascope.dev/ontology/demo/themes#"
    descent = rows[th + "descent"]
    assert descent["paths"] == [
        "afterlife/descent-to-the-underworld",
        "hero-s-journey/descent-to-the-underworld",
    ]
    assert descent["path"] == descent["paths"][0] and descent["depth"] == 1
    assert rows[th + "creation"]["related"] == [th + "flood"]
    assert rows[th + "flood"]["related"] == [th + "creation"]
    assert rows[th + "flood"]["paths"] == ["flood"]


@pytest.mark.django_db
def test_the_tree_and_dictionary_carry_every_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    import pyoxigraph as ox

    from oss_server.knowledge import store as store_mod

    monkeypatch.setattr(store_mod, "store", lambda: ox.Store())
    pack_dir = tmp_path / "pack"
    shutil.copytree(REPO / "packs" / "demo" / "pack", pack_dir)
    with (pack_dir / "taxonomy.ttl").open("a", encoding="utf-8") as fh:
        # a cycle as well: the import still ends
        fh.write("\nth:descent skos:broader th:journey .\nth:journey skos:broader th:descent .\n")
    pack = import_pack(pack_dir, force=True)
    tree = {n["label"]: n for n in hierarchy(pack, "themes")}
    assert [c["label"] for c in tree["Afterlife"]["children"]] == ["Descent to the underworld"]
    descent_iri = "https://semascope.dev/ontology/demo/themes#descent"
    entries = [e for e in dictionary_entries(pack) if e["iri"] == descent_iri]
    assert entries and all(len(e["paths"]) >= 1 for e in entries)
    stored = Concept.objects.get(pack=pack, iri=descent_iri)
    assert stored.paths[0] == "afterlife/descent-to-the-underworld"
