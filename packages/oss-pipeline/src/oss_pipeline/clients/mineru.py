"""MinerU parse server (v1 jobs API): PDF, Office and images to Markdown on a GPU.

The flow is the server's own: a job is created with the file inline (small
files) or from an upload (large ones), polled until it finishes, and its
Markdown output downloaded through the files API. Every call carries the
bearer token when one is configured, which is what a GPU router in front of
the server checks.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

import httpx

from oss_pipeline.connectors.guard import service_client

INLINE_LIMIT = 16 * 1024 * 1024
DONE = ("completed", "partial", "failed", "canceled")


class MineruError(RuntimeError):
    pass


class MineruClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        *,
        timeout_s: float = 3600.0,
        poll_s: float = 2.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = service_client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=600.0, transport=transport
        )
        self._timeout_s = timeout_s
        self._poll_s = poll_s

    def health(self) -> bool:
        return self._client.get("/v1/health").status_code < 400

    def _upload(self, path: Path, media_type: str) -> str:
        data = path.read_bytes()
        created = self._client.post(
            "/v1/uploads",
            json={"filename": path.name, "bytes": len(data), "mime_type": media_type},
        )
        created.raise_for_status()
        upload_id = created.json()["id"]
        put = self._client.put(
            f"/v1/uploads/{upload_id}/content",
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        put.raise_for_status()
        done = self._client.post(f"/v1/uploads/{upload_id}/complete", json={})
        done.raise_for_status()
        file_obj = done.json().get("file") or {}
        if not file_obj.get("id"):
            raise MineruError("upload completed without a file id")
        return str(file_obj["id"])

    def parse(self, path: Path, media_type: str, *, ocr_mode: str = "auto") -> str:
        """Markdown for one file; raises MineruError when the server fails the file."""

        size = path.stat().st_size
        if size <= INLINE_LIMIT:
            source = {
                "type": "inline",
                "name": path.name,
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        else:
            source = {"type": "file_id", "file_id": self._upload(path, media_type)}
        created = self._client.post(
            "/v1/parse/jobs",
            json={
                "files": [{"source": source}],
                "ocr_mode": ocr_mode,
                "output_formats": ["markdown"],
            },
        )
        created.raise_for_status()
        job = created.json()
        deadline = time.monotonic() + self._timeout_s
        while job.get("status") not in DONE:
            if time.monotonic() > deadline:
                job_id = job.get("job_id")
                raise MineruError(f"job {job_id} did not finish in {self._timeout_s:.0f}s")
            time.sleep(self._poll_s)
            poll = self._client.get(f"/v1/parse/jobs/{job['job_id']}")
            poll.raise_for_status()
            job = poll.json()
        files = job.get("files") or []
        if not files or files[0].get("status") != "completed":
            error = (files[0].get("error") or {}).get("message") if files else job.get("status")
            raise MineruError(f"parse failed: {error}")
        ref = (files[0].get("output_files") or {}).get("markdown") or {}
        if not ref.get("file_id"):
            raise MineruError("parse finished without a markdown output")
        content = self._client.get(f"/v1/files/{ref['file_id']}/content")
        content.raise_for_status()
        return content.content.decode("utf-8", errors="replace")
