"""Extractors the patterns stage runs after its built-in patterns (registered in
`oss_pipeline.stages.patterns`): IBANs, legal references, amounts with their currency, and
a collection's own regex lists.

| name | writes | settings (`settings.pipeline`) |
|---|---|---|
| `iban` | `x_iban_ss` (valid IBANs, no spaces) | — |
| `law` | `x_law_ss` and `LegalReference` entity tags | `law_patterns`, `law_defaults` |
| `currency` | `x_currency_ss` (ISO 4217) and `x_amount_ss` (`AUD 1250.00`) | `dollar_currency` |
| regex lists | `x_<name>_ss` per list | `regex_lists` |

The first three run unless the collection's `patterns` list leaves them out; regex lists
run when a collection has them. At most `patterns_max_chars` (2,000,000) characters of a
document's text are read. A field with nothing found is removed, so a replay clears values
that are no longer there.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any
from urllib.parse import quote

from oss_core.contracts import Span, StageContext, Tag, WorkingDocument
from oss_pipeline.extractors import amounts, iban, law, regexlists
from oss_pipeline.provenance import assign_pages

log = logging.getLogger(__name__)

BASE = "https://semascope.dev/ontology/base#"
LIST_FIELDS = "regex_list_names"  # doc.fields: the lists last written, cleared when dropped
EXTRACTORS = ("iban", "law", "currency")
MAX_CHARS = 2_000_000
SETTINGS = ("regex_lists", "law_patterns", "law_defaults", "dollar_currency", "patterns_max_chars")


@lru_cache(maxsize=64)
def _compiled(raw: str) -> dict[str, list[Any]]:
    return regexlists.compile_lists(json.loads(raw))


@lru_cache(maxsize=64)
def _law_patterns(raw: str) -> list[Any]:
    patterns = json.loads(raw)
    if not patterns:
        return []
    return regexlists.compile_lists({"law": patterns})["law"]


def problems(pipeline: dict[str, Any]) -> list[str]:
    """The keys of a collection's pipeline settings these extractors refuse."""

    refused: list[str] = []
    if regexlists.problems(pipeline.get("regex_lists")):
        refused.append("regex_lists")
    law_patterns = pipeline.get("law_patterns")
    if law_patterns is not None and (
        not isinstance(law_patterns, list) or regexlists.problems({"law": law_patterns})
    ):
        refused.append("law_patterns")
    if "law_defaults" in pipeline and not isinstance(pipeline["law_defaults"], bool):
        refused.append("law_defaults")
    dollar = pipeline.get("dollar_currency")
    if dollar is not None and str(dollar).upper() not in amounts.CODES:
        refused.append("dollar_currency")
    return refused


def _set(doc: WorkingDocument, key: str, values: list[str]) -> None:
    if values:
        doc.fields[key] = sorted(set(values))
    else:
        doc.fields.pop(key, None)


def run_extractors(doc: WorkingDocument, ctx: StageContext, stage: Any) -> WorkingDocument:
    """Run the extractors on the document's text; returns the document."""

    settings = ctx.settings
    text = (doc.text or "")[: int(settings.get("patterns_max_chars", MAX_CHARS))]
    chosen = settings.get("patterns")
    enabled = set(EXTRACTORS) if not chosen else set(chosen) & set(EXTRACTORS)
    if "iban" in enabled:
        _set(doc, "x_iban_ss", [value for value, _s, _e in iban.find(text)])
    if "currency" in enabled:
        found = amounts.find(text, str(settings.get("dollar_currency") or "USD"))
        _set(doc, "x_currency_ss", [code for code, _a, _s, _e in found])
        _set(doc, "x_amount_ss", [amount for _c, amount, _s, _e in found])
    if "law" in enabled:
        extra = _law_patterns(json.dumps(settings.get("law_patterns") or []))
        refs = law.find(text, extra, defaults=settings.get("law_defaults", True) is not False)
        tags: dict[str, Tag] = {}
        for value, start, end in refs:
            key = value.lower()
            tag = tags.get(key)
            if tag is None:
                tag = Tag(
                    iri=f"{BASE}LegalReference/{quote(key, safe='')}",
                    label=value,
                    kind="entity",
                    stage=stage.name,
                    stage_version=stage.version,
                    confidence=0.8,
                )
                tags[key] = tag
                doc.tags.append(tag)
            tag.spans.append(Span(start=start, end=end))
        assign_pages(tags.values(), doc)
        _set(doc, "x_law_ss", [t.label for t in tags.values()])
    lists = settings.get("regex_lists")
    compiled: dict[str, list[Any]] = {}
    if lists:
        try:
            compiled = _compiled(json.dumps(lists, sort_keys=True))
        except regexlists.RegexListError as exc:  # set before the API checked them
            log.warning("regex lists of %s ignored: %s", ctx.collection, exc)
    for name in doc.fields.get(LIST_FIELDS) or []:
        if name not in compiled:  # a list the collection no longer has
            doc.fields.pop(f"x_{name}_ss", None)
    for name, matches in regexlists.find(compiled, text).items():
        _set(doc, f"x_{name}_ss", [value for value, _s, _e in matches])
    if compiled:
        doc.fields[LIST_FIELDS] = sorted(compiled)
    else:
        doc.fields.pop(LIST_FIELDS, None)
    return doc
