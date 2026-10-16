"""What leaves the stack on its own: webhooks (signed, retried) and saved-search alerts.

Both run on the worker from the beat's once-a-minute tick. The tests queue the
task themselves (`run_task`) rather than wait for the tick; a tick that lands
in between only delivers sooner. The webhook receiver is the `fixtures`
service of tests/integration/stack/compose.yaml.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any

import httpx
import pytest
from _stack import doc_id, ensure_collection, run_task, service_url, wait_for

IT = "https://semascope.dev/ontology/it#"
SECRET = "it-webhook-secret"  # noqa: S105 - a fixture


def last_event_id(api: httpx.Client) -> int:
    cursor = 0
    while page := api.get("/curation/events", params={"since_id": cursor, "limit": 1000}).json():
        cursor = page[-1]["id"]
    return cursor


def webhook(api: httpx.Client, hook_id: int) -> dict[str, Any]:
    hooks: list[dict[str, Any]] = api.get("/curation/webhooks").raise_for_status().json()
    return next(h for h in hooks if h["id"] == hook_id)


def received(receiver: str, name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = httpx.get(f"{receiver}/hook/{name}").raise_for_status().json()
    return out


def test_webhook_delivery_signature_filter_and_retry(
    api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    receiver = service_url("fixtures", 8000)
    name = "it-" + secrets.token_hex(4)
    resp = api.post(
        "/curation/webhooks",
        json={
            "url": f"http://fixtures:8000/hook/{name}",
            "kinds": ["proposal.add_tag"],
            "secret": SECRET,
        },
    )
    assert resp.status_code == 201, resp.text
    hook = resp.json()
    assert hook["last_event_id"] == last_event_id(api), "a new webhook starts at the outbox head"
    try:
        httpx.post(f"{receiver}/hook/{name}/fail", params={"count": 1}).raise_for_status()
        doc = docs_by_slug["song-celestial"]["id"]
        iri = IT + "webhook-" + name
        for decision in ("accept", "reject"):  # add_tag, then reject_tag (filtered out)
            api.post(
                f"/curation/documents/{doc}/tags",
                json={"iri": iri, "decision": decision, "label": "Webhook test"},
            ).raise_for_status()
        events = api.get(
            "/curation/events", params={"since_id": hook["last_event_id"], "kind": "proposal."}
        ).json()
        added = next(e for e in events if e["kind"] == "proposal.add_tag" and e["subject"] == iri)
        rejected = next(e for e in events if e["kind"] == "proposal.reject_tag")

        # first delivery: the receiver answers 500, the cursor stays, the error is recorded
        run_task("semascope.deliver_webhooks")
        attempts = wait_for(
            lambda: received(receiver, name), timeout=120, every=1.0, what="a first delivery"
        )
        assert attempts[0]["status"] == 500, attempts
        state = webhook(api, hook["id"])
        if not any(a["status"] == 204 for a in received(receiver, name)):  # no retry yet
            assert state["last_error"] == f"500 on event {added['id']}", state
            assert state["last_event_id"] < added["id"], state

        # the next run retries the same event and moves the cursor past the filtered one
        run_task("semascope.deliver_webhooks")
        wait_for(
            lambda: (
                (h := webhook(api, hook["id"]))["last_event_id"] >= rejected["id"]
                and not h["last_error"]
            ),
            timeout=120,
            every=1.0,
            what="the retried delivery",
        )
        delivered = [a for a in received(receiver, name) if a["status"] == 204]
        bodies = [json.loads(a["body"]) for a in delivered]
        assert {b["id"] for b in bodies} == {added["id"]}, bodies  # reject_tag filtered out
        body = bodies[0]
        assert body["kind"] == "proposal.add_tag" and body["subject"] == iri
        assert body["collection"] == added["collection"]
        assert body["payload"]["document_id"] == doc
        first_ok = delivered[0]
        assert first_ok["headers"]["x-semascope-event"] == "proposal.add_tag"
        expected = hmac.new(SECRET.encode(), first_ok["body"].encode(), hashlib.sha256)
        assert first_ok["headers"]["x-semascope-signature"] == "sha256=" + expected.hexdigest()
        failed_first = [a for a in received(receiver, name) if a["status"] == 500]
        assert json.loads(failed_first[0]["body"])["id"] == added["id"]  # same event, retried
    finally:
        api.delete(f"/curation/webhooks/{hook['id']}")
    assert all(h["id"] != hook["id"] for h in api.get("/curation/webhooks").json())


def upload(api: httpx.Client, slug: str, name: str, text: str) -> str:
    resp = api.post(f"/collections/{slug}/upload", files=[("files", (name, text.encode()))])
    assert resp.status_code == 202, resp.text
    return str(resp.json()[0]["uri"])


def searchable(api: httpx.Client, slug: str, q: str, n: int) -> bool:
    body = {"q": q, "collection": slug, "facets": []}
    return bool(api.post("/search", json=body).json()["total"] == n)


def saved(api: httpx.Client, saved_id: int) -> dict[str, Any]:
    return next(s for s in api.get("/search/saved").json() if s["id"] == saved_id)


def test_saved_search_alert_fires_when_the_total_grows(api: httpx.Client) -> None:
    slug = "it-alerts"
    word = "wombat" + secrets.token_hex(3)
    ensure_collection(api, slug)
    upload(api, slug, f"{word}-1.txt", f"A {word} crossed the fire trail at dusk.")
    wait_for(lambda: searchable(api, slug, word, 1), timeout=300, what="the first upload")
    query = {"q": word, "collection": slug}
    alert = api.post("/search/saved", json={"name": f"alert {word}", "query": query, "alert": True})
    quiet = api.post("/search/saved", json={"name": f"quiet {word}", "query": query})
    assert alert.status_code == 201 and quiet.status_code == 201, (alert.text, quiet.text)
    alert_id, quiet_id = alert.json()["id"], quiet.json()["id"]
    try:
        # the first check records the total and fires nothing
        cursor = last_event_id(api)
        run_task("semascope.check_alerts")
        first = wait_for(
            lambda: (s := saved(api, alert_id))["last_checked_at"] and s,
            timeout=120,
            every=1.0,
            what="the first alert check",
        )
        assert first["last_total"] == 1, first
        subject = f"saved-search:{alert_id}"
        fired = api.get("/curation/events", params={"since_id": cursor, "kind": "alert."}).json()
        assert not [e for e in fired if e["subject"] == subject], fired

        uri = upload(api, slug, f"{word}-2.txt", f"Another {word} in the culvert.")
        wait_for(lambda: searchable(api, slug, word, 2), timeout=300, what="the second upload")
        run_task("semascope.check_alerts")

        def alert_event() -> dict[str, Any] | None:
            events = api.get(
                "/curation/events", params={"since_id": cursor, "kind": "alert.saved_search"}
            ).json()
            return next((e for e in events if e["subject"] == subject), None)

        event = wait_for(alert_event, timeout=120, every=1.0, what="the alert event")
        assert event["collection"] == slug
        # every reader of the collection and every webhook sees the outbox: the event names
        # the saved search, never its owner, its name or its hits (SEMA-048)
        assert event["payload"] == {"saved_search": alert_id, "new": 1, "total": 2}, event
        assert word not in str(event)
        # the owner reads the matches in the in-app alert list
        mine = next(a for a in api.get("/views/alerts").json() if a["id"] == alert_id)
        assert doc_id(slug, uri) in {h["id"] for h in mine["newest"]}
        assert saved(api, alert_id)["last_total"] == 2
        # a saved search without an alert is never checked
        assert saved(api, quiet_id)["last_checked_at"] is None
        events = api.get("/curation/events", params={"since_id": cursor, "kind": "alert."}).json()
        assert [e["subject"] for e in events if e["subject"] == subject] == [subject]
    finally:
        for sid in (alert_id, quiet_id):
            api.delete(f"/search/saved/{sid}")


@pytest.mark.parametrize("payload", [{"q": "x", "page_size": "many"}])
def test_saved_search_with_an_invalid_query_is_refused(
    api: httpx.Client, payload: dict[str, Any]
) -> None:
    resp = api.post("/search/saved", json={"name": "broken", "query": payload})
    assert resp.status_code == 422, resp.text
