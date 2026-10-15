"""`oss-server` console script: runs the Django application.

Phase 0 ships a placeholder that answers /healthz so the compose stack can be
brought up and checked end to end before the Django project lands in Phase 1.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from oss_core import __version__


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path == "/healthz":
            body = json.dumps({"status": "ok", "version": __version__, "phase": 0}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def main() -> None:
    parser = argparse.ArgumentParser(prog="oss-server")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - container-internal
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--reload", action="store_true", help="accepted for compose.dev.yaml; no-op in Phase 0"
    )
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
