"""Search syntax, languages, synonyms and facet features (docs/search.md) on a live stack.

Short texts in several languages are uploaded to their own collection; each is
detected, routed to its language's stemmed fields and found by another form of a
word it contains. The demo subset and the demo pack cover synonyms, OR and the
result-list features.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from _stack import collection, documents_settled, ensure_collection, wait_for

SLUG = "it-languages"
TEXTS = {
    "de": (
        "kinder.txt",
        "Die Kinder spielten den ganzen Nachmittag in den alten Häusern am Fluss. Ihre Eltern "
        "arbeiteten auf den Feldern und kamen erst am Abend zurück. Die Großmutter erzählte "
        "ihnen Geschichten von Riesen und Zwergen, und alle hörten still zu.",
    ),
    "it": (
        "canzoni.txt",
        "I bambini cantavano le canzoni antiche della loro terra. Le madri preparavano il pane "
        "nelle cucine e i padri tornavano dai campi al tramonto. Ogni sera la nonna raccontava "
        "storie di cavalieri e di draghi, e nessuno voleva andare a dormire.",
    ),
    "fr": (
        "chansons.txt",
        "Les enfants chantaient les chansons anciennes de leur pays. Les mères préparaient le "
        "pain dans les cuisines et les pères rentraient des champs au coucher du soleil. Chaque "
        "soir, la grand-mère racontait des histoires de chevaliers et de dragons.",
    ),
    "ru": (
        "knigi.txt",
        "Дети читали старые книги в большой библиотеке. Учитель рассказывал им о далёких "
        "странах и великих путешествиях. Вечером они возвращались домой по тихим улицам "
        "старого города и говорили о том, что узнали.",
    ),
    "pl": (
        "dzieci.txt",
        "Dzieci bawiły się w starych domach nad rzeką przez całe popołudnie. Rodzice pracowali "
        "w polu i wracali dopiero wieczorem. Babcia opowiadała im historie o smokach i "
        "rycerzach, a wszyscy słuchali w ciszy.",
    ),
    "en": (
        "runners.txt",
        "The runners were running along the riverbank long before sunrise. Their coaches "
        "shouted times from the bridges while the gulls circled overhead. Wotan rode out at "
        "dawn across the frozen hills, and the ravens followed him.",
    ),
    "ja": (
        "daigaku.txt",
        "東京大学の学生たちは毎朝図書館で古い本を読んでいます。先生は遠い国の物語を話して"
        "くれました。夕方には静かな町を歩いて家に帰ります。週末には友達と山に登ります。",
    ),
}


def search(api: httpx.Client, q: str = "*:*", **extra: Any) -> dict[str, Any]:
    body = {"q": q, "collection": SLUG, "page_size": 50, "facets": [], **extra}
    resp = api.post("/search", json=body)
    assert resp.status_code == 200, resp.text
    out: dict[str, Any] = resp.json()
    return out


def names(out: dict[str, Any]) -> set[str]:
    return {h["uri"].rsplit("/", 1)[-1] for h in out["hits"]}


@pytest.fixture(scope="module")
def languages(api: httpx.Client) -> dict[str, dict[str, Any]]:
    """The uploaded texts, indexed and searchable, by language code."""

    ensure_collection(api, SLUG)
    files = [("files", (name, text.encode(), "text/plain")) for name, text in TEXTS.values()]
    resp = api.post(f"/collections/{SLUG}/upload", files=files)
    assert resp.status_code == 202, resp.text
    uris = [d["uri"] for d in resp.json()]
    docs = wait_for(
        lambda: documents_settled(api, SLUG, uris), timeout=600, what="the language texts"
    )
    assert all(d["status"] == "indexed" for d in docs.values()), docs
    wait_for(
        lambda: len(search(api)["hits"]) >= len(TEXTS),
        timeout=60,
        every=1.0,
        what="the language texts to be searchable",
    )
    by_name = {h["uri"].rsplit("/", 1)[-1]: h for h in search(api)["hits"]}
    return {code: by_name[name] for code, (name, _) in TEXTS.items()}


@pytest.mark.parametrize("code", sorted(TEXTS))
def test_each_text_is_detected_as_its_language(
    languages: dict[str, dict[str, Any]], code: str
) -> None:
    assert languages[code]["language"] == code, languages[code]


@pytest.mark.parametrize(
    ("code", "word", "exact_hits"),
    [
        ("de", "Kind", False),  # Kinder
        ("it", "canzone", False),  # canzoni: Italian, not Spanish, stemming
        ("fr", "chanson", False),  # chansons
        ("ru", "книга", False),  # книги
        ("pl", "dziecko", False),  # Dzieci: the Stempel stemmer (Solr's analysis-extras)
        ("en", "run", False),  # running, runners
    ],
)
def test_another_form_of_a_word_finds_the_text_in_its_language(
    api: httpx.Client,
    languages: dict[str, dict[str, Any]],
    code: str,
    word: str,
    exact_hits: bool,
) -> None:
    name = TEXTS[code][0]
    assert name in names(search(api, word)), f"{word!r} should find {name} by its stem"
    # without other word forms, only the written form counts
    assert (name in names(search(api, word, stemming=False))) is exact_hits
    assert (name in names(search(api, f"exact:{word}"))) is exact_hits
    # the stem lives in that language's field only
    other = "de" if code != "de" else "it"
    assert name not in names(search(api, f"text_txt_{other}:{word}"))


def test_cjk_text_is_indexed_as_bigrams(
    api: httpx.Client, languages: dict[str, dict[str, Any]]
) -> None:
    name = TEXTS["ja"][0]
    assert name in names(search(api, "大学"))
    assert name in names(search(api, 'text_txt_ja:"東京大学"'))
    # the bigrams keep the characters' order: the reversed pair is not a word of the text
    assert name not in names(search(api, 'text_txt_ja:"学大"'))


def test_pack_labels_are_query_synonyms(
    api: httpx.Client, languages: dict[str, dict[str, Any]]
) -> None:
    # the demo pack names Odin also Woden and Wotan; the text says only "Wotan"
    name = TEXTS["en"][0]
    assert name in names(search(api, "Odin"))
    assert name in names(search(api, "Woden"))


def test_explicit_or_finds_either_word(
    api: httpx.Client, languages: dict[str, dict[str, Any]]
) -> None:
    de, it = TEXTS["de"][0], TEXTS["it"][0]
    assert names(search(api, "Kinder bambini")) == set()  # no text has both
    assert {de, it} <= names(search(api, "Kinder OR bambini"))
    # OR joins its neighbours: Eltern AND (Kinder OR bambini)
    assert names(search(api, "Eltern Kinder OR bambini")) == {de}
    assert {de, it} <= names(search(api, "Kinder bambini", operator="any"))
    assert names(search(api, "Kinder Eltern", operator="all")) == {de}


def test_negation_phrases_wildcards_and_fuzzy(
    api: httpx.Client, languages: dict[str, dict[str, Any]]
) -> None:
    de, en = TEXTS["de"][0], TEXTS["en"][0]
    assert de in names(search(api, "Großmutter"))
    assert de not in names(search(api, "Großmutter -Zwergen"))
    assert de not in names(search(api, "Großmutter NOT Zwergen"))
    assert en in names(search(api, '"frozen hills"'))
    assert en not in names(search(api, '"hills frozen"'))
    assert en in names(search(api, "sunri*"))
    assert en in names(search(api, "sunrize~1"))
    assert de in names(search(api, "Grossmutter"))  # ß folds to ss


def test_excluded_values_leave_documents_out(
    api: httpx.Client, languages: dict[str, dict[str, Any]]
) -> None:
    every = names(search(api))
    out = search(api, exclude={"language_s": ["de", "it"]}, facets=["language_s"])
    assert names(out) == every - {TEXTS["de"][0], TEXTS["it"][0]}
    # the facet still counts the excluded values, so they can be brought back
    counted = {v["value"] for v in out["facets"]["language_s"]}
    assert {"de", "it"} <= counted


def test_date_range_and_drill_down(
    api: httpx.Client, languages: dict[str, dict[str, Any]]
) -> None:
    # uploads carry no modification date; the indexing date serves the same way
    every = names(search(api))
    wide = {"field": "indexed_dt", "start": "2000-01-01", "end": "2200-01-01"}
    assert names(search(api, dates=wide)) == every
    none = {"field": "indexed_dt", "start": "1900-01-01", "end": "1900-01-02"}
    assert names(search(api, dates=none)) == set()
    years = search(api, histogram="indexed_dt", histogram_gap="+1YEAR")["histogram"]
    year = max(years, key=lambda b: b["count"])["value"][:4]
    span = {"field": "indexed_dt", "start": f"{year}-01-01", "end": f"{int(year) + 1}-01-01"}
    months = search(api, dates=span, histogram="indexed_dt", histogram_gap="+1MONTH")
    assert months["histogram"] and all(b["value"].startswith(year) for b in months["histogram"])
    assert sum(b["count"] for b in months["histogram"]) == months["total"]
    bad = api.post("/search", json={"q": "*:*", "dates": {"start": "yesterday"}})
    assert bad.status_code == 422, bad.text


def test_a_search_without_words_lists_the_newest_first(
    api: httpx.Client, indexed: dict[str, Any]
) -> None:
    def dates(**extra: Any) -> list[str]:
        body = {"q": "*:*", "collection": collection(), "page_size": 50, "facets": [], **extra}
        hits = api.post("/search", json=body).raise_for_status().json()["hits"]
        return [h["modified_at"] or "" for h in hits]

    newest = dates()
    assert any(newest) and newest == sorted(newest, reverse=True)
    oldest = dates(sort="modified_dt asc")
    assert oldest == sorted(oldest)


@pytest.mark.parametrize(
    "body",
    [
        {"page_size": 101},
        {"page": 0},
        {"facet_limit": 0},
        {"facet_limit": -1},
        {"page": 200, "page_size": 100},  # starts beyond 10,000
    ],
)
def test_paging_and_facet_caps(api: httpx.Client, body: dict[str, Any]) -> None:
    resp = api.post("/search", json={"q": "*:*", **body})
    assert resp.status_code == 422, resp.text


def test_hits_carry_entities_and_facets_show_more(
    api: httpx.Client, indexed: dict[str, Any]
) -> None:
    body = {"q": "Odysseus", "collection": collection(), "facets": ["persons_ss", "places_ss"]}
    out = api.post("/search", json={**body, "facet_limit": 3}).raise_for_status().json()
    assert any(h["entities"].get("persons_ss") for h in out["hits"]), out["hits"][0]
    assert len(out["facets"]["persons_ss"]) == 3 and len(out["facets"]["places_ss"]) <= 3
    more = {**body, "facet_limit": 3, "facet_more": ["persons_ss"]}
    out = api.post("/search", json=more).raise_for_status().json()
    assert len(out["facets"]["persons_ss"]) > 3 and len(out["facets"]["places_ss"]) <= 3


def test_or_across_the_demo_texts(api: httpx.Client, indexed: dict[str, Any]) -> None:
    def slugs(q: str) -> set[str]:
        body = {"q": q, "collection": collection(), "page_size": 50, "facets": []}
        hits = api.post("/search", json=body).raise_for_status().json()["hits"]
        return {h["uri"].rsplit("/", 1)[-1].removesuffix(".txt") for h in hits}

    either = slugs("Odysseus OR Arjuna")
    assert {"odyssey", "song-celestial"} <= either, either
    assert slugs("Odysseus") | slugs("Arjuna") == either
