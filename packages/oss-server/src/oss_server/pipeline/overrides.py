"""The stage settings a collection may change under `settings.pipeline`.

Only tuning is per collection: sizes and caps, retries, which optional work
runs, pseudonymisation. Service addresses, credentials, Solr collection names,
paths and secrets come from the deployment's settings and nothing else, so a
collection's settings cannot point the workers, with their credentials, at
another host or another collection. The API refuses any other key (422); at
run time any other key is ignored (rows written before this check, or edited
in the admin).
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

PIPELINE_OVERRIDES = frozenset(
    {
        # structure
        "chunk_words",
        "chunk_overlap",
        # extract
        "ocr",  # now, later or off (true and false as before); checked: `refused_overrides`
        "ocr_min_chars_per_page",
        "ocr_languages",  # Tesseract codes, e.g. "eng+deu" (checked: `refused_overrides`)
        "detect_language",
        "embedded_max_items",
        "embedded_max_chars",
        # patterns
        "patterns",
        # preview
        "preview_pages",
        "preview_width",
        # ner
        "ner_max_chars",
        "ner_min_length",
        "ner_window_chars",
        # embed
        "embed",
        "embed_max_chunks",
        # graph
        "pseudonymise",
        # retries (stages in place, and the document task)
        "stage_retries",
        "stage_retry_backoff_s",
        "task_retries",
        "task_retry_backoff_s",
    }
)


def refused_overrides(settings: dict[str, Any] | None) -> list[str]:
    """The keys of a collection's `settings.pipeline` that a collection may not set."""

    from oss_pipeline.clients.tika import valid_ocr_languages
    from oss_pipeline.ocr_later import valid_ocr_setting

    pipeline = (settings or {}).get("pipeline")
    if pipeline is None:
        return []
    if not isinstance(pipeline, dict):
        return ["pipeline"]
    refused = [str(k) for k in pipeline if k not in PIPELINE_OVERRIDES]
    if pipeline.get("ocr_languages") is not None and not valid_ocr_languages(
        pipeline["ocr_languages"]
    ):
        refused.append("ocr_languages")  # Tesseract codes only: it becomes a request header
    if "ocr" in pipeline and not valid_ocr_setting(pipeline["ocr"]):
        refused.append("ocr")
    return sorted(refused)


def pipeline_overrides(settings: dict[str, Any] | None) -> dict[str, Any]:
    """A collection's `settings.pipeline`, keeping only the keys a collection may set."""

    pipeline = (settings or {}).get("pipeline") or {}
    if not isinstance(pipeline, dict):
        return {}
    ignored = sorted(str(k) for k in pipeline if k not in PIPELINE_OVERRIDES)
    if ignored:
        log.warning("collection pipeline settings ignored: %s", ", ".join(ignored))
    return {k: v for k, v in pipeline.items() if k in PIPELINE_OVERRIDES}
