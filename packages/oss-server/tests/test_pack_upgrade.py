"""Pack upgrades as a diff and a reindex plan, confirmed activation, and removing a version."""

import shutil
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_core.pack import load_pack, write_manifest
from oss_server.api.auth import issue_token
from oss_server.knowledge.importer import derive, dictionary_entries, import_pack
from oss_server.knowledge.models import Concept, Pack
from oss_server.knowledge.upgrade import diff_rows

REPO = Path(__file__).resolve().parents[3]
DEMO = REPO / "packs" / "demo" / "pack"
TR = "https://semascope.dev/ontology/demo/traditions#"
EX = "https://semascope.dev/ontology/demo#"


def _row(iri: str, **kw: Any) -> dict[str, Any]:
    base = {
        "iri": iri,
        "kind": "concept",
        "pref_label": iri.rsplit("#", 1)[-1],
        "alt_labels": [],
        "hidden_labels": [],
        "taggable": True,
        "extra": {},
    }
    return {**base, **kw}


def test_diff_rows_reports_terms_labels_and_taggability() -> None:
    old = [
        _row("u:a"),
        _row("u:b", alt_labels=["Bee"]),
        _row("u:c", taggable=False),
        _row("u:d", definition="old"),
        _row("u:k", kind="class", taggable=False),
    ]
    new = [
        _row("u:b", alt_labels=["Bee", "Bea"]),
        _row("u:c"),
        _row("u:d", definition="new"),
        _row("u:e", kind="individual"),
        _row("u:k", kind="class", taggable=False, broader=["u:top"]),
    ]
    d = diff_rows(old, new)
    s = d["summary"]
    assert s["added"] == {"individual": 1} and s["removed"] == {"concept": 1}
    assert s["changed"] == {"concept": 3, "class": 1}
    assert s["became_taggable"] == 1 and s["no_longer_taggable"] == 0
    # Bea, c's label (now taggable), e's label added; a's label removed
    assert s["labels_added"] == 3 and s["labels_removed"] == 1
    fields = {c["iri"]: c["fields"] for c in d["changed"]}
    assert fields["u:b"] == {"alt_labels": [["Bee"], ["Bea", "Bee"]]}
    assert fields["u:d"] == {"definition": ["old", "new"]}
    # only tags that exist can be affected: removed a, b's labels; not c (was not taggable),
    # not d (definition only), not the class
    assert d["tag_affecting_iris"] == ["u:a", "u:b"]


