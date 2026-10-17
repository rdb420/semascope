"""Tika server client: text and metadata through /rmeta.

/rmeta returns one JSON object per embedded document (the container first),
each with `X-TIKA:content` and the parsed metadata. Semascope keeps the
container's text and metadata, and each embedded item's name, media type and
text (an attachment of an .eml, the files of a zip, an image in a Word file,
OCRed when OCR is on), which the extract stage adds to the container's text
within limits. Archive and mail sources make their members child documents
instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oss_pipeline.connectors.guard import MAX_BYTES_CEILING, service_client


class TikaError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        # busy or restarting; a 500 is a parser crash on this file and is not retried
        self.transient = status in (408, 429, 502, 503, 504)


@dataclass
class Extraction:
    text: str
    metadata: dict[str, Any]
    media_type: str | None
    language: str | None
    page_count: int | None
    embedded: list[dict[str, Any]] = field(default_factory=list)
    ocr_used: bool = False
    # (name, media type, text) of each embedded item with text, in Tika's order
    embedded_text: list[tuple[str, str, str]] = field(default_factory=list)


# Tesseract language codes: `eng`, `deu`, `chi_sim`, `deu_latf`, joined with `+`
OCR_LANGUAGES = re.compile(r"[a-z]{3}(?:_[a-z]{2,5})?(?:\+[a-z]{3}(?:_[a-z]{2,5})?){0,7}")


def valid_ocr_languages(value: object) -> str | None:
    """The value as a Tesseract language list (`eng+deu`), or None when it is not one."""

    if isinstance(value, list | tuple):
        value = "+".join(str(v).strip() for v in value)
    if not isinstance(value, str):
        return None
    value = value.strip().lower().replace(",", "+").replace(" ", "")
    return value if OCR_LANGUAGES.fullmatch(value) else None


class TikaClient:
    def __init__(
        self, base_url: str, *, timeout: float = 300.0, max_bytes: int = 512 * 1024 * 1024
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_bytes = max_bytes
        self._http = service_client(timeout=timeout, max_bytes=MAX_BYTES_CEILING)

    def version(self) -> str:
        resp = self._http.get(f"{self.base_url}/version")
        resp.raise_for_status()
        return resp.text.strip()

    def language(self, text: str) -> str | None:
        """Language of a text through Tika's detector; None when it has no opinion."""

        sample = text[:20000].encode("utf-8", errors="ignore")
        if len(sample) < 20:
            return None
        resp = self._http.put(
            f"{self.base_url}/language/string",
            content=sample,
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        if resp.status_code != 200:
            return None
        return primary_language(resp.text)

    def detect(self, path: Path) -> str:
        with path.open("rb") as fh:
            resp = self._http.put(
                f"{self.base_url}/detect/stream",
                content=fh,
                headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
            )
        resp.raise_for_status()
        return resp.text.strip()

    def extract(
        self, path: Path, *, ocr: bool = True, ocr_languages: str | None = None
    ) -> Extraction:
        size = path.stat().st_size
        if size > self.max_bytes:
            raise TikaError(f"{path.name} is {size} bytes, above the {self.max_bytes} byte cap")
        headers = {
            "Accept": "application/json",
            "Content-Disposition": f'attachment; filename="{path.name}"',
        }
        if not ocr:
            headers["X-Tika-OCRskipOcr"] = "true"
        elif ocr_languages and valid_ocr_languages(ocr_languages):
            headers["X-Tika-OCRLanguage"] = str(valid_ocr_languages(ocr_languages))
        with path.open("rb") as fh:
            resp = self._http.put(f"{self.base_url}/rmeta/text", content=fh, headers=headers)
        if resp.status_code == 422:
            raise TikaError(f"Tika cannot parse {path.name}: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise TikaError(
                f"Tika {resp.status_code} on {path.name}: {resp.text[:200]}", resp.status_code
            )
        items: list[dict[str, Any]] = resp.json()
        if not items:
            raise TikaError(f"Tika returned nothing for {path.name}")
        main = items[0]
        text = str(main.get("X-TIKA:content") or "").strip()
        media_type = _first(main, "Content-Type")
        language = _first(main, "language") or _first(main, "dc:language")
        pages = _first(main, "xmpTPg:NPages") or _first(main, "meta:page-count")
        ocr_used = any(
            "TesseractOCRParser" in str(v) for v in _as_list(main.get("X-TIKA:Parsed-By-Full-Set"))
        )
        return Extraction(
            text=text,
            metadata={k: v for k, v in main.items() if k != "X-TIKA:content"},
            media_type=_normalise_media_type(media_type),
            language=language,
            page_count=int(pages) if pages and str(pages).isdigit() else None,
            embedded=[
                {k: v for k, v in item.items() if k != "X-TIKA:content"} for item in items[1:]
            ],
            ocr_used=ocr_used
            or any(
                "TesseractOCRParser" in str(v)
                for item in items[1:]
                for v in _as_list(item.get("X-TIKA:Parsed-By-Full-Set"))
            ),
            embedded_text=[
                (
                    _embedded_name(item, n),
                    _normalise_media_type(_first(item, "Content-Type")) or "",
                    str(item.get("X-TIKA:content") or "").strip(),
                )
                for n, item in enumerate(items[1:], start=1)
                if str(item.get("X-TIKA:content") or "").strip()
            ],
        )

    def close(self) -> None:
        self._http.close()


def primary_language(value: str | None) -> str | None:
    """`en`, `zh-CN`, `pt_BR` -> the primary subtag (`en`, `zh`, `pt`); None for anything else."""

    code = (value or "").strip().lower().replace("_", "-").split("-", 1)[0]
    if code in ("und", "mul", "zxx", "mis"):  # undetermined, several, none, uncoded
        return None
    return code if 2 <= len(code) <= 3 and code.isalpha() else None


def _embedded_name(item: dict[str, Any], n: int) -> str:
    path = _first(item, "X-TIKA:embedded_resource_path") or ""
    name = _first(item, "resourceName") or path.rsplit("/", 1)[-1]
    return name or f"embedded item {n}"


def _normalise_media_type(value: str | None) -> str | None:
    """`image/ocr-png` is Tika's marker for an OCRed image; callers want `image/png`."""

    if not value:
        return None
    mt = value.split(";")[0].strip().lower()
    major, _, minor = mt.partition("/")
    if minor.startswith("ocr-"):
        minor = minor[4:]
    return f"{major}/{minor}" if minor else major


def _first(meta: dict[str, Any], key: str) -> str | None:
    value = meta.get(key)
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value) if value is not None else None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list | tuple) else [value]
