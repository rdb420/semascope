"""Runtime wiring on the server: stage subsets, pack-class entity labels, chunk model stats."""

from __future__ import annotations

from pathlib import Path

import pytest
from ninja.errors import HttpError

from oss_server.api.root import _stage_list
from oss_server.knowledge.models import Concept, Pack
from oss_server.pipeline.tasks import active_entity_labels, replay_set, stage_settings


def test_stage_list_accepts_profile_stages_only() -> None:
    assert _stage_list("embed, ner") == ["embed", "ner"]
    assert _stage_list(None) == []
    with pytest.raises(HttpError):
        _stage_list("embed,typo")


def test_entity_labels_come_from_active_pack_classes(db: None) -> None:
    pack = Pack.objects.create(
        pack_id="myth",
        version="1",
        title="Myth",
        base_iri="https://x/myth#",
        manifest_hash="m",
        graph_iri="g:myth",
        status=Pack.Status.ACTIVE,
    )
    Concept.objects.create(
        pack=pack,
        iri="https://x/myth#Deity",
        kind=Concept.Kind.CLASS,
        pref_label="Deity",
        definition="A god or goddess of a tradition.",
    )
    Concept.objects.create(
        pack=pack, iri="https://x/myth#hero", kind=Concept.Kind.INDIVIDUAL, pref_label="Odysseus"
    )
    off = Pack.objects.create(
        pack_id="old",
        version="1",
        title="Old",
        base_iri="https://x/old#",
        manifest_hash="o",
        graph_iri="g:old",
        status=Pack.Status.IMPORTED,
    )
    Concept.objects.create(
        pack=off, iri="https://x/old#Thing", kind=Concept.Kind.CLASS, pref_label="Thing"
    )
    labels = active_entity_labels()
    assert labels == {
        "deity": {"class": "Deity", "description": "A god or goddess of a tradition."}
    }


def test_stage_settings_carry_the_providers(db: None) -> None:
    from oss_server.catalogue.models import Collection

    c = Collection.objects.create(slug="c", name="C")
    s = stage_settings(c)
    assert s["ner_provider"] == "spacy" and s["embedder"] == "fastembed"
    assert s["extract_provider"] == "tika" and "ner_labels" in s


def test_replay_set_adds_followers_and_publishers() -> None:
    assert replay_set(None) == set()
    assert replay_set(["embed"]) == {"structure", "embed", "index", "graph"}
    assert replay_set(["ner"]) == {"ner", "link", "index", "graph"}


def test_ner_labels_and_shapes_come_from_the_collections_bound_packs(
    db: None, tmp_path: Path
) -> None:
    from oss_server.catalogue.models import Collection
    from oss_server.knowledge.models import PackBinding

    shapes: dict[str, Path] = {}
    for pid, cls in (("myth", "Deity"), ("law", "Easement")):
        folder = tmp_path / pid
        folder.mkdir()
        (folder / "shapes.ttl").write_text(f"# shapes of {pid}\n")
        shapes[pid] = folder
        pack = Pack.objects.create(
            pack_id=pid,
            version="1",
            title=pid,
            base_iri=f"https://x/{pid}#",
            manifest_hash=pid,
            graph_iri=f"g:{pid}",
            status=Pack.Status.ACTIVE,
            source_path=str(folder),
        )
        Concept.objects.create(
            pack=pack, iri=f"https://x/{pid}#{cls}", kind=Concept.Kind.CLASS, pref_label=cls
        )
    bound = Collection.objects.create(slug="bound", name="bound")
    PackBinding.objects.create(collection=bound, pack_id="myth")
    open_collection = Collection.objects.create(slug="open", name="open")

    settings = stage_settings(bound)
    assert set(settings["ner_labels"]) == {"deity"}
    assert "shapes of myth" in settings["shapes_ttl"]
    assert "shapes of law" not in settings["shapes_ttl"]
    every = stage_settings(open_collection)  # no bindings: every active pack
    assert set(every["ner_labels"]) == {"deity", "easement"}
    assert "shapes of law" in every["shapes_ttl"]
