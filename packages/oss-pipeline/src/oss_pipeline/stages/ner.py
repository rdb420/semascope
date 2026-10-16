"""ner: named-entity recognition behind three providers.

`spacy` (bundled, CPU) uses the statistical pipelines chosen by document
language. `gliner` runs GLiNER2 in-process on a GPU (the gpu image).
`remote` posts to an extraction service (`POST /entities`). The text is cut
into paragraph-bounded windows first, so every provider sees bounded inputs
and spans are mapped back to document offsets. Entities become Tags of kind
`entity` under the base classes (Person, Organisation, Place, Event) or the
active packs' classes, and fill the persons/organisations/places facets.
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from typing import Any, NamedTuple, Protocol

import httpx

from oss_core.contracts import Span, StageContext, Tag, WorkingDocument
from oss_pipeline.provenance import assign_pages
from oss_pipeline.stages.base import SkipStageError

BASE = "https://semascope.dev/ontology/base#"
LABEL_MAP = {
    "PERSON": "person",
    "PER": "person",
    "ORG": "organisation",
    "GPE": "place",
    "LOC": "place",
    "FAC": "place",
    "EVENT": "event",
}
# label -> (class, facet field, description handed to zero-shot models)
BASE_LABELS: dict[str, tuple[str, str, str]] = {
    "person": ("Person", "persons", "a named human being"),
    "organisation": ("Organisation", "organisations", "a company, institution, agency or group"),
    "place": ("Place", "places", "a city, country, region, building or landmark"),
    "event": ("Event", "events", "a named historical, cultural or scheduled event"),
}
DEFAULT_MODELS = {"en": "en_core_web_sm", "xx": "xx_ent_wiki_sm"}
MAX_CHARS = (
    300_000  # per document by default; `ner_max_chars` in the collection's pipeline settings
)
WINDOW_CHARS = {"spacy": 20_000, "gliner": 1_800, "remote": 1_800}


class Entity(NamedTuple):
    label: str
    text: str
    start: int
    end: int
    confidence: float


class NerProvider(Protocol):
    name: str

    def extract(
        self, windows: list[str], language: str | None, labels: dict[str, str]
    ) -> list[list[Entity]]: ...


def windows(text: str, size: int) -> list[tuple[int, str]]:
    """(offset, piece) windows of about `size` characters, cut on paragraph boundaries."""

    out: list[tuple[int, str]] = []
    start = 0
    pos = 0
    length = len(text)
    while pos < length:
        end = min(pos + size, length)
        if end < length:
            cut = text.rfind("\n\n", pos + size // 2, end)
            if cut == -1:
                cut = text.rfind("\n", pos + size // 2, end)
            if cut == -1:
                cut = text.rfind(" ", pos + size // 2, end)
            if cut != -1:
                end = cut + 1
        out.append((start, text[pos:end]))
        pos = end
        start = pos
    return out


@lru_cache(maxsize=4)
def _spacy(model: str) -> Any:
    import spacy

    # tok2vec and ner only: the tagger and parser are not used here
    unused = ["lemmatizer", "textcat", "parser", "tagger", "attribute_ruler"]
    return spacy.load(model, disable=unused)


class SpacyProvider:
    name = "spacy"

    def __init__(self, models: dict[str, str], max_length: int) -> None:
        self._models = models
        self._max_length = max_length

    def extract(
        self, windows: list[str], language: str | None, labels: dict[str, str]
    ) -> list[list[Entity]]:
        try:
            import spacy  # noqa: F401
        except ImportError as exc:
            raise SkipStageError("spaCy is not installed (nlp extra)") from exc
        model = self._models.get(language or "", self._models.get("xx"))
        if not model:
            raise SkipStageError(f"no NER model for language {language!r}")
        try:
            nlp = _spacy(model)
        except OSError as exc:
            raise SkipStageError(f"model {model} not installed") from exc
        nlp.max_length = self._max_length + 1
        self.model = model
        out: list[list[Entity]] = []
        for doc in nlp.pipe(windows, batch_size=8):
            found = []
            for ent in doc.ents:
                label = LABEL_MAP.get(ent.label_)
                if label:
                    found.append(Entity(label, ent.text, ent.start_char, ent.end_char, 0.7))
            out.append(found)
        return out


def parse_gliner_results(results: list[Any], windows: list[str]) -> list[list[Entity]]:
    """GLiNER2's per-text output, flattened or nested, to Entities with window offsets."""

    out: list[list[Entity]] = []
    for text, result in zip(windows, results, strict=True):
        found: list[Entity] = []
        items: list[dict[str, Any]] = []
        if isinstance(result, dict):  # {"entities": {label: [{text, start, end, confidence}]}}
            for label, spans in (result.get("entities") or {}).items():
                for span in spans or []:
                    if isinstance(span, dict):
                        items.append({**span, "label": label})
        else:  # the sidecar's flat list [{label, text, start, end, confidence}]
            items = [s for s in result if isinstance(s, dict)]
        for item in items:
            start, end = item.get("start"), item.get("end")
            if start is None or end is None:
                continue
            value = str(item.get("text") or text[int(start) : int(end)])
            found.append(
                Entity(
                    str(item["label"]).lower(),
                    value,
                    int(start),
                    int(end),
                    float(item.get("confidence", 1.0)),
                )
            )
        out.append(found)
    return out