class FakeSolr:
    def __init__(self) -> None:
        self.selects: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        self.selects.append(params)
        fq = " ".join(params.get("fq") or [])
        if "pack_versions_ss" in fq:
            flat: list[Any] = ["texts", 7]
        elif "{!bool" in fq:
            assert f"{TR}islam" in params["sem_iris"].split(" ")
            flat = ["texts", 2]
        else:
            flat = ["texts", 10, "elsewhere", 3]
        return {
            "response": {"numFound": 0, "docs": []},
            "facet_counts": {"facet_fields": {"collection_s": flat}},
        }

    def count(self, collection: str, query: str = "*:*") -> int:
        return 4

    def add(self, *args: Any, **kwargs: Any) -> None:
        return None

    def delete_by_query(self, collection: str, query: str) -> None:
        self.deleted.append(query)

    def commit(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import pyoxigraph as ox

    from oss_server import services
    from oss_server.catalogue.models import Collection
    from oss_server.knowledge import store as store_mod
    from oss_server.pipeline import tasks

    memory = ox.Store()
    monkeypatch.setattr(store_mod, "store", lambda: memory)
    solr = FakeSolr()
    monkeypatch.setattr(services, "solr_client", lambda: solr)
    queued: list[str] = []
    monkeypatch.setattr(tasks.reindex_collection, "delay", lambda slug: queued.append(slug))
    monkeypatch.setenv("SEMASCOPE_DATA_DIR", str(tmp_path / "data"))

    candidate = tmp_path / "data" / "packs" / "demo-next"
    shutil.copytree(DEMO, candidate)
    _edit(candidate / "requirements.yaml", "version: 0.1.0", "version: 0.2.0")
    tax = candidate / "taxonomy.ttl"
    tax.write_text(
        "\n".join(line for line in tax.read_text().splitlines() if not line.startswith("tr:islam "))
        + "\n"
    )
    _edit(
        tax,
        'skos:prefLabel "Judaism"@en ;',
        'skos:prefLabel "Judaism"@en, "Judentum"@de ; skos:notation "JUD" ;',
    )
    _edit(
        candidate / "instances.ttl",
        '"Jupiter"@en, "Jove"@en',
        '"Jupiter"@en, "Jove"@en, "Zeus Pater"@en',
    )
    write_manifest(load_pack(candidate, force=True))

    import_pack(DEMO)
    Collection.objects.create(slug="texts", name="texts")
    admin = User.objects.create_user("adm", password="a" * 16, is_staff=True)
    return {
        "candidate": candidate,
        "solr": solr,
        "queued": queued,
        "store": memory,
        "admin": {"Authorization": f"Bearer {issue_token(admin, 't')}"},
    }


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


@pytest.mark.django_db
def test_candidate_keeps_notation_and_other_language_labels(world: dict[str, Any]) -> None:
    candidate = world["candidate"]
    rows = {r["iri"]: r for r in derive(candidate, load_pack(candidate))}
    judaism = rows[f"{TR}judaism"]
    assert judaism["pref_label"] == "Judaism" and judaism["lang"] == "en"
    assert judaism["extra"]["notation"] == ["JUD"]
    assert judaism["extra"]["pref_labels"] == [{"label": "Judentum", "lang": "de"}]
    pack = import_pack(candidate, activate=False)
    entries = [e for e in dictionary_entries(pack) if e["iri"] == f"{TR}judaism"]
    german = [e for e in entries if e["label"] == "Judentum"]
    assert german and german[0]["lang"] == "de" and german[0]["label_type"] == "pref"
    assert len({e["id"] for e in entries}) == len(entries)


@pytest.mark.django_db
def test_diff_and_plan_before_an_upgrade(client: Client, world: dict[str, Any]) -> None:
    r = client.post(
        "/api/knowledge/packs/diff",
        data={"path": str(world["candidate"])},
        content_type="application/json",
        headers=world["admin"],
    )
    assert r.status_code == 200, r.content
    out = r.json()
    assert out["active_version"] == "0.1.0" and out["candidate_version"] == "0.2.0"
    assert out["requires_confirmation"] and out["changes"]
    summary = out["diff"]["summary"]
    assert summary["removed"] == {"concept": 1}
    assert summary["labels_added"] == 2 and summary["labels_removed"] >= 4  # Islam + alt labels
    changed = {c["iri"]: c["fields"] for c in out["diff"]["changed"]}
    assert set(changed[f"{EX}zeus"]) == {"alt_labels"}
    assert set(changed[f"{TR}judaism"]) == {"pref_labels", "notation"}
    plan = out["plan"]
    assert plan["reindex"] == ["texts"]
    by_slug = {c["collection"]: c for c in plan["collections"]}
    assert by_slug["texts"] == {
        "collection": "texts",
        "reindex": True,
        "documents": 10,
        "tagged_documents": 7,
        "affected_documents": 2,
    }
    tagged_fq = [s["fq"] for s in world["solr"].selects if "pack_versions_ss" in " ".join(s["fq"])]
    assert tagged_fq == [['pack_versions_ss:"semascope-demo@0.1.0"']]


@pytest.mark.django_db
def test_activation_needs_confirmation_then_rolls_back(
    client: Client, world: dict[str, Any]
) -> None:
    body = {"path": str(world["candidate"]), "activate": True}
    refused = client.post(
        "/api/knowledge/packs/import",
        data=body,
        content_type="application/json",
        headers=world["admin"],
    )
    assert refused.status_code == 409, refused.content
    assert refused.json()["plan"]["diff"]["summary"]["removed"] == {"concept": 1}
    assert not Pack.objects.filter(version="0.2.0").exists(), "nothing imported without confirm"
    assert world["queued"] == []

    done = client.post(
        "/api/knowledge/packs/import",
        data={**body, "confirm": True},
        content_type="application/json",
        headers=world["admin"],
    )
    assert done.status_code == 201, done.content
    assert done.json()["status"] == "active" and done.json()["plan"]["active_version"] == "0.1.0"
    assert Pack.objects.get(version="0.1.0").status == Pack.Status.RETIRED
    assert world["queued"] == ["texts"]

    # roll back: the same rule applies
    back = "/api/knowledge/packs/semascope-demo/activate?version=0.1.0"
    assert client.post(back, headers=world["admin"]).status_code == 409
    diff = client.get(
        "/api/knowledge/packs/semascope-demo/diff?version=0.1.0", headers=world["admin"]
    ).json()
    assert diff["diff"]["summary"]["added"] == {"concept": 1}
    ok = client.post(back + "&confirm=true", headers=world["admin"])
    assert ok.status_code == 200 and ok.json()["status"] == "active"
    # activating the active version again is not a change
    again = client.post(back, headers=world["admin"])
    assert again.status_code == 200 and not again.json()["plan"]["requires_confirmation"]


@pytest.mark.django_db
def test_remove_a_version(client: Client, world: dict[str, Any]) -> None:
    import pyoxigraph as ox

    from oss_server.knowledge import store as store_mod

    old = import_pack(world["candidate"], activate=False)
    graph = ox.NamedNode(store_mod.graph_iri(old.pack_id, old.version))
    assert graph in list(world["store"].named_graphs())
    url = "/api/knowledge/packs/semascope-demo/versions/0.2.0"
    r = client.delete(url, headers=world["admin"])
    assert r.status_code == 200, r.content
    out = r.json()
    assert out["concepts_removed"] > 50 and out["documents_still_tagged"] == 4
    assert not Pack.objects.filter(version="0.2.0").exists()
    assert not Concept.objects.filter(pack_id=old.pk).exists()
    assert graph not in list(world["store"].named_graphs())
    assert world["solr"].deleted == ['pack_id:"semascope-demo" AND pack_version:"0.2.0"']
    assert client.delete(url, headers=world["admin"]).status_code == 404
    active = client.delete(
        "/api/knowledge/packs/semascope-demo/versions/0.1.0", headers=world["admin"]
    )
    assert active.status_code == 409


def test_the_store_is_closed_between_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another process (a second server worker) can open the store after an operation:
    holding it open locked every other worker out of pack operations."""

    import subprocess
    import sys

    from oss_server.knowledge import store as store_mod

    monkeypatch.setenv("SEMASCOPE_DATA_DIR", str(tmp_path))
    info = load_pack(DEMO)
    assert store_mod.load_bundle(info.rdf, info.id, info.version) > 100
    assert store_mod.query("ASK { ?s ?p ?o }", pack_id=info.id, version=info.version)
    other = subprocess.run(  # noqa: S603 - our interpreter, a fixed script
        [
            sys.executable,
            "-c",
            "import sys, pyoxigraph as ox; s = ox.Store(sys.argv[1]); "
            "print(len(list(s.named_graphs())))",
            str(tmp_path / "packs.oxigraph"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert other.returncode == 0 and other.stdout.strip() == "1", other.stderr
    store_mod.drop_graph(info.id, info.version)
    assert (
        store_mod.query("SELECT * WHERE { ?s ?p ?o }", pack_id=info.id, version=info.version) == []
    )


def _load_in_child(data_dir: str) -> None:
    import os

    os.environ["SEMASCOPE_DATA_DIR"] = data_dir
    from oss_server.knowledge import store as store_mod

    info = load_pack(DEMO)
    store_mod.load_bundle(info.rdf, info.id, "fork")


def test_a_forked_worker_can_write_after_the_parent_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard: the gunicorn master imports the bundled packs, then forks its workers, and a
    worker's write then hung on a live stack (RocksDB threads do not survive a fork). This
    test does not reproduce that hang reliably; the live stack check in the hardening
    report does. It keeps the fork-then-write path working."""

    import multiprocessing

    from oss_server.knowledge import store as store_mod

    monkeypatch.setenv("SEMASCOPE_DATA_DIR", str(tmp_path))
    info = load_pack(DEMO)
    store_mod.load_bundle(info.rdf, info.id, info.version)
    child = multiprocessing.get_context("fork").Process(
        target=_load_in_child, args=(str(tmp_path),)
    )
    child.start()
    child.join(timeout=60)
    if child.is_alive():
        child.kill()
        pytest.fail("a write in a forked process hung")
    assert child.exitcode == 0
    rows = store_mod.query(
        "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }", pack_id=info.id, version="fork"
    )
    assert int(rows[0]["n"]) > 100


@pytest.mark.django_db
def test_unchanged_import_with_activation_activates(world: dict[str, Any]) -> None:
    newer = import_pack(world["candidate"])
    assert newer.status == Pack.Status.ACTIVE
    old = import_pack(DEMO)  # unchanged: skipped, but activation is still asked for
    old.refresh_from_db()
    newer.refresh_from_db()
    assert old.status == Pack.Status.ACTIVE and newer.status == Pack.Status.RETIRED
