"""The stores on the compose network ask for their passwords."""

from __future__ import annotations

import pytest
from _stack import compose


def test_valkey_refuses_anonymous_clients() -> None:
    proc = compose("exec", "-T", "valkey", "valkey-cli", "ping", timeout=60)
    if proc.returncode != 0 and "no such service" in proc.stderr.lower():
        pytest.skip("no valkey service on this stack")
    assert "NOAUTH" in proc.stdout + proc.stderr, proc.stdout + proc.stderr
    auth = compose(
        "exec", "-T", "valkey", "sh", "-c", 'REDISCLI_AUTH="$VALKEY_PASSWORD" valkey-cli ping',
        timeout=60,
    )  # fmt: skip
    assert auth.stdout.strip() == "PONG", auth.stdout + auth.stderr
