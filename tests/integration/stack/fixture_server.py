"""A small HTTP server of test fixtures: a web site, feeds, a webhook receiver and a mock IdP.

Standard library only, so it runs in the Semascope image as the `fixtures`
service of the integration stack (tests/integration/stack/compose.yaml) and in
a thread inside the unit tests (packages/oss-pipeline/tests/test_web_feed_live.py).

    python fixture_server.py [--port 8000] [--root web/]

Routes

    GET  /<path>                  a file under --root; `@BASE@` in text files becomes
                                  http://<Host header>, so links and sitemaps point back here
    GET  /big.html                200 kB of HTML with a Content-Length
    GET  /big-stream.html         200 kB of HTML without one (close-delimited)
    GET  /redirect-private.html   302 to a private address
    GET  /status/<code>[/...]     answers with that HTTP status
    PUT  /files/<path>            serve the request body at /<path> (over any file there)
    DELETE /files/<path>          stop serving it (a file under --root shows again, else 404)
    POST /answer?path=/p&code=503 answer /p with that status until code=0 (a site going down)
    POST /hook/<name>             records the request; answers 500 while failures are armed
    GET  /hook/<name>             what was recorded, as JSON
    POST /hook/<name>/fail?count=N  answer the next N deliveries with 500
    GET  /oidc/...                an OpenID Connect provider that signs in one fixed user
    GET  /health                  ok
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

BIG = 200_000
PRIVATE_TARGET = "http://10.255.255.1/internal.html"
TEXT_TYPES = (".html", ".xml", ".txt")

# the mock IdP's client and the one user it signs in
OIDC_CLIENT_ID = "semascope-it"
OIDC_CLIENT_SECRET = "semascope-it-oidc-secret"  # noqa: S105 - a test fixture, not a secret
OIDC_USER = {
    "sub": "it-sso-0001",
    "email": "sso.user@example.test",
    "email_verified": True,
    "name": "Sso User",
    "given_name": "Sso",
    "family_name": "User",
    "preferred_username": "sso.user",
}


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.hooks: dict[str, list[dict[str, Any]]] = {}
        self.failures: dict[str, int] = {}
        self.codes: dict[str, dict[str, str]] = {}
        self.files: dict[str, tuple[bytes, str]] = {}  # path -> (body, content type)
        self.answers: dict[str, int] = {}  # path -> forced status


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def id_token(issuer: str, nonce: str | None) -> str:
    """An HS256 ID token signed with the client secret (OIDC Core 10.1)."""

    now = int(time.time())
    claims: dict[str, Any] = {
        **OIDC_USER,
        "iss": issuer,
        "aud": OIDC_CLIENT_ID,
        "iat": now,
        "exp": now + 600,
    }
    if nonce:
        claims["nonce"] = nonce
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "it"}).encode())
    payload = _b64(json.dumps(claims).encode())
    signing = f"{header}.{payload}".encode()
    sig = hmac.new(OIDC_CLIENT_SECRET.encode(), signing, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(sig)}"


def make_handler(root: Path, state: State) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "semascope-fixtures/1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib name
            print(f"{self.address_string()} {format % args}", flush=True)

        # --- helpers ---------------------------------------------------------------------

        @property
        def base(self) -> str:
            return f"http://{self.headers.get('Host') or 'localhost'}"

        def send(
            self, status: int, body: bytes, ctype: str, headers: dict[str, str] | None = None
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_json(self, status: int, data: Any) -> None:
            self.send(status, json.dumps(data).encode(), "application/json")

        def body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        # --- GET ---------------------------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 - stdlib name
            url = urlparse(self.path)
            path = url.path
            if path == "/health":
                return self.send(200, b"ok", "text/plain")
            if path == "/big.html":
                page = b"<html><body>" + b"x" * BIG + b"</body></html>"
                return self.send(200, page, "text/html")
            if path == "/big-stream.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                for _ in range(BIG // 10_000):
                    self.wfile.write(b"y" * 10_000)
                return None
            if path == "/redirect-private.html":
                return self.send(302, b"", "text/plain", {"Location": PRIVATE_TARGET})
            if path.startswith("/status/"):
                code = int(path.split("/")[2])
                return self.send(code, f"status {code}".encode(), "text/plain")
            with state.lock:
                forced = state.answers.get(path)
                served = state.files.get(path)
            if forced:
                return self.send(forced, b"forced answer", "text/plain")
            if served is not None:
                body, ctype = served
                return self.send(
                    200,
                    body.replace(b"@BASE@", self.base.encode()),
                    ctype,
                    {"Last-Modified": "Tue, 03 Feb 2026 10:00:00 GMT"},
                )
            if path.startswith("/hook/"):
                with state.lock:
                    return self.send_json(200, state.hooks.get(path[6:], []))
            if path.startswith("/oidc/"):
                return self.oidc_get(path[6:], parse_qs(url.query))
            return self.static(path)

        do_HEAD = do_GET  # noqa: N815 - stdlib name

        def static(self, path: str) -> None:
            if path.endswith("/"):
                path += "index.html"
            file = (root / path.lstrip("/")).resolve()
            if not file.is_relative_to(root) or not file.is_file():
                return self.send(404, b"not found", "text/plain")
            data = file.read_bytes()
            if file.suffix in TEXT_TYPES:
                data = data.replace(b"@BASE@", self.base.encode())
            ctype = {
                ".xml": "application/xml",
                ".html": "text/html; charset=utf-8",
                ".txt": "text/plain; charset=utf-8",
            }.get(file.suffix) or (mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            if file.parent.name == "feeds":
                ctype = "application/atom+xml" if "atom" in file.name else "application/rss+xml"
            return self.send(200, data, ctype, {"Last-Modified": "Tue, 03 Feb 2026 10:00:00 GMT"})

        # --- POST --------------------------------------------------------------------------

        def do_PUT(self) -> None:  # noqa: N802 - stdlib name
            url = urlparse(self.path)
            if not url.path.startswith("/files/"):
                return self.send(404, b"not found", "text/plain")
            target = url.path[len("/files") :]
            ctype = self.headers.get("Content-Type") or "text/html; charset=utf-8"
            with state.lock:
                state.files[target] = (self.body(), ctype)
            return self.send_json(200, {"serving": target})

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib name
            url = urlparse(self.path)
            target = url.path[len("/files") :] if url.path.startswith("/files/") else ""
            with state.lock:
                state.files.pop(target, None)
            return self.send_json(200, {"removed": target})

        def do_POST(self) -> None:  # noqa: N802 - stdlib name
            url = urlparse(self.path)
            parts = url.path.strip("/").split("/")
            if url.path == "/answer":
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                with state.lock:
                    if int(query.get("code", "0")):
                        state.answers[query["path"]] = int(query["code"])
                    else:
                        state.answers.pop(query.get("path", ""), None)
                return self.send_json(200, {"answers": state.answers})
            if parts[0] == "hook" and len(parts) == 3 and parts[2] == "fail":
                count = int(parse_qs(url.query).get("count", ["1"])[0])
                with state.lock:
                    state.failures[parts[1]] = count
                return self.send_json(200, {"armed": count})
            if parts[0] == "hook" and len(parts) == 2:
                body = self.body()
                with state.lock:
                    failing = state.failures.get(parts[1], 0) > 0
                    if failing:
                        state.failures[parts[1]] -= 1
                    state.hooks.setdefault(parts[1], []).append(
                        {
                            "headers": {k.lower(): v for k, v in self.headers.items()},
                            "body": body.decode("utf-8", "replace"),
                            "status": 500 if failing else 204,
                        }
                    )
                if failing:
                    return self.send(500, b"armed failure", "text/plain")
                self.send_response(204)
                self.end_headers()
                return None
            if url.path == "/oidc/token":
                return self.oidc_token(parse_qs(self.body().decode()))
            return self.send(404, b"not found", "text/plain")

        # --- mock OpenID Connect provider --------------------------------------------------

        def oidc_get(self, what: str, query: dict[str, list[str]]) -> None:
            issuer = f"{self.base}/oidc"
            if what == ".well-known/openid-configuration":
                return self.send_json(
                    200,
                    {
                        "issuer": issuer,
                        "authorization_endpoint": f"{issuer}/authorize",
                        "token_endpoint": f"{issuer}/token",
                        "userinfo_endpoint": f"{issuer}/userinfo",
                        "jwks_uri": f"{issuer}/jwks",
                        "response_types_supported": ["code"],
                        "subject_types_supported": ["public"],
                        "id_token_signing_alg_values_supported": ["HS256"],
                        "token_endpoint_auth_methods_supported": ["client_secret_post"],
                        "scopes_supported": ["openid", "profile", "email"],
                    },
                )
            if what == "authorize":
                one = {k: v[0] for k, v in query.items()}
                if one.get("client_id") != OIDC_CLIENT_ID or not one.get("redirect_uri"):
                    return self.send(400, b"unknown client", "text/plain")
                code = secrets.token_urlsafe(16)
                with state.lock:
                    state.codes[code] = {
                        "nonce": one.get("nonce", ""),
                        "redirect_uri": one["redirect_uri"],
                    }
                sep = "&" if "?" in one["redirect_uri"] else "?"
                target = (
                    one["redirect_uri"]
                    + sep
                    + urlencode({"code": code, "state": one.get("state", "")})
                )
                return self.send(302, b"", "text/plain", {"Location": target})
            if what == "userinfo":
                if not (self.headers.get("Authorization") or "").startswith("Bearer it-"):
                    return self.send(401, b"no token", "text/plain")
                return self.send_json(200, OIDC_USER)
            if what == "jwks":
                return self.send_json(200, {"keys": []})
            return self.send(404, b"not found", "text/plain")

        def oidc_token(self, form: dict[str, list[str]]) -> None:
            one = {k: v[0] for k, v in form.items()}
            if one.get("client_id") != OIDC_CLIENT_ID or one.get("client_secret") != (
                OIDC_CLIENT_SECRET
            ):
                return self.send_json(401, {"error": "invalid_client"})
            with state.lock:
                grant = state.codes.pop(one.get("code", ""), None)
            if grant is None or grant["redirect_uri"] != one.get("redirect_uri"):
                return self.send_json(400, {"error": "invalid_grant"})
            return self.send_json(
                200,
                {
                    "access_token": "it-" + secrets.token_urlsafe(16),
                    "token_type": "Bearer",
                    "expires_in": 600,
                    "id_token": id_token(f"{self.base}/oidc", grant["nonce"] or None),
                },
            )

    return Handler


def serve(root: Path, host: str = "0.0.0.0", port: int = 0) -> ThreadingHTTPServer:  # noqa: S104
    """A server bound to host:port (0 picks a free port); call serve_forever() on it."""

    server = ThreadingHTTPServer((host, port), make_handler(root.resolve(), State()))
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent / "web")
    args = parser.parse_args()
    server = serve(args.root, port=args.port)
    print(f"fixtures on :{server.server_address[1]} from {args.root}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
