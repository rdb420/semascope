#!/usr/bin/env python3
"""Pin every image in compose*.yaml to the digest its tag points to now.

    uv run python deploy/pin_images.py            # resolve and rewrite in place
    uv run python deploy/pin_images.py --dry-run  # show what would change
    uv run python deploy/pin_images.py --check    # fail if an image has no digest (offline)

Each `image: name:tag` (or `name:tag@sha256:old`) becomes `image: name:tag@sha256:new`,
where the digest is the registry's current one for the tag: the multi-platform index when
the image has one, so every architecture stays pullable. The tag is kept for readers;
Docker pulls by the digest. Images whose reference contains a variable (the Semascope
images, `${SEMASCOPE_IMAGE_TAG}`) are left alone: their releases are pinned by tag and
signed (see `.github/workflows/release.yml`).

Talks to the registries' HTTP API (anonymous pull tokens) with the standard library
only, so `just bump-images` needs no extra packages and no Docker daemon.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
IMAGE_LINE = re.compile(r"^(?P<lead>\s*image:\s*)(?P<ref>[^\s#]+)(?P<trail>\s*(#.*)?)$")
ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


@dataclass(frozen=True)
class ImageRef:
    registry: str  # e.g. registry-1.docker.io, ghcr.io
    repository: str  # e.g. library/postgres
    tag: str
    digest: str | None
    written: str  # the name as written, without tag or digest

    @property
    def pinned(self) -> str:
        return f"{self.written}:{self.tag}"


def parse_ref(ref: str) -> ImageRef:
    """Split `name[:tag][@digest]` the way Docker does (docker.io and library/ implied)."""

    name, _, digest = ref.partition("@")
    tag = "latest"
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = name.rsplit(":", 1)
    first = name.split("/", 1)[0]
    if "/" in name and ("." in first or ":" in first or first == "localhost"):
        registry, repository = name.split("/", 1)
    else:
        registry, repository = "docker.io", name
    if registry == "docker.io":
        registry = "registry-1.docker.io"
        if "/" not in repository:
            repository = f"library/{repository}"
    return ImageRef(registry, repository, tag, digest or None, name)


def parse_challenge(header: str) -> dict[str, str]:
    """`Bearer realm="…",service="…",scope="…"` -> {realm, service, scope}."""

    scheme, _, params = header.partition(" ")
    if scheme.lower() != "bearer":
        return {}
    return dict(re.findall(r'(\w+)="([^"]*)"', params))


def _request(url: str, method: str = "GET", headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, method=method, headers=headers or {})  # noqa: S310
    return urllib.request.urlopen(req, timeout=30)  # noqa: S310 - https registry URLs only


def resolve(image: ImageRef) -> str:
    """The registry's current digest for the image's tag."""

    url = f"https://{image.registry}/v2/{image.repository}/manifests/{image.tag}"
    headers = {"Accept": ACCEPT}
    try:
        resp = _request(url, "HEAD", headers)
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
        challenge = parse_challenge(exc.headers.get("WWW-Authenticate", ""))
        if not challenge.get("realm"):
            raise
        query = {k: v for k, v in challenge.items() if k in ("service", "scope")}
        query.setdefault("scope", f"repository:{image.repository}:pull")
        token_url = f"{challenge['realm']}?{urllib.parse.urlencode(query)}"
        with _request(token_url) as tok:
            body = json.load(tok)
        headers["Authorization"] = f"Bearer {body.get('token') or body.get('access_token')}"
        resp = _request(url, "HEAD", headers)
    with resp:
        digest = resp.headers.get("Docker-Content-Digest", "")
    if not digest.startswith("sha256:"):
        raise RuntimeError(f"{image.pinned}: registry sent no digest")
    return str(digest)


def pin_text(text: str, resolver: Callable[[ImageRef], str]) -> tuple[str, list[str]]:
    """Rewrite every fixed image line of a compose file; returns the text and a change log."""

    out, log = [], []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        m = IMAGE_LINE.match(body)
        if not m or "${" in m["ref"]:
            if m:
                log.append(f"skip  {m['ref']} (variable tag)")
            out.append(line)
            continue
        image = parse_ref(m["ref"])
        digest = resolver(image)
        new_ref = f"{image.pinned}@{digest}"
        if new_ref != m["ref"]:
            log.append(f"pin   {image.pinned} -> {digest}")
        else:
            log.append(f"keep  {image.pinned}@{digest[:19]}…")
        out.append(f"{m['lead']}{new_ref}{m['trail']}{line[len(body) :]}")
    return "".join(out), log


def unpinned(text: str) -> list[str]:
    refs = [m["ref"] for m in map(IMAGE_LINE.match, text.splitlines()) if m]
    return [r for r in refs if "${" not in r and "@sha256:" not in r]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="*", type=Path, help="default: compose*.yaml in the repo root")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true", help="only verify that digests are present")
    args = ap.parse_args(argv)
    files = args.files or sorted(ROOT.glob("compose*.yaml"))
    failed = False
    cache: dict[tuple[str, str, str], str] = {}

    def cached(image: ImageRef) -> str:
        key = (image.registry, image.repository, image.tag)
        if key not in cache:
            cache[key] = resolve(image)
        return cache[key]

    for path in files:
        text = path.read_text(encoding="utf-8")
        if args.check:
            for ref in unpinned(text):
                print(f"{path.name}: {ref} has no digest")
                failed = True
            continue
        new, log = pin_text(text, cached)
        print(f"{path.name}:")
        for entry in log:
            print(f"  {entry}")
        if new != text and not args.dry_run:
            path.write_text(new, encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