@lru_cache(maxsize=2)
def _gliner(model: str, device: str) -> tuple[Any, str]:
    """GLiNER2 loaded once per worker process, on the GPU when there is one."""

    try:
        from gliner2 import GLiNER2
    except ImportError as exc:
        raise SkipStageError("gliner2 is not installed (gpu image)") from exc
    loaded = GLiNER2.from_pretrained(model)
    if device == "auto":
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
    return (loaded.to(device) if hasattr(loaded, "to") else loaded), device


class GlinerProvider:
    """GLiNER2 in-process; the model is loaded once per worker process."""

    name = "gliner"

    def __init__(self, model: str, threshold: float = 0.5, device: str = "auto") -> None:
        self.model = model
        self._threshold = threshold
        self._device = device

    def _load(self) -> Any:
        loaded, self.device = _gliner(self.model, self._device)
        return loaded

    def extract(
        self, windows: list[str], language: str | None, labels: dict[str, str]
    ) -> list[list[Entity]]:
        model = self._load()
        results = model.batch_extract_entities(
            windows,
            labels,
            include_confidence=True,
            include_spans=True,
            batch_size=8,
            threshold=self._threshold,
        )
        return parse_gliner_results(list(results), windows)


class RemoteProvider:
    """`POST {base}/entities {"texts", "labels", "threshold"}` -> `{"results": [[span...]...]}`."""

    name = "remote"
    BATCH = 32

    def __init__(
        self,
        base_url: str | None,
        api_key: str = "",
        threshold: float = 0.5,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise SkipStageError("SEMASCOPE_NER_BASE_URL is not set")
        self.model = base_url
        self._threshold = threshold
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=600.0, transport=transport
        )

    def extract(
        self, windows: list[str], language: str | None, labels: dict[str, str]
    ) -> list[list[Entity]]:
        out: list[list[Entity]] = []
        for i in range(0, len(windows), self.BATCH):
            batch = windows[i : i + self.BATCH]
            resp = self._client.post(
                "/entities",
                json={"texts": batch, "labels": labels, "threshold": self._threshold},
            )
            resp.raise_for_status()
            out.extend(parse_gliner_results(list(resp.json()["results"]), batch))
        return out


def looks_like_name(value: str, min_len: int = 3) -> bool:
    """Reject what small models mistake for names: lowercase words, digits, long spans, junk."""

    if len(value) < min_len or len(value) > 80 or value.isdigit():
        return False
    words = value.split()
    if len(words) > 6:
        return False
    alpha = [c for c in value if c.isalpha()]
    if len(alpha) < 2 or not any(c.isupper() for c in alpha):
        return False
    if sum(c.isalpha() or c in " '-." for c in value) < len(value) * 0.8:
        return False
    return not all(w.isupper() for w in words) or len(words) > 1  # "WAINAMOINEN" alone is a heading


