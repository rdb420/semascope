"""Webhook delivery (signed, filtered, retried) to a real HTTP receiver, and saved-search alerts.

The receiver is the integration stack's fixture server (tests/integration/stack),
run in a thread on the loopback interface. Alerts run against a stand-in for
Solr; the integration tests run both against the live stack.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import socket
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.contrib.auth.models import User

from oss_server.api.schemas import SearchOut
from oss_server.catalogue.models import SavedSearch
from oss_server.curation.models import Event, Webhook
from oss_server.curation.services import emit
from oss_server.pipeline import tasks

STACK = Path(__file__).resolve().parents[3] / "tests" / "integration" / "stack"
SECRET = "hook-secret"  # noqa: S105 - a test value


@pytest.fixture(scope="module")
def receiver() -> Iterator[str]:
    spec = importlib.util.spec_from_file_location("fixture_server", STACK / "fixture_server.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fixture_server"] = module
    spec.loader.exec_module(module)
    server = module.serve(STACK / "web", host="127.0.0.1", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def loopback_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The receiver runs on 127.0.0.1, which deliveries reach only when the operator allows it."""

    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "127.0.0.1")


def received(receiver: str, name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = httpx.get(f"{receiver}/hook/{name}").json()
    return out


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_webhooks_are_signed_filtered_and_retried(
    receiver: str, db: None, loopback_allowed: None
) -> None:
    emit("proposal.accept_tag", collection="c", subject="before")  # before the hook: not sent
    head = Event.objects.order_by("-pk").first()
    assert head is not None
    hook = Webhook.objects.create(
        url=f"{receiver}/hook/signed",
        kinds=["proposal."],
        secret=SECRET,
        last_event_id=head.pk,
    )
    first = emit("proposal.add_tag", collection="c", subject="https://x#a", payload={"n": 1})
    emit("alert.saved_search", collection="c", subject="not wanted")
    last = emit("proposal.reject_tag", collection="c", subject="https://x#a")

    httpx.post(f"{receiver}/hook/signed/fail", params={"count": 1}).raise_for_status()
    assert tasks.deliver_webhooks() == 0
    hook.refresh_from_db()
    assert hook.last_error == f"500 on event {first.pk}"
    assert hook.last_event_id == head.pk  # the failed event is retried, not skipped

    assert tasks.deliver_webhooks() == 2
    hook.refresh_from_db()
    assert hook.last_error == "" and hook.last_event_id == last.pk
    calls = received(receiver, "signed")
    assert [c["status"] for c in calls] == [500, 204, 204]
    bodies = [json.loads(c["body"]) for c in calls]
    assert [b["id"] for b in bodies] == [first.pk, first.pk, last.pk]  # in order, no alert
    assert bodies[1]["kind"] == "proposal.add_tag" and bodies[1]["payload"] == {"n": 1}
    assert bodies[1]["collection"] == "c" and bodies[1]["subject"] == "https://x#a"
    for call in calls[1:]:
        digest = hmac.new(SECRET.encode(), call["body"].encode(), hashlib.sha256).hexdigest()
        assert call["headers"]["x-semascope-signature"] == f"sha256={digest}"
        assert call["headers"]["x-semascope-event"] == json.loads(call["body"])["kind"]
        assert call["headers"]["content-type"] == "application/json"

    assert tasks.deliver_webhooks() == 0  # nothing new
    assert len(received(receiver, "signed")) == 3


def test_unsigned_disabled_and_unreachable_webhooks(
    receiver: str, db: None, loopback_allowed: None
) -> None:
    unsigned = Webhook.objects.create(url=f"{receiver}/hook/plain")
    Webhook.objects.create(url=f"{receiver}/hook/off", enabled=False)
    down = Webhook.objects.create(url=f"http://127.0.0.1:{closed_port()}/hook")
    event = emit("proposal.add_tag", collection="c", subject="s")

    assert tasks.deliver_webhooks() == 1
    calls = received(receiver, "plain")
    assert len(calls) == 1 and "x-semascope-signature" not in calls[0]["headers"]
    assert received(receiver, "off") == []
    down.refresh_from_db()
    assert down.last_error == f"ConnectError on event {event.pk}" and down.last_event_id == 0
    unsigned.refresh_from_db()
    assert unsigned.last_event_id == event.pk


def test_a_webhook_on_a_private_address_is_refused(
    receiver: str, db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "")
    port = receiver.rsplit(":", 1)[1]
    hooks = [
        Webhook.objects.create(url=f"{receiver}/hook/private"),
        Webhook.objects.create(url=f"http://localhost:{port}/hook/private"),
    ]
    event = emit("proposal.add_tag", collection="c", subject="s")

    assert tasks.deliver_webhooks() == 0
    assert received(receiver, "private") == []
    for hook in hooks:
        hook.refresh_from_db()
        assert hook.last_error.startswith(f"refused on event {event.pk}"), hook.last_error
        assert hook.last_event_id == 0


@pytest.fixture
def redirector(receiver: str) -> Iterator[str]:
    """A receiver that answers every delivery with a redirect to another receiver."""

    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - the http.server API
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(307)
            self.send_header("Location", f"{receiver}/hook/redirected")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_a_webhook_redirect_is_not_followed(
    receiver: str, redirector: str, db: None, loopback_allowed: None
) -> None:
    hook = Webhook.objects.create(url=f"{redirector}/hook")
    event = emit("proposal.add_tag", collection="c", subject="s")

    assert tasks.deliver_webhooks() == 0
    assert received(receiver, "redirected") == []
    hook.refresh_from_db()
    assert hook.last_error == f"307 on event {event.pk} (not followed)"
    assert hook.last_event_id == 0


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "gopher://hooks.example.test/", "http:///hook", "hook", ""]
)
def test_webhook_urls_are_http_with_a_host(url: str, db: None) -> None:
    from django.test import Client

    from oss_server.api.auth import issue_token

    staff = User.objects.create_user("staff", password="x" * 16, is_staff=True)
    auth = {"Authorization": f"Bearer {issue_token(staff, 'test')}"}
    client = Client()

    def add(target: str) -> int:
        return client.post(
            "/api/curation/webhooks",
            data={"url": target},
            content_type="application/json",
            headers=auth,
        ).status_code

    assert add(url) == 422
    assert add("https://hooks.example.test/semascope") == 201
    assert list(Webhook.objects.values_list("url", flat=True)) == [
        "https://hooks.example.test/semascope"
    ]


