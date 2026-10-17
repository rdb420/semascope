"""Security headers the server sets itself: the Content-Security-Policy of its HTML pages.

The browser UI, the Django admin and the sign-on pages get a policy that allows
scripts only from this origin (no inline script, no eval), images and tile requests
from this origin and the configured map-tile server, workers from this origin and
`blob:` (the map), form posts to this origin and the single sign-on issuer, and no
framing. The policy names the map-tile and issuer origins, which only the server's
settings know, which is why the server sets it rather than Caddy (deploy/caddy sets
the headers that do not depend on settings). A response that already carries a
policy keeps it: originals are served with their own sandboxing policy (`api/files.py`).
JSON and file responses get no page policy (`nosniff` and their media type cover them).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from urllib.parse import urlsplit

from django.http import HttpRequest, HttpResponse

from oss_core.config import get_settings


def _origin(url: str) -> str:
    """`scheme://host[:port]` of a URL template such as `https://tile.example/{z}/{x}/{y}.png`."""

    if not url:
        return ""
    try:
        parts = urlsplit(url.replace("{s}", "a"))
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname or "{" in parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


@lru_cache(maxsize=8)
def page_policy(tile_url: str = "", issuer_url: str = "") -> str:
    tiles = _origin(tile_url)
    issuer = _origin(issuer_url)
    extra_tiles = f" {tiles}" if tiles else ""
    extra_issuer = f" {issuer}" if issuer else ""
    directives = [
        "default-src 'self'",
        "script-src 'self'",
        # charting and map libraries set style attributes; inline style cannot run code
        "style-src 'self' 'unsafe-inline'",
        f"img-src 'self' data: blob:{extra_tiles}",
        "font-src 'self' data:",
        f"connect-src 'self'{extra_tiles}",
        "worker-src 'self' blob:",
        "child-src 'self' blob:",
        "frame-src 'none'",
        "object-src 'none'",
        "base-uri 'self'",
        f"form-action 'self'{extra_issuer}",
        "frame-ancestors 'none'",
        "manifest-src 'self'",
    ]
    return "; ".join(directives)


def current_page_policy() -> str:
    s = get_settings()
    return page_policy(s.semascope_map_tile_url.strip(), s.semascope_oidc_server_url.strip())


class SecurityHeadersMiddleware:
    """Adds the page policy to HTML responses that do not set their own."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self.policy = current_page_policy()

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        kind = response.get("Content-Type", "").split(";")[0].strip().lower()
        if kind == "text/html" and "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = self.policy
        return response
