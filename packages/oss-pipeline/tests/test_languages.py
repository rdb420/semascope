"""Per-language analysis: documents are routed to their language's stemmed fields, and the
documents schema declares a field type, fields and word lists for every language."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from oss_core.contracts import WorkingDocument
from oss_pipeline.languages import LANGUAGES, all_language_fields, language_fields, normalise
from oss_pipeline.stages.index import RESERVED_FIELDS, custom_field_name, to_solr_document

CONF = Path(__file__).resolve().parents[3] / "deploy" / "solr" / "configsets" / "documents" / "conf"
SCHEMA = (CONF / "managed-schema.xml").read_text(encoding="utf-8")

# the languages the review asks for, by ISO 639-1 code (Czech is cs, Italian is it)
REQUIRED = "en de fr es it nl pt ru cs sv da no fi hu pl ro tr ar fa el zh ja ko".split()


@pytest.mark.parametrize(
    ("given", "code"),
    [
        ("de", "de"),
        ("DE", "de"),
        ("de-AT", "de"),
        ("pt_BR", "pt"),
        ("zh-cn", "zh"),
        ("nb", "no"),
        ("nn", "no"),
        ("cz", "cs"),
        ("it", "it"),
        ("und", None),
        ("xx", None),
        ("", None),
        (None, None),
    ],
)
def test_language_codes_normalise(given: str | None, code: str | None) -> None:
    assert normalise(given) == code


def doc(language: str | None) -> WorkingDocument:
    return WorkingDocument(
        id="d1",
        collection="c",
        source_id="1",
        uri="file:///x/kinder.txt",
        content_hash="h",
        title="Kinder",
        text="Die Kinder spielten.",
        language=language,
    )


def test_a_document_is_routed_to_its_languages_fields_only() -> None:
    out = to_solr_document(doc("de"))
    assert out["text_txt_de"] == "Die Kinder spielten." and out["title_txt_de"] == "Kinder"
    assert out["text_txt"] == "Die Kinder spielten."  # the neutral copy, always
    assert not [k for k in out if k.startswith("text_txt_") and k != "text_txt_de"]
    # no English stemming for a German text any more (the old copyField did that)
    assert "text_txt_en" not in out


@pytest.mark.parametrize("language", [None, "und", "xx"])
def test_an_unknown_language_is_indexed_neutrally(language: str | None) -> None:
    out = to_solr_document(doc(language))
    assert not [k for k in out if re.fullmatch(r"(text|title)_txt_\w+", k)]


def test_connectors_cannot_write_the_language_fields() -> None:
    for name in all_language_fields():
        assert name in RESERVED_FIELDS
        assert custom_field_name("x_" + name) is None


def test_the_review_languages_are_supported() -> None:
    assert set(REQUIRED) <= set(LANGUAGES)
    assert language_fields("it", "t", "x") == {"text_txt_it": "x", "title_txt_it": "t"}


@pytest.mark.parametrize("code", sorted(LANGUAGES))
def test_the_schema_declares_each_language(code: str) -> None:
    kind = "text_cjk" if code in ("zh", "ja", "ko") else f"text_{code}"
    for prefix in ("title_txt_", "text_txt_"):
        assert f'<field name="{prefix}{code}" type="{kind}" indexed="true"' in SCHEMA, code
    block = re.search(rf'<fieldType name="{kind}".*?</fieldType>', SCHEMA, re.S)
    assert block, kind
    for path in re.findall(r'(?:words|articles|dictionary)="([^"]+)"', block.group(0)):
        assert (CONF / path).is_file(), path


def test_no_language_shares_another_languages_stemmer() -> None:
    def stemmer(code: str) -> str:
        block = re.search(rf'<fieldType name="text_{code}".*?</fieldType>', SCHEMA, re.S)
        assert block
        stems = re.findall(r'<filter name="(\w*[Ss]tem\w*|snowballPorter)"[^>]*?>', block.group(0))
        lang = re.search(r'language="(\w+)"', block.group(0))
        return ",".join(stems) + (lang.group(1) if lang else "")

    assert "italianLightStem" in stemmer("it") and "spanishLightStem" in stemmer("es")
    assert "czechStem" in stemmer("cs")
    stems = [stemmer(c) for c in sorted(LANGUAGES) if c not in ("zh", "ja", "ko")]
    assert len(stems) == len(set(stems))


def test_configsets_have_no_subfolders() -> None:
    # Solr's collection BACKUP keeps only the top-level files of a configset, and RESTORE
    # writes that copy over the live one: a file in a subfolder (lang/stopwords_it.txt)
    # is lost, and the schema that names it no longer loads. Keep every file at the root.
    root = CONF.parents[1]
    nested = sorted(str(p.relative_to(root)) for p in root.glob("*/conf/*/**") if p.exists())
    assert nested == [], nested
    assert not re.search(r'(?:words|articles|dictionary|synonyms)="[^"]*/', SCHEMA)


def test_the_neutral_field_has_no_language_stop_words() -> None:
    block = re.search(r'<fieldType name="text_general".*?</fieldType>', SCHEMA, re.S)
    assert block and 'name="stop"' not in block.group(0)
    assert 'synonyms="synonyms_packs.txt"' in block.group(0)