def facet_for(label: str, cls: str) -> str:
    if label in BASE_LABELS:
        return BASE_LABELS[label][1]
    slug = "".join(c if c.isalnum() else "_" for c in cls.lower()).strip("_")
    return f"x_{slug}_entities_ss"


class NerStage:
    name = "ner"
    version = "3"
    requires = ("text",)
    provides = ("tags",)

    def __init__(self, provider: NerProvider | None = None) -> None:
        self._provider = provider
        self._provider_key: tuple[Any, ...] | None = None

    def _get_provider(self, ctx: StageContext, limit: int) -> NerProvider:
        s = ctx.settings
        kind = str(s.get("ner_provider", "spacy"))
        key = (kind, s.get("ner_model"), s.get("ner_base_url"), s.get("ner_threshold"))
        if self._provider is not None and self._provider_key in (None, key):
            return self._provider
        threshold = float(s.get("ner_threshold", 0.5))
        if kind == "spacy":
            models = {**DEFAULT_MODELS, **(s.get("ner_models") or {})}
            self._provider = SpacyProvider(models, limit)
        elif kind == "gliner":
            model = str(s.get("ner_model") or "fastino/gliner2-large-v1")
            self._provider = GlinerProvider(model, threshold, str(s.get("ner_device") or "auto"))
        elif kind == "remote":
            token = str(s.get("ner_api_key") or "")
            self._provider = RemoteProvider(s.get("ner_base_url"), token, threshold)
        else:
            raise SkipStageError(f"unknown NER provider {kind!r}")
        self._provider_key = key
        return self._provider

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        limit = int(ctx.settings.get("ner_max_chars", MAX_CHARS))
        text = (doc.text or "")[:limit]
        # facets of an earlier run (a replay, or another provider) must not survive this one
        for key in list(doc.fields):
            if key.endswith("_entities_ss") or key in {f for _, f, _ in BASE_LABELS.values()}:
                del doc.fields[key]
        if not text.strip():
            return doc
        provider = self._get_provider(ctx, limit)
        size = int(ctx.settings.get("ner_window_chars", WINDOW_CHARS.get(provider.name, 20_000)))
        pieces = windows(text, size)
        # zero-shot labels: the base four plus the active packs' classes
        classes: dict[str, str] = {label: cls for label, (cls, _, _) in BASE_LABELS.items()}
        labels: dict[str, str] = {label: desc for label, (_, _, desc) in BASE_LABELS.items()}
        for label, spec in (ctx.settings.get("ner_labels") or {}).items():
            classes[str(label).lower()] = str(spec.get("class") or label)
            labels[str(label).lower()] = str(spec.get("description") or label)
        results = provider.extract([piece for _, piece in pieces], doc.language, labels)
        min_len = int(ctx.settings.get("ner_min_length", 3))
        model = f"{provider.name}:{getattr(provider, 'model', '')}"
        found: dict[str, Tag] = {}
        facets: dict[str, set[str]] = defaultdict(set)
        for (offset, _), entities in zip(pieces, results, strict=True):
            for ent in entities:
                cls = classes.get(ent.label)
                if cls is None:
                    continue
                value = " ".join(ent.text.split()).strip(" .,;:'\"")
                if not looks_like_name(value, min_len):
                    continue
                key = f"{cls}/{value.lower()}"
                tag = found.get(key)
                if tag is None:
                    tag = Tag(
                        iri=f"{BASE}{cls}/{value.lower()}",
                        label=value,
                        kind="entity",
                        stage=self.name,
                        stage_version=self.version,
                        confidence=round(ent.confidence, 3),
                        model=model,
                    )
                    found[key] = tag
                    facets[facet_for(ent.label, cls)].add(value)
                elif ent.confidence > tag.confidence:
                    tag.confidence = round(ent.confidence, 3)
                tag.spans.append(Span(start=offset + ent.start, end=offset + ent.end))
        assign_pages(found.values(), doc)
        doc.tags.extend(found.values())
        for facet, values in facets.items():
            doc.fields[facet] = sorted(values)
        doc.fields["ner_model"] = model
        return doc
