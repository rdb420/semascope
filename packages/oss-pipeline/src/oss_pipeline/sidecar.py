"""Sidecar metadata files: what a file next to a document says about it, as custom fields.

For a file `report.pdf` the filesystem connector reads, when they exist:

- `report.pdf.meta.json`: a JSON object. Each key becomes a custom field `x_<key>`, typed
  by its value unless the key already ends in a field suffix (`_s`, `_ss`, `_i`, `_l`,
  `_d`, `_b`, `_dt`): text `_s`, a list `_ss`, a whole number `_l`, a decimal `_d`, true or
  false `_b`. One level of nesting is flattened (`{"case": {"id": 7}}` gives
  `x_case_id_l`). A key `title` sets the document's title.
- `report.pdf.xmp` or `report.xmp` (Adobe's convention for raw photos): an XMP packet. Its
  Dublin Core title sets the title; `dc:subject` gives `x_keywords_ss`, `dc:creator`
  `x_creator_ss`, `dc:description` `x_description_s`, `dc:rights` `x_rights_s`,
  `xmp:Rating` `x_rating_l` and `xmp:Label` `x_label_s`.

A JSON value wins over the same field from XMP. Keys are made lower case with `_` for
anything but letters and digits; at most 100 fields, texts of 1,000 characters and lists of
100 values are kept, sidecars over 1 MiB are not read, and a field that names one of the
index's own fields is dropped when indexed. `x_sidecar_s` names the sidecars and their
hashes, so a change to a sidecar alone makes discovery process the document again. The
sidecar files themselves are not indexed as documents. A source turns this off with
`sidecars: false`. A sidecar that cannot be parsed is ignored (and logged).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import defusedxml.ElementTree as SafeET  # noqa: N812

log = logging.getLogger(__name__)

JSON_SUFFIX = ".meta.json"
XMP_SUFFIX = ".xmp"
MAX_BYTES = 1 << 20
MAX_FIELDS = 100
MAX_TEXT = 1000
MAX_LIST = 100
SUFFIXES = ("_ss", "_s", "_i", "_l", "_d", "_b", "_dt")
CHANGE_FIELD = "x_sidecar_s"
_KEY = re.compile(r"[^a-z0-9]+")

NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
}
XMP_FIELDS = {
    ("dc", "subject"): "x_keywords_ss",
    ("dc", "creator"): "x_creator_ss",
    ("dc", "description"): "x_description_s",
    ("dc", "rights"): "x_rights_s",
    ("xmp", "Rating"): "x_rating_l",
    ("xmp", "Label"): "x_label_s",
}


def enabled(settings: Mapping[str, Any] | None) -> bool:
    return (settings or {}).get("sidecars", True) is not False


def sidecars_of(file: Path) -> list[Path]:
    """The sidecar files next to `file` that exist, JSON first."""

    candidates = [
        file.with_name(file.name + JSON_SUFFIX),
        file.with_name(file.name + XMP_SUFFIX),
        file.with_suffix(XMP_SUFFIX) if file.suffix.lower() != XMP_SUFFIX else None,
    ]
    out: list[Path] = []
    for c in candidates:
        if c is not None and c != file and c not in out and c.is_file():
            out.append(c)
    return out


def is_sidecar(file: Path, settings: Mapping[str, Any] | None = None) -> bool:
    """Whether `file` is the sidecar of another file in its folder (and so not a document)."""

    if not enabled(settings):
        return False
    name = file.name
    if name.lower().endswith(JSON_SUFFIX):
        return file.with_name(name[: -len(JSON_SUFFIX)]).is_file()
    if file.suffix.lower() == XMP_SUFFIX:
        stem = name[: -len(XMP_SUFFIX)]
        if stem and file.with_name(stem).is_file():
            return True
        return any(p != file and p.stem == file.stem for p in file.parent.glob(f"{file.stem}.*"))
    return False


def _key(raw: str) -> str:
    return _KEY.sub("_", raw.lower()).strip("_")[:60]


def _typed(key: str, value: Any) -> tuple[str, Any] | None:
    """`x_<key><suffix>` and the value as the index takes it; None for what is not kept."""

    if not key:
        return None
    if isinstance(value, list):
        values = [str(v)[:MAX_TEXT] for v in value[:MAX_LIST] if isinstance(v, str | int | float)]
        if not values:
            return None
        return (f"x_{key}" if key.endswith("_ss") else f"x_{key}_ss"), values
    if any(key.endswith(s) for s in SUFFIXES):
        return f"x_{key}", value
    if isinstance(value, bool):
        return f"x_{key}_b", value
    if isinstance(value, int):
        return f"x_{key}_l", value
    if isinstance(value, float):
        return f"x_{key}_d", value
    if isinstance(value, str) and value.strip():
        return f"x_{key}_s", value.strip()[:MAX_TEXT]
    return None


def from_json(raw: bytes) -> tuple[dict[str, Any], str | None]:
    """The custom fields and the title of a `.meta.json` sidecar."""

    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("a .meta.json sidecar is a JSON object")
    fields: dict[str, Any] = {}
    title = data.get("title") if isinstance(data.get("title"), str) else None
    for raw_key, value in data.items():
        if raw_key == "title":
            continue
        items = (
            [(f"{raw_key}_{k}", v) for k, v in value.items()]
            if isinstance(value, dict)
            else [(raw_key, value)]
        )
        for k, v in items:
            typed = _typed(_key(str(k)), v)
            if typed and len(fields) < MAX_FIELDS:
                fields[typed[0]] = typed[1]
    return fields, (title.strip()[:MAX_TEXT] if title and title.strip() else None)


def _values(element: Any) -> list[str]:
    """The text of an XMP property: an rdf:Bag, Seq or Alt of `rdf:li`, or plain text."""

    items = element.findall(".//rdf:li", NS)
    texts = [i.text for i in items] if items else [element.text]
    return [t.strip() for t in texts if t and t.strip()]


def from_xmp(raw: bytes) -> tuple[dict[str, Any], str | None]:
    """The custom fields and the title of an XMP sidecar."""

    root = SafeET.fromstring(raw)
    fields: dict[str, Any] = {}
    title = None
    for prefix, uri in (("dc", NS["dc"]), ("xmp", NS["xmp"])):
        for element in root.iter():
            tag = str(element.tag)
            if not tag.startswith("{" + uri + "}"):
                continue
            local = tag.split("}", 1)[1]
            values = _values(element)
            if not values:
                continue
            if (prefix, local) == ("dc", "title"):
                title = values[0][:MAX_TEXT]
                continue
            name = XMP_FIELDS.get((prefix, local))
            if name is None:
                continue
            _put(fields, name, values)
    # the attribute form: <rdf:Description xmp:Rating="4" xmp:Label="Red">
    for element in root.iter(f"{{{NS['rdf']}}}Description"):
        for attr, value in element.attrib.items():
            for prefix, uri in (("dc", NS["dc"]), ("xmp", NS["xmp"])):
                name = XMP_FIELDS.get((prefix, attr.split("}", 1)[-1]))
                if attr.startswith("{" + uri + "}") and name and name not in fields:
                    if value.strip():
                        _put(fields, name, [value.strip()])
    return fields, title


def _put(fields: dict[str, Any], name: str, values: list[str]) -> None:
    if name.endswith("_ss"):
        fields[name] = [v[:MAX_TEXT] for v in values[:MAX_LIST]]
    elif name.endswith("_l"):
        try:
            fields[name] = int(float(values[0]))
        except ValueError:
            return
    else:
        fields[name] = values[0][:MAX_TEXT]


def read(file: Path) -> tuple[dict[str, Any], str | None]:
    """The merged custom fields and title of `file`'s sidecars ({}, None without any)."""

    fields: dict[str, Any] = {}
    title: str | None = None
    names: list[str] = []
    for sidecar in reversed(sidecars_of(file)):  # XMP first, so JSON wins
        try:
            if sidecar.stat().st_size > MAX_BYTES:
                continue
            raw = sidecar.read_bytes()
            parsed = from_json(raw) if sidecar.name.lower().endswith(JSON_SUFFIX) else from_xmp(raw)
        except (OSError, ValueError, SafeET.ParseError) as exc:
            log.warning("sidecar %s ignored: %s", sidecar, exc)
            continue
        except Exception as exc:  # noqa: BLE001 - defusedxml refusals and the like
            log.warning("sidecar %s ignored: %s: %s", sidecar, type(exc).__name__, exc)
            continue
        fields.update(parsed[0])
        title = parsed[1] or title
        names.append(f"{sidecar.name}@{hashlib.sha256(raw).hexdigest()[:16]}")
    if names:
        fields[CHANGE_FIELD] = ";".join(sorted(names))
    return fields, title


def with_sidecar(
    file: Path, metadata: dict[str, Any], settings: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """A filesystem item's metadata with its sidecars' fields (and title) merged in."""

    if not enabled(settings):
        return metadata
    fields, title = read(file)
    if not fields:
        return metadata
    merged = {**fields, **(metadata.get("fields") or {})}  # path fields stay the connector's
    out = {**metadata, "fields": merged}
    if title and not out.get("title"):
        out["title"] = title
    return out


def changed(stored_fields: Mapping[str, Any], item_metadata: Mapping[str, Any]) -> bool:
    """Whether the sidecars of an item differ from those its document was processed with."""

    now = (item_metadata.get("fields") or {}).get(CHANGE_FIELD)
    return bool(now or stored_fields.get(CHANGE_FIELD)) and now != stored_fields.get(CHANGE_FIELD)
