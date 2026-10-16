"""Query-time synonyms from the active packs (ADR-0003; docs/search.md#synonyms)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from oss_server.knowledge import synonyms
from oss_server.knowledge.models import Concept, Pack
from oss_server.knowledge.synonyms import fold, stopwords, synonym_lines, words

STOP = {"the", "and", "us", "will"}


def test_an_individuals_names_are_one_equivalence_line() -> None:
    assert synonym_lines(["Odysseus", "Ulysses"], STOP) == ["Odysseus, Ulysses"]
    # accents: the folded form joins, so a query typed without them expands too
    assert synonym_lines(["Väinämöinen", "Wainamoinen"], STOP) == [
        "Vainamoinen, Väinämöinen, Wainamoinen"
    ]
    assert synonym_lines(["Straße", "Strasse"], STOP) == ["Strasse, Straße"]


def test_a_concepts_other_labels_come_in_at_lower_weight() -> None:
    lines = synonym_lines(["Flood", "Deluge", "Great flood"], STOP, 0.5)
    assert lines == [
        "Flood => Flood, Deluge|0.5, Great|0.5 flood|0.5",
        "Deluge => Deluge, Flood|0.5, Great|0.5 flood|0.5",
        "Great flood => Great flood, Flood|0.5, Deluge|0.5",
    ]


def test_labels_are_written_as_the_query_tokenizer_splits_them() -> None:
    assert words("Bhagavad-Gita") == "Bhagavad Gita"
    assert words("St. Paul") == "St Paul"
    assert words("Qur'an") == "Qur'an"
    assert words("a, b => c | d") == "a b c d"  # nothing the rule format would read
    assert words("東京大学") == "東 京 大 学"
    assert fold("Œdipe Ærø") == "OEdipe AEro"


def test_stop_words_short_labels_and_acronyms_do_not_expand() -> None:
    # "US" would expand every lower-cased "us"; "The" every "the"
    assert synonym_lines(["United States", "US", "USA"], STOP) == []
    assert synonym_lines(["Will", "Testament"], STOP) == []
    assert synonym_lines(["Ra", "Re"], STOP) == []
    assert synonym_lines(["Odysseus"], STOP) == []  # nothing to expand to


def test_every_languages_stop_words_are_read() -> None:
    words_ = stopwords()
    assert {"the", "und", "les", "della", "и"} <= words_
    assert not any(w.startswith(("|", "#")) for w in words_)
    german = stopwords({"de"})
    assert "und" in german and "the" not in german


def _pack(pack_id: str, status: str) -> Pack:
    return Pack.objects.create(
        pack_id=pack_id,
        version="1",
        title=pack_id,
        base_iri=f"https://example.org/{pack_id}#",
        status=status,
        manifest_hash=pack_id,
        graph_iri=f"urn:{pack_id}",
    )


@pytest.mark.django_db
def test_the_file_holds_the_active_packs_taggable_terms_only() -> None:
    active = _pack("demo", Pack.Status.ACTIVE)
    retired = _pack("old", Pack.Status.RETIRED)
    iri = "https://example.org/demo#"
    Concept.objects.create(
        pack=active,
        iri=iri + "odysseus",
        kind="individual",
        pref_label="Odysseus",
        alt_labels=["Ulysses"],
        hidden_labels=["Odyseus"],
    )
    Concept.objects.create(
        pack=active,
        iri=iri + "flood",
        kind="concept",
        pref_label="Flood",
        alt_labels=["Deluge"],
        extra={"pref_labels": [{"label": "Sintflut", "lang": "de"}]},
    )
    Concept.objects.create(
        pack=active,
        iri=iri + "x",
        kind="concept",
        pref_label="Draft",
        alt_labels=["Sketch"],
        taggable=False,
    )
    Concept.objects.create(
        pack=active, iri=iri + "c", kind="class", pref_label="Person", alt_labels=["Human"]
    )
    Concept.objects.create(
        pack=retired, iri=iri + "zeus", kind="individual", pref_label="Zeus", alt_labels=["Jove"]
    )
    text, count = synonyms.synonyms_text(STOP)
    body = [line for line in text.splitlines() if not line.startswith("#")]
    assert "Odyseus, Odysseus, Ulysses" in body
    assert "Flood => Flood, Sintflut|0.5, Deluge|0.5" in body
    assert not any(w in text for w in ("Sketch", "Human", "Jove"))
    assert count == len(body) == 4


@pytest.mark.django_db
def test_stop_words_of_english_and_the_labels_language_only() -> None:
    pack = _pack("p", Pack.Status.ACTIVE)
    # "War" is a German stop word ("was") but an English concept's label
    Concept.objects.create(
        pack=pack,
        iri="https://e.org/war",
        kind="concept",
        pref_label="War",
        lang="en",
        alt_labels=["Warfare"],
    )
    Concept.objects.create(
        pack=pack,
        iri="https://e.org/k",
        kind="concept",
        pref_label="Krieg",
        lang="de",
        alt_labels=["War"],  # in German, "war" is a stop word: not expanded
    )
    text, _ = synonyms.synonyms_text()
    assert "War => War, Warfare|0.5" in text
    assert "Krieg => Krieg, War|0.5" not in text


@pytest.mark.django_db
def test_a_file_too_large_for_zookeeper_leaves_terms_out(monkeypatch: pytest.MonkeyPatch) -> None:
    pack = _pack("big", Pack.Status.ACTIVE)
    for i in range(50):
        Concept.objects.create(
            pack=pack,
            iri=f"https://example.org/big#{i}",
            kind="individual",
            pref_label=f"Name{i:03d}",
            alt_labels=[f"Other{i:03d}"],
        )
    monkeypatch.setattr(synonyms, "MAX_BYTES", len(synonyms.HEADER) + 200)
    text, count = synonyms.synonyms_text(STOP)
    assert 0 < count < 50 and len(text.encode()) <= synonyms.MAX_BYTES


class FakeSolr:
    def __init__(self) -> None:
        self.uploaded: dict[str, str] = {}
        self.reloaded: list[str] = []

    def upload_configset(self, name: str, conf_dir: Path) -> None:
        self.uploaded = {
            p.relative_to(conf_dir).as_posix(): p.read_text(encoding="utf-8")
            for p in conf_dir.rglob("*")
            if p.is_file()
        }
        self.uploaded["@name"] = name

    def list_collections(self) -> list[str]:
        return ["documents", "entities", "chunks"]

    def reload_collection(self, name: str) -> None:
        self.reloaded.append(name)


@pytest.mark.django_db
def test_sync_uploads_the_whole_configset_with_the_file_and_reloads() -> None:
    pack = _pack("demo", Pack.Status.ACTIVE)
    Concept.objects.create(
        pack=pack,
        iri="https://e.org/o",
        kind="individual",
        pref_label="Odysseus",
        alt_labels=["Ulysses"],
    )
    solr = FakeSolr()
    assert synonyms.sync_synonyms(solr) == 1
    assert solr.uploaded["@name"] == "semascope-documents"
    assert "Odysseus, Ulysses" in solr.uploaded["synonyms_packs.txt"]
    assert "managed-schema.xml" in solr.uploaded and "stopwords_de.txt" in solr.uploaded
    assert solr.reloaded == ["documents"]


@pytest.mark.django_db
def test_sync_never_raises() -> None:
    class Broken(FakeSolr):
        def upload_configset(self, name: str, conf_dir: Path) -> None:
            raise RuntimeError("solr is away")

    assert synonyms.sync_synonyms(Broken()) == 0


@pytest.mark.django_db
def test_activating_a_pack_rewrites_the_synonyms(monkeypatch: pytest.MonkeyPatch) -> None:
    """Activation runs as a pack job; its hook rewrites the query-time synonyms."""

    from oss_server.knowledge import jobs, synonyms

    calls: list[str] = []

    def sync(*args: Any) -> int:
        calls.append("sync")
        return 0

    monkeypatch.setattr(synonyms, "sync_synonyms", sync)
    jobs.on_pack_activated(_pack("demo", Pack.Status.ACTIVE))
    assert calls == ["sync"]
