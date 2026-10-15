"""Tika server client: text and metadata through /rmeta.

/rmeta returns one JSON object per embedded document (the container first),
each with `X-TIKA:content` and the parsed metadata. Semascope keeps the
container's text and records the embedded items' names; archives and mail
attachments become child documents through their own connector logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


class TikaError(RuntimeError):
    pass


@dataclass
class Extraction:
    text: str
    metadata: dict[str, Any]
    media_type: str | None
    language: str | None
    page_count: int | None
    embedded: list[dict[str, Any]] = field(default_factory=list)
    ocr_used: bool = False


class TikaClient:
    def __init__(
        self, base_url: str, *, timeout: float = 300.0, max_bytes: int = 512 * 1024 * 1024
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_bytes = max_bytes
        self._http = httpx.Client(timeout=timeout)

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
        code = resp.text.strip().lower()
        return code[:2] if code and code.isalpha() else None

    def detect(self, path: Path) -> str:
        with path.open("rb") as fh:
            resp = self._http.put(
                f"{self.base_url}/detect/stream",
                content=fh,
                headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
            )
        resp.raise_for_status()
        return resp.text.strip()

    def extract(self, path: Path, *, ocr: bool = True) -> Extraction:
        size = path.stat().st_size
        if size > self.max_bytes:
            raise TikaError(f"{path.name} is {size} bytes, above the {self.max_bytes} byte cap")
        headers = {
            "Accept": "application/json",
            "Content-Disposition": f'attachment; filename="{path.name}"',
        }
        if not ocr:
            headers["X-Tika-OCRskipOcr"] = "true"
        with path.open("rb") as fh:
            resp = self._http.put(f"{self.base_url}/rmeta/text", content=fh, headers=headers)
        if resp.status_code == 422:
            raise TikaError(f"Tika cannot parse {path.name}: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise TikaError(f"Tika {resp.status_code} on {path.name}: {resp.text[:200]}")
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
            ocr_used=ocr_used,
        )

    def close(self) -> None:
        self._http.close()


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