class FakeSearch:
    """Stands in for Solr: the total each call returns, and the scope it was given."""

    def __init__(self, totals: list[int]) -> None:
        self.totals = totals
        self.calls: list[tuple[Any, list[str] | None]] = []

    def __call__(self, client: Any, coll: str, req: Any, allowed: list[str] | None) -> SearchOut:
        self.calls.append((req, allowed))
        total = self.totals.pop(0)
        return SearchOut(total=total, page=1, page_size=5, took_ms=1, hits=[], facets={})


def test_alerts_fire_only_when_the_total_grows(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from oss_server.api import search as search_mod

    user = User.objects.create_user("watcher", password="x" * 16)
    alert = SavedSearch.objects.create(
        user=user, name="floods", query={"q": "flood", "collection": "c"}, alert=True
    )
    quiet = SavedSearch.objects.create(user=user, name="quiet", query={"q": "x"})
    fake = FakeSearch([3, 3, 5, 4])
    monkeypatch.setattr(search_mod, "search", fake)
    monkeypatch.setattr(tasks, "solr_client", lambda: object())

    assert tasks.check_alerts() == 0  # first check: a baseline, no event
    alert.refresh_from_db()
    assert alert.last_total == 3 and alert.last_checked_at is not None
    assert tasks.check_alerts() == 0  # unchanged
    assert tasks.check_alerts() == 1  # grew by two
    assert tasks.check_alerts() == 0  # shrank: no event, new baseline
    alert.refresh_from_db()
    assert alert.last_total == 4
    events = list(Event.objects.filter(kind="alert.saved_search"))
    assert len(events) == 1
    assert events[0].subject == "floods" and events[0].collection == "c"
    assert events[0].payload["new"] == 2 and events[0].payload["total"] == 5
    assert events[0].payload["user"] == "watcher"
    quiet.refresh_from_db()
    assert quiet.last_checked_at is None  # no alert, never run
    assert len(fake.calls) == 4 and all(req.q == "flood" for req, _ in fake.calls)


def test_alerts_see_only_what_their_owner_may_read(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.api import search as search_mod
    from oss_server.catalogue.models import Collection

    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    Collection.objects.create(slug="secret", name="secret")
    user = User.objects.create_user("outsider", password="x" * 16)
    SavedSearch.objects.create(
        user=user, name="peek", query={"q": "x", "collection": "secret"}, alert=True
    )
    SavedSearch.objects.create(user=user, name="all", query={"q": "x"}, alert=True)
    fake = FakeSearch([1])
    monkeypatch.setattr(search_mod, "search", fake)
    monkeypatch.setattr(tasks, "solr_client", lambda: object())

    assert tasks.check_alerts() == 0
    # the collection it cannot read is not searched; the open query is scoped to nothing
    assert len(fake.calls) == 1 and fake.calls[0][1] == []
    assert SavedSearch.objects.get(name="peek").last_checked_at is None


def test_a_failing_alert_does_not_stop_the_others(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.api import search as search_mod

    user = User.objects.create_user("w2", password="x" * 16)
    SavedSearch.objects.create(user=user, name="a-broken", query={"q": "x"}, alert=True)
    SavedSearch.objects.create(user=user, name="b-fine", query={"q": "y"}, alert=True)

    def search(client: Any, coll: str, req: Any, allowed: Any) -> SearchOut:
        if req.q == "x":
            raise RuntimeError("solr down")
        return SearchOut(total=1, page=1, page_size=5, took_ms=1, hits=[], facets={})

    monkeypatch.setattr(search_mod, "search", search)
    monkeypatch.setattr(tasks, "solr_client", lambda: object())
    assert tasks.check_alerts() == 0
    assert SavedSearch.objects.get(name="b-fine").last_checked_at is not None
    assert SavedSearch.objects.get(name="a-broken").last_checked_at is None
