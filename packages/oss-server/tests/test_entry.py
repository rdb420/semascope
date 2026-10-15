import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from oss_server.entry import _Handler


def test_healthz_answers() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/healthz") as resp:  # noqa: S310
            assert json.load(resp)["status"] == "ok"
    finally:
        server.shutdown()
