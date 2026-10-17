"""patterns: cheap, model-free extractors for the base pack's entity types."""

from __future__ import annotations

import re

from oss_core.contracts import Span, StageContext, Tag, WorkingDocument
from oss_pipeline.extractors import run_extractors
from oss_pipeline.provenance import assign_pages

BASE = "https://semascope.dev/ontology/base#"

PATTERNS: dict[str, tuple[str, re.Pattern[str]]] = {
    "emails": ("EmailAddress", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    "urls": ("URL", re.compile(r"\bhttps?://[^\s<>\"')\]]+", re.I)),
    "phones": ("PhoneNumber", re.compile(r"(?<![\w/])\+?\d[\d\s().-]{7,}\d(?![\w/])")),
    "money": (
        "MonetaryAmount",
        re.compile(
            r"(?:(?:AUD|USD|EUR|GBP|NZD|CAD|CHF|JPY|INR|CNY)\s?|[$€£¥])\s?\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?"
            r"(?:\s?(?:million|billion|thousand|m|bn|k))?\b",
            re.I,
        ),
    ),
    "hashtags": ("Hashtag", re.compile(r"(?<![\w&])#[A-Za-z][\w]{1,63}\b")),
}


class PatternsStage:
    name = "patterns"
    version = "2"
    requires = ("text",)
    provides = ("tags",)

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        text = doc.text or ""
        enabled = set(ctx.settings.get("patterns") or PATTERNS.keys())
        for field_name, (cls, pattern) in PATTERNS.items():
            if field_name not in enabled:
                continue
            seen: dict[str, Tag] = {}
            for match in pattern.finditer(text):
                value = match.group(0).strip().rstrip(".,;:!?")
                if field_name == "phones" and sum(c.isdigit() for c in value) < 8:
                    continue
                key = value.lower()
                tag = seen.get(key)
                if tag is None:
                    tag = Tag(
                        iri=f"{BASE}{cls}/{key}",
                        label=value,
                        kind="entity",
                        stage=self.name,
                        stage_version=self.version,
                        confidence=0.9,
                    )
                    seen[key] = tag
                    doc.tags.append(tag)
                tag.spans.append(Span(start=match.start(), end=match.end()))
            assign_pages(seen.values(), doc)
            doc.fields[field_name] = sorted({t.label for t in seen.values()})
        return run_extractors(doc, ctx, self)  # IBAN, law, currency, regex lists (WS-7)
