"""Webhook deliveries go through the pinned, capped client: DNS rebinding, redirects, caps.

Uses the pipeline tests' outbound doubles (a local origin, a resolver that changes its
answer); see packages/oss-pipeline/tests/test_outbound_guard.py for the other client types.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from oss_server.curation.models import Webhook
from oss_server.curation.services import emit
from oss_server.pipeline import tasks

FAKES = Path(__file__).resolve().parents[2] / "oss-pipeline" / "tests" / "outbound_fakes.py"


def _fakes() -> ModuleType:
    spec = importlib.util.spec_from_file_location("outbound_fakes", FAKES)
    assert spec and spec.loader
    module = sys.modules.get("outbound_fakes") or importlib.util.module_from_spec(spec)
    if "outbound_fakes" not in sys.modules:
        sys.modules["outbound_fakes"] = module
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def origin() -> Iterator[Any]:
    with _fakes().origin_server() as server:
        yield server


@pytest.fixture(autouse=True)
def _fresh(origin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    origin.reset()
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "")


@pytest.mark.parametrize("public_answers", [0, 1, 2, 3])
def test_a_webhook_host_that_rebinds_to_a_private_address_is_never_reached(
    public_answers: int, origin: Any, db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    dns = _fakes().Rebinding(monkeypatch, public_answers)
    hook = Webhook.objects.create(url=f"{origin.url('rebind.test')}/hook")
    emit("proposal.add_tag", collection="c", subject="s")

    assert tasks.deliver_webhooks() == 0
    assert origin.hits == []
    assert not dns.reached_private()
    hook.refresh_from_db()
    assert hook.last_error and hook.last_event_id == 0


def test_an_allowed_webhook_host_is_reached_through_the_pinned_address(
    origin: Any, db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "allowed.test")
    dns = _fakes().Rebinding(monkeypatch, 0)
    Webhook.objects.create(url=f"{origin.url('allowed.test')}/hook")
    emit("proposal.add_tag", collection="c", subject="s")

    assert tasks.deliver_webhooks() == 1
    assert origin.hits == ["/hook"] and dns.connects == ["127.0.0.1"]
    assert origin.hosts == [f"allowed.test:{origin.port}"]


@pytest.mark.parametrize("mode", ["/r", "/big", "/stream"])
def test_webhook_redirects_are_not_followed_and_answers_are_capped(
    mode: str, origin: Any, db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "allowed.test")
    _fakes().Rebinding(monkeypatch, 0)
    hook = Webhook.objects.create(url=f"{origin.url('allowed.test', mode)}/hook")
    event = emit("proposal.add_tag", collection="c", subject="s")

    assert tasks.deliver_webhooks() == 0
    assert origin.hits == [f"{mode}/hook"]  # /secret is never asked for
    assert origin.sent < 32 * 1024 * 1024
    hook.refresh_from_db()
    expected = "(not followed)" if mode == "/r" else "answer over 64 KiB"
    assert expected in hook.last_error and str(event.pk) in hook.last_error
