"""S3 connector: objects in any S3-compatible bucket (AWS, MinIO, Ceph, ...).

Settings: `bucket` (required), `prefix`, `endpoint_url` (for MinIO and
friends), `region`, `access_key`, `secret_key` (prefer the environment:
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY), `include` / `exclude` globs on
the key, `max_bytes`. Change detection by ETag; objects are downloaded to a
temporary file on fetch. Credentials stay in the source settings (or the
environment) and are read from there when an object is fetched; they never
travel with the queued item. An `endpoint_url` must be a public address
unless the operator allows the host (see `guard`).
"""

from __future__ import annotations

import fnmatch
import hashlib
import tempfile
from collections.abc import Iterable
from datetime import UTC
from pathlib import Path
from typing import Any

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline.connectors.guard import refuse_private

CONNECTION_KEYS = ("endpoint_url", "region")  # what an item may carry; never credentials


def _client(settings: dict[str, Any]) -> Any:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("the s3 connector needs boto3 (uv add boto3)") from exc
    kwargs: dict[str, Any] = {}
    if settings.get("endpoint_url"):
        kwargs["endpoint_url"] = settings["endpoint_url"]
    if settings.get("region"):
        kwargs["region_name"] = settings["region"]
    if settings.get("access_key") and settings.get("secret_key"):
        kwargs["aws_access_key_id"] = settings["access_key"]
        kwargs["aws_secret_access_key"] = settings["secret_key"]
    return boto3.client("s3", **kwargs)


class S3Connector:
    name = "s3"

    def __init__(self) -> None:
        self._clients: dict[str, Any] = {}

    def _for(self, settings: dict[str, Any]) -> Any:
        if settings.get("endpoint_url"):
            refuse_private(str(settings["endpoint_url"]))  # on every use: addresses change
        secret = hashlib.sha256(str(settings.get("secret_key") or "").encode()).hexdigest()[:16]
        key = (
            f"{settings.get('endpoint_url')}|{settings.get('region')}|"
            f"{settings.get('access_key')}|{secret}"
        )
        if key not in self._clients:
            self._clients[key] = _client(settings)
        return self._clients[key]

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        settings = source.settings
        bucket = str(settings["bucket"])
        prefix = str(settings.get("prefix") or "")
        include = list(settings.get("include") or ["*"])
        exclude = list(settings.get("exclude") or [])
        max_bytes = int(settings.get("max_bytes", 512 * 1024 * 1024))
        client = self._for(settings)
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = str(obj["Key"])
                name = key.rsplit("/", 1)[-1]
                if key.endswith("/") or not name:
                    continue
                if any(fnmatch.fnmatch(key, p) for p in exclude) or not any(
                    fnmatch.fnmatch(key, p) for p in include
                ):
                    continue
                size = int(obj.get("Size") or 0)
                if size == 0 or size > max_bytes:
                    continue
                modified = obj.get("LastModified")
                yield SourceItem(
                    uri=f"s3://{bucket}/{key}",
                    content_hash=str(obj.get("ETag") or "").strip('"'),
                    modified_at=modified.astimezone(UTC) if modified else None,
                    size=size,
                    metadata={
                        "bucket": bucket,
                        "key": key,
                        **{k: settings.get(k) for k in CONNECTION_KEYS},
                    },
                )

    def fetch(self, item: SourceItem, source: SourceConfig | None = None) -> FetchedItem:
        """Download the object, with the credentials of the source it came from."""

        meta = item.metadata
        settings = (
            dict(source.settings)
            if source is not None
            else {k: meta.get(k) for k in CONNECTION_KEYS}
        )
        client = self._for(settings)
        tmp = Path(tempfile.gettempdir()) / "semascope-s3"
        tmp.mkdir(parents=True, exist_ok=True)
        dest = tmp / f"{item.content_hash or 'obj'}-{str(meta['key']).rsplit('/', 1)[-1]}"
        if not dest.exists():
            client.download_file(str(meta["bucket"]), str(meta["key"]), str(dest))
        return FetchedItem(item=item, path=str(dest))

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []
