"""S3 connector: objects in any S3-compatible bucket (AWS, MinIO, Ceph, ...).

Settings: `bucket` (required), `prefix`, `endpoint_url` (for MinIO and
friends), `region`, `access_key`, `secret_key` (prefer the environment:
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY), `include` / `exclude` globs on
the key, `max_bytes`. Change detection by ETag; objects are downloaded to the
source's work area on fetch, and the blob store keys them by the SHA-256 of
what was downloaded, never by the ETag. Credentials stay in the source settings (or the
environment) and are read from there when an object is fetched; they never
travel with the queued item. An `endpoint_url` must be a public address
unless the operator allows the host (see `guard`).
"""

from __future__ import annotations

import fnmatch
import hashlib
from collections.abc import Iterable
from datetime import UTC
from typing import Any

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline.connectors.base import RunReport, path_fields, work_dir, work_root
from oss_pipeline.connectors.guard import (
    MAX_BYTES_CEILING,
    TooLarge,
    byte_cap,
    guard_s3_client,
    refuse_private,
)

CONNECTION_KEYS = ("endpoint_url", "region")  # what an item may carry; never credentials
MAX_OBJECT = 512 * 1024 * 1024  # default per-object cap; a source's max_bytes may change it


def _download(client: Any, bucket: str, key: str, part: Any, max_bytes: int) -> None:
    """Stream an object to `part`, stopping at `max_bytes` read (whatever the listing said)."""

    obj = client.get_object(Bucket=bucket, Key=key)
    body = obj["Body"]
    try:
        declared = int(obj.get("ContentLength") or 0)
        if declared > max_bytes:
            raise TooLarge(f"s3://{bucket}/{key} is {declared} bytes; the cap is {max_bytes}")
        total = 0
        with open(part, "wb") as out:
            for chunk in body.iter_chunks(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise TooLarge(f"s3://{bucket}/{key} is over the cap of {max_bytes} bytes")
                out.write(chunk)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    finally:
        body.close()


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
    # every request goes through the guarded, address-pinned client; the per-source cap is
    # applied to the bytes read in fetch
    return guard_s3_client(boto3.client("s3", **kwargs), max_bytes=MAX_BYTES_CEILING)


class S3Connector:
    name = "s3"
    deletes_missing = True  # a listing error raises, so a run sees every object or fails

    def __init__(self) -> None:
        self._clients: dict[str, Any] = {}
        self.report = RunReport()

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
        self.report = RunReport()
        settings = source.settings
        bucket = str(settings["bucket"])
        prefix = str(settings.get("prefix") or "")
        include = list(settings.get("include") or ["*"])
        exclude = list(settings.get("exclude") or [])
        max_bytes = byte_cap(settings, MAX_OBJECT)
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
                        "fields": path_fields([bucket, *key.split("/")[:-1]], name),
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
        if source is not None:
            tmp = work_dir(source, "s3")
        else:
            tmp = work_root() / "_s3"
            tmp.mkdir(parents=True, exist_ok=True)
        name = str(meta["key"]).rsplit("/", 1)[-1]
        tag = hashlib.sha256(f"{meta['bucket']}/{meta['key']}".encode()).hexdigest()[:16]
        dest = tmp / f"{tag}-{name}"
        part = tmp / f".part-{tag}-{name}"
        cap = byte_cap(settings, MAX_OBJECT)
        _download(client, str(meta["bucket"]), str(meta["key"]), part, cap)
        part.replace(dest)  # the document task removes it once the blob is stored
        return FetchedItem(item=item, path=str(dest))

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []
