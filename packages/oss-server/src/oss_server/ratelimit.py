"""Request rate limits: the sign-in lockout and per-user limits on expensive API routes.

Both count in Django's cache, which is Valkey in a deployment (`settings.CACHES`), so
every server process shares the counters; the unit tests use the local-memory cache.
When the cache cannot be reached the limits fail open and log a warning: an outage of
Valkey must not lock everyone out.

**Sign-in lockout.** Every failed sign-in (the API, the Django admin, allauth: anything
that calls `django.contrib.auth.authenticate`) counts against the account name and the
client address. Once an account has `SEMASCOPE_LOGIN_MAX_FAILURES` failures, or an
address `SEMASCOPE_LOGIN_MAX_FAILURES_PER_IP`, within `SEMASCOPE_LOGIN_LOCKOUT_S`
seconds, further attempts are refused, with the right password too, until that window
has passed since the first failure. `LoginLockoutBackend` (first in
`AUTHENTICATION_BACKENDS`) refuses them; the API answers 429 with Retry-After. A
successful sign-in clears the account's count, never the address's.

**API limits.** `RATE_LIMITED_ROUTES` names the expensive routes and the scope each
counts in; `apply_rate_limits` attaches one django-ninja throttle per scope to those
routes. Each signed-in user (or bearer token: each token counts on its own) gets
`DEFAULT_RATES[scope]` requests per period, changed with `SEMASCOPE_RATE_LIMITS`
(`export=10/h,console=5/m`, or `off`). Anonymous callers (the public Reconciliation
API) count per client address. A refused request answers 429 with Retry-After.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from django.contrib.auth.backends import BaseBackend, ModelBackend
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from ninja.throttling import SimpleRateThrottle

from oss_core.config import get_settings

log = logging.getLogger(__name__)

# --- client address --------------------------------------------------------------------------


def client_ip(request: HttpRequest | None) -> str:
    """The client's address: the one the outermost trusted proxy saw.

    Caddy replaces any X-Forwarded-For a client sends with the address it saw (it trusts
    no proxy in front of it), so with `SEMASCOPE_TRUSTED_PROXIES=1` the last entry is
    the client. With 0 (no proxy) the socket address is used and the header ignored.
    """

    if request is None:
        return ""
    remote = str(request.META.get("REMOTE_ADDR") or "")
    proxies = max(0, get_settings().semascope_trusted_proxies)
    forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR") or "")
    if proxies == 0 or not forwarded:
        return remote
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    if not hops:
        return remote
    return hops[-min(proxies, len(hops))][:64]


# --- sign-in lockout -------------------------------------------------------------------------


def _account_key(username: str) -> str:
    digest = hashlib.sha256(username.strip().lower().encode()).hexdigest()[:32]
    return f"login:account:{digest}"


def _ip_key(ip: str) -> str:
    return f"login:ip:{ip}"


def _failures(key: str) -> tuple[int, float]:
    """(failures, unix time of the first one) under `key`; (0, 0) when none."""

    value = cache.get(key)
    if isinstance(value, tuple | list) and len(value) == 2:
        return int(value[0]), float(value[1])
    return 0, 0.0


def _count_failure(key: str, window: int) -> None:
    count, first = _failures(key)
    now = time.time()
    if not count or now - first >= window:
        count, first = 0, now
    remaining = max(1, int(first + window - now))
    cache.set(key, (count + 1, first), remaining)


def lockout_remaining(request: HttpRequest | None, username: str) -> int:
    """Seconds until a sign-in for `username` from this client is allowed again (0: now)."""

    s = get_settings()
    window = max(1, s.semascope_login_lockout_s)
    checks = [(_account_key(username), s.semascope_login_max_failures)] if username else []
    ip = client_ip(request)
    if ip:
        checks.append((_ip_key(ip), s.semascope_login_max_failures_per_ip))
    wait = 0
    try:
        for key, limit in checks:
            if limit <= 0:
                continue
            count, first = _failures(key)
            if count >= limit:
                wait = max(wait, int(first + window - time.time()) + 1)
    except Exception:  # noqa: BLE001 - fail open: a cache outage must not lock everyone out
        log.warning("sign-in lockout unavailable (cache unreachable); allowing the attempt")
        return 0
    return max(0, wait)


def record_login_failure(request: HttpRequest | None, username: str) -> None:
    window = max(1, get_settings().semascope_login_lockout_s)
    try:
        if username:
            _count_failure(_account_key(username), window)
        ip = client_ip(request)
        if ip:
            _count_failure(_ip_key(ip), window)
    except Exception:  # noqa: BLE001
        log.warning("could not count a failed sign-in (cache unreachable)")


def clear_login_failures(username: str) -> None:
    try:
        cache.delete(_account_key(username))
    except Exception:  # noqa: BLE001
        log.warning("could not clear the failed sign-ins of an account (cache unreachable)")


class LoginLockoutBackend(BaseBackend):
    """First authentication backend: refuses every sign-in of a locked account or address.

    Raising PermissionDenied stops `authenticate` from trying the other backends, so a
    correct password does not get through either; the attempt still counts as failed.
    """

    def authenticate(self, request: Any, username: str | None = None, **kwargs: Any) -> None:
        name = username or kwargs.get("email") or kwargs.get("login") or ""
        if lockout_remaining(request, str(name)):
            if request is not None:
                request._semascope_login_locked = True  # the audit entry says why
            raise PermissionDenied("too many failed sign-ins")
        return None

    def get_user(self, user_id: Any) -> Any:
        # a session started with this backend (force_login uses the first one) loads its
        # user the way ModelBackend does
        return ModelBackend().get_user(user_id)


def login_failed(sender: Any, credentials: dict[str, Any], request: Any = None, **_: Any) -> None:
    """`user_login_failed` receiver: count the failure against the account and the address."""

    name = credentials.get("username") or credentials.get("email") or credentials.get("login")
    record_login_failure(request, str(name or ""))


def logged_in(sender: Any, request: Any, user: Any, **_: Any) -> None:
    """`user_logged_in` receiver: a successful sign-in clears the account's failures."""

    clear_login_failures(user.get_username())


# --- API rate limits -------------------------------------------------------------------------

DEFAULT_RATES: dict[str, str] = {
    # searches and the views computed from one (the evaluation runner, feeds and the UI)
    "search": "600/m",
    # CSV, JSON-LD and Turtle exports of a search, delta exports
    "export": "60/h",
    # list search: up to 500 searches in one request
    "list": "60/h",
    # graph neighbourhoods, paths and hubs
    "graph": "240/m",
    # the staff Cypher and SPARQL consoles
    "console": "60/m",
    # work queued for the workers: bulk tagging, reindexing, source runs, pack imports
    "jobs": "120/h",
    # uploaded files (each request may carry several)
    "upload": "600/h",
    # the Reconciliation API: per user, or per client address when it is public
    "reconcile": "600/m",
}

RATE_LIMITED_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/search"): "search",
    ("POST", "/api/search/tag-query"): "search",
    ("POST", "/api/search/terms"): "search",
    ("POST", "/api/views/table/stats"): "search",
    ("POST", "/api/views/entities"): "search",
    ("GET", "/api/documents/{doc_id}/similar"): "search",
    ("GET", "/api/analytics/overview"): "search",
    ("POST", "/api/analytics/clusters"): "search",
    ("POST", "/api/analytics/compare"): "search",
    ("POST", "/api/analytics/wordtree"): "search",
    ("GET", "/api/analytics/diff"): "search",
    ("POST", "/api/explain"): "search",
    ("GET", "/api/synonyms/suggest"): "search",
    ("GET", "/api/feeds/saved/{saved_id}"): "search",
    ("GET", "/api/feeds/search"): "search",
    ("GET", "/api/feeds/alerts"): "search",
    ("POST", "/api/search/export.csv"): "export",
    ("POST", "/api/export/search"): "export",
    ("POST", "/api/export/documents"): "export",
    ("POST", "/api/export/entities"): "export",
    ("GET", "/api/export/graph"): "export",
    ("GET", "/api/knowledge/packs/{pack_id}/deltas/export"): "export",
    ("POST", "/api/knowledge/packs/{pack_id}/deltas/export"): "export",
    ("POST", "/api/search/list"): "list",
    ("GET", "/api/graph/neighbours"): "graph",
    ("GET", "/api/graph/path"): "graph",
    ("GET", "/api/graph/shared"): "graph",
    ("POST", "/api/views/graph"): "graph",
    ("POST", "/api/views/graph/edge"): "graph",
    ("POST", "/api/graph/cypher"): "console",
    ("POST", "/api/knowledge/packs/{pack_id}/sparql"): "console",
    ("POST", "/api/curation/bulk-tags"): "jobs",
    ("POST", "/api/collections/{slug}/reindex"): "jobs",
    ("POST", "/api/documents/{doc_id}/reindex"): "jobs",
    ("POST", "/api/sources/{source_id}/run"): "jobs",
    ("POST", "/api/knowledge/packs/import"): "jobs",
    ("POST", "/api/knowledge/packs/import-list"): "jobs",
    ("POST", "/api/knowledge/packs/{pack_id}/activate"): "jobs",
    ("POST", "/api/knowledge/packs/diff"): "jobs",
    ("POST", "/api/annotations/w3c/import"): "jobs",
    ("POST", "/api/collections/{slug}/upload"): "upload",
    ("GET", "/api/reconcile"): "reconcile",
    ("POST", "/api/reconcile"): "reconcile",
    ("GET", "/api/reconcile/preview"): "reconcile",
}


