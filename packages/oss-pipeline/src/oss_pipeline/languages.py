"""Languages the documents index analyses: one stemmed copy of title and text per language.

Every document is indexed language-neutrally (`title_txt`, `text_txt`: lowercased,
accents folded, nothing removed or stemmed). A document whose language is one of
`LANGUAGES` also gets `title_txt_<code>` and `text_txt_<code>`, analysed with that
language's stop words and stemmer (`deploy/solr/configsets/documents/conf/managed-schema.xml`
declares one field type per code). Keyword search queries the neutral fields and every
language's fields, so "Kinder" finds "Kind" in German texts and "running" finds "run" in
English ones, while a word never matches another language's stems.

Codes are ISO 639-1. Chinese, Japanese and Korean share one analysis (CJK bigrams),
each under its own code. Norwegian Bokmål and Nynorsk (`nb`, `nn`) use `no`.
"""

from __future__ import annotations

# code -> English name. The schema has a `text_<code>` type for each (see the module doc).
LANGUAGES: dict[str, str] = {
    "ar": "Arabic",
    "bg": "Bulgarian",
    "ca": "Catalan",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "hi": "Hindi",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "lv": "Latvian",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sv": "Swedish",
    "tr": "Turkish",
    "zh": "Chinese",
}

# other codes detectors return for a supported language
ALIASES: dict[str, str] = {"nb": "no", "nn": "no", "cz": "cs", "in": "id"}

# the analysed fields of a language, as the schema names them
TITLE_PREFIX = "title_txt_"
TEXT_PREFIX = "text_txt_"


def normalise(code: object) -> str | None:
    """The supported language code for what a detector or a connector said, or None.

    Accepts `de`, `DE`, `de-AT`, `de_AT`, `zh-cn`, `nb`; anything else is None.
    """

    if not isinstance(code, str):
        return None
    base = code.strip().lower().replace("_", "-").split("-")[0]
    base = ALIASES.get(base, base)
    return base if base in LANGUAGES else None


def language_fields(language: object, title: str, text: str) -> dict[str, str]:
    """The per-language index fields for a document: empty when its language is unsupported."""

    code = normalise(language)
    if code is None:
        return {}
    out = {f"{TEXT_PREFIX}{code}": text}
    if title:
        out[f"{TITLE_PREFIX}{code}"] = title
    return out


def all_language_fields() -> list[str]:
    """Every per-language field name (for the index's reserved names and the query fields)."""

    return [p + code for code in sorted(LANGUAGES) for p in (TITLE_PREFIX, TEXT_PREFIX)]
