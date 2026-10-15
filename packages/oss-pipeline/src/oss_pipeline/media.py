"""Media type to content-type group, the base pack's one SKOS scheme."""

from __future__ import annotations

GROUPS = (
    "text",
    "image",
    "audio",
    "video",
    "archive",
    "mail",
    "spreadsheet",
    "presentation",
    "web",
    "data",
)

_EXACT = {
    "text/html": "web",
    "application/xhtml+xml": "web",
    "application/rss+xml": "web",
    "application/atom+xml": "web",
    "message/rfc822": "mail",
    "application/mbox": "mail",
    "application/vnd.ms-outlook": "mail",
    "application/vnd.ms-outlook-pst": "mail",
    "text/csv": "data",
    "text/tab-separated-values": "data",
    "application/json": "data",
    "application/xml": "data",
    "text/xml": "data",
    "text/turtle": "data",
    "application/rdf+xml": "data",
    "application/ld+json": "data",
    "application/zip": "archive",
    "application/x-tar": "archive",
    "application/gzip": "archive",
    "application/x-7z-compressed": "archive",
    "application/x-rar-compressed": "archive",
    "application/vnd.ms-excel": "spreadsheet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "spreadsheet",
    "application/vnd.oasis.opendocument.spreadsheet": "spreadsheet",
    "application/vnd.ms-powerpoint": "presentation",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "presentation",
    "application/vnd.oasis.opendocument.presentation": "presentation",
}


def content_type_group(media_type: str | None) -> str:
    if not media_type:
        return "text"
    mt = media_type.split(";")[0].strip().lower()
    if mt in _EXACT:
        return _EXACT[mt]
    major = mt.split("/", 1)[0]
    if major in ("image", "audio", "video"):
        return major
    return "text"