def configured_rates(raw: str | None = None) -> dict[str, str | None]:
    """The rate of each scope: the defaults with `SEMASCOPE_RATE_LIMITS` applied.

    `off` (or `none`) turns every limit off; `scope=off` one of them. An unknown scope
    or a malformed rate is logged and ignored, never fatal.
    """

    text = (get_settings().semascope_rate_limits if raw is None else raw).strip()
    rates: dict[str, str | None] = dict(DEFAULT_RATES)
    if text.lower() in ("off", "none", "0", "false"):
        return dict.fromkeys(rates)
    for part in filter(None, (p.strip() for p in text.split(","))):
        scope, _, rate = part.partition("=")
        scope, rate = scope.strip().lower(), rate.strip().lower()
        if scope not in rates:
            log.warning("SEMASCOPE_RATE_LIMITS: unknown scope %r ignored", scope)
            continue
        if rate in ("off", "none", "0"):
            rates[scope] = None
            continue
        try:
            SimpleRateThrottle(rate)
        except ValueError:
            log.warning("SEMASCOPE_RATE_LIMITS: %s=%r is not count/period; ignored", scope, rate)
            continue
        rates[scope] = rate
    return rates


class ScopedThrottle(SimpleRateThrottle):
    """django-ninja's throttle with the rate read from the settings on every request.

    The key is the bearer token when the request came with one, else the user, else
    (anonymous) the client address. Fails open when the cache is unreachable.
    """

    cache_format = "ratelimit:%(scope)s:%(ident)s"

    def __init__(self, scope: str) -> None:
        self.scope = scope
        super().__init__(rate=DEFAULT_RATES[scope])

    def get_cache_key(self, request: HttpRequest) -> str | None:
        token_id = getattr(request, "api_token_id", None)
        user = getattr(request, "user", None)
        if token_id is not None:
            ident = f"token-{token_id}"
        elif user is not None and getattr(user, "is_authenticated", False):
            ident = f"user-{user.pk}"
        else:
            ident = f"ip-{client_ip(request)}"
        return self.cache_format % {"scope": self.scope, "ident": ident}

    def allow_request(self, request: HttpRequest) -> bool:
        rate = configured_rates().get(str(self.scope))
        if rate is None:
            return True
        self.rate = rate
        self.num_requests, self.duration = self.parse_rate(rate)
        try:
            return super().allow_request(request)
        except Exception:  # noqa: BLE001 - fail open when Valkey is unreachable
            log.warning("rate limit %s unavailable (cache unreachable); allowing", self.scope)
            return True


def apply_rate_limits(api: Any) -> set[tuple[str, str]]:
    """Attach a scope's throttle to each route of `RATE_LIMITED_ROUTES`; return those found.

    Works on the bound (served) operations, so it runs once every router is mounted.
    """

    throttles = {scope: ScopedThrottle(scope) for scope in DEFAULT_RATES}
    found: set[tuple[str, str]] = set()
    for bound in api._get_bound_routers():  # noqa: SLF001 - the served operations
        for path, view in bound.path_operations.items():
            for op in view.operations:
                for method in op.methods:
                    route = (method, "/api" + bound.prefix + path)
                    scope = RATE_LIMITED_ROUTES.get(route)
                    if scope is None:
                        continue
                    op.throttle_objects = [*op.throttle_objects, throttles[scope]]
                    found.add(route)
    missing = set(RATE_LIMITED_ROUTES) - found
    if missing:
        raise RuntimeError(f"rate-limited routes that do not exist: {sorted(missing)}")
    return found
