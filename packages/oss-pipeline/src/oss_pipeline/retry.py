"""Which failures are worth retrying, and how long to wait.

Transient: the service did not answer or said "try later" (connection
refused or reset, timeouts, HTTP 408, 425, 429, 500, 502, 503 and 504, Neo4j
unavailable or a transient Neo4j error). Permanent: everything else, such as
a format Tika cannot parse (422), an oversized file, a missing setting, an
unknown provider or a MinerU job that failed or ran out of time. Only
transient failures are retried, and always against the same provider: a
remote stage never falls back to an in-process CPU model, because a mixed
index is worse than a late one.
"""

from __future__ import annotations

import random

import httpx

TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def _neo4j_transient(exc: BaseException) -> bool:
    try:
        from neo4j import exceptions as neo
    except ImportError:  # pragma: no cover - neo4j ships with the pipeline
        return False
    return isinstance(exc, neo.ServiceUnavailable | neo.SessionExpired | neo.TransientError)


def _one(exc: BaseException) -> bool:
    marked = getattr(exc, "transient", None)
    if isinstance(marked, bool):
        return marked
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in TRANSIENT_STATUS
    if isinstance(exc, httpx.TransportError):
        return not isinstance(exc, httpx.UnsupportedProtocol)
    if isinstance(exc, ConnectionError | TimeoutError):
        return True
    return _neo4j_transient(exc)


def is_transient(exc: BaseException) -> bool:
    """Whether a failure is worth retrying, looking through `raise ... from` chains."""

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if _one(current):
            return True
        current = current.__cause__
    return False


def backoff(attempt: int, base: float, *, factor: float = 2.0, jitter: float = 0.25) -> float:
    """Seconds to wait before retry number `attempt` (1-based): base, base*factor, ... ± jitter."""

    delay = base * factor ** (attempt - 1)
    return float(delay * (1 + random.uniform(-jitter, jitter)))  # noqa: S311 - not crypto
