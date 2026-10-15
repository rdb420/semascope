"""ner: statistical named-entity recognition with spaCy (optional `nlp` extra).

Models are loaded once per worker process and chosen by document language;
`ner_models` maps a language code to a spaCy pipeline name, with `xx` as the
fallback. Entities become Tags of kind `entity` under the base pack's
classes and fill the persons/organisations/places facets.
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from typing import Any

from oss_core.contracts import Span, StageContext, Tag, WorkingDocument
from oss_pipeline.stages.base import SkipStageError

BASE = "https://semascope.dev/ontology/base#"
LABEL_MAP = {
    "PERSON": ("Person", "persons"),
    "PER": ("Person", "persons"),
    "ORG": ("Organisation", "organisations"),
    "GPE": ("Place", "places"),
    "LOC": ("Place", "places"),
    "FAC": ("Place", "places"),
    "EVENT": ("Event", "events"),
}
DEFAULT_MODELS = {"en": "en_core_web_sm", "xx": "xx_ent_wiki_sm"}
MAX_CHARS = 1_000_000


@lru_cache(maxsize=4)
def _load(model: str) -> Any:
    import spacy

    return spacy.load(model, disable=["lemmatizer", "textcat"])


class NerStage:
    name = "ner"
    version = "1"
    queue = "nlp"
    requires = ("text",)
    provides = ("tags",)

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        try:
            import spacy  # noqa: F401
        except ImportError as exc:
            raise SkipStageError("spaCy is not installed (nlp extra)") from exc
        text = (doc.text or "")[:MAX_CHARS]
        if not text.strip():
            return doc
        models = {**DEFAULT_MODELS, **(ctx.settings.get("ner_models") or {})}
        model = models.get(doc.language or "", models.get("xx"))
        if not model:
            raise SkipStageError(f"no NER model for language {doc.language!r}")
        try:
            nlp = _load(model)
        except OSError as exc:
            raise SkipStageError(f"model {model} not installed") from exc
        nlp.max_length = MAX_CHARS + 1
        min_len = int(ctx.settings.get("ner_min_length", 3))
        found: dict[str, Tag] = {}
        facets: dict[str, set[str]] = defaultdict(set)
        for ent in nlp(text).ents:
            mapped = LABEL_MAP.get(ent.label_)
            if mapped is None:
                continue
            cls, facet = mapped
            value = " ".join(ent.text.split()).strip(" .,;:'\"")
            if len(value) < min_len or value.isdigit():
                continue
            key = f"{cls}/{value.lower()}"
            tag = found.get(key)
            if tag is None:
                tag = Tag(
                    iri=f"{BASE}{cls}/{value.lower()}",
                    label=value,
                    kind="entity",
                    stage=self.name,
                    confidence=0.7,
                )
                found[key] = tag
                facets[facet].add(value)
            tag.spans.append(Span(start=ent.start_char, end=ent.end_char))
        doc.tags.extend(found.values())
        for facet, values in facets.items():
            doc.fields[facet] = sorted(values)
        doc.fields["ner_model"] = model
        return doc
