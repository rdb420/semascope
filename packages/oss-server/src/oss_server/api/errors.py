"""Error answers that tell the caller what went wrong without the server's internals.

An unexpected exception answers 500 with a reference that also appears in the
server log next to the traceback; store errors (Solr, Neo4j, the pack store) are
logged, never echoed, since they can name hosts, paths and queries.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from django.core.exceptions import (
    RequestDataTooBig,
    SuspiciousOperation,
    TooManyFieldsSent,
    TooManyFilesSent,
)
from django.http import HttpRequest, HttpResponse
from ninja.errors import HttpError

log = logging.getLogger("semascope.errors")


def reference() -> str:
    return secrets.token_hex(4)


def console_error(kind: str, exc: Exception) -> HttpError:
    """422 for a failed console query: the error's class (and Neo4j's code), with the full
    message only in the log under a reference."""

    ref = reference()
    log.warning("%s console query failed (reference %s): %s", kind, ref, exc)
    code = getattr(exc, "code", None)
    what = f"{type(exc).__name__}{f' {code}' if code else ''}"
    return HttpError(422, f"{kind} query failed ({what}); see the server log, reference {ref}")


def logged_detail(what: str, exc: Exception) -> str:
    """A short message for a partial failure reported inside a 200 answer."""

    ref = reference()
    log.warning("%s (reference %s): %s", what, ref, exc)
    return f"{what}; see the server log, reference {ref}"


def install(api: Any) -> None:
    """Answer unexpected exceptions with JSON 500 and a reference instead of a traceback."""

    def server_error(request: HttpRequest, exc: Exception) -> HttpResponse:
        ref = reference()
        log.exception("unhandled error on %s %s (reference %s)", request.method, request.path, ref)
        return api.create_response(  # type: ignore[no-any-return]
            request, {"detail": f"internal error; reference {ref}"}, status=500
        )

    def too_big(request: HttpRequest, exc: Exception) -> HttpResponse:
        return api.create_response(  # type: ignore[no-any-return]
            request, {"detail": "request too large"}, status=413
        )

    def suspicious(request: HttpRequest, exc: Exception) -> HttpResponse:
        log.warning("refused %s %s: %s", request.method, request.path, type(exc).__name__)
        return api.create_response(  # type: ignore[no-any-return]
            request, {"detail": "bad request"}, status=400
        )

    api.add_exception_handler(Exception, server_error)
    api.add_exception_handler(SuspiciousOperation, suspicious)
    api.add_exception_handler(RequestDataTooBig, too_big)
    api.add_exception_handler(TooManyFilesSent, too_big)
    api.add_exception_handler(TooManyFieldsSent, too_big)
