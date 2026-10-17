"""Log output with credentials masked: the password in any URL, in messages and tracebacks.

An HTTP client's error names the URL it failed on, and a source's URL can carry a user
and password (`https://crawler:pw@intranet/`), so a failed fetch would print the
password in the worker's log. `RedactingFormatter` wraps a handler's formatter and
masks the final text, traceback included. The server's LOGGING uses it; the Celery
worker and beat wrap their handlers when Celery sets up logging (`oss_server.celery`),
and `redact_handlers` covers handlers set up by `logging.basicConfig`.
"""

from __future__ import annotations

import logging
from typing import Any

from oss_server.redact import mask_text


class RedactingFormatter(logging.Formatter):
    def __init__(self, *args: Any, inner: logging.Formatter | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.inner = inner

    def format(self, record: logging.LogRecord) -> str:
        text = self.inner.format(record) if self.inner is not None else super().format(record)
        return mask_text(text)


def redact_handlers(logger: logging.Logger | None = None) -> None:
    """Wrap the formatter of every handler of `logger` (the root logger by default)."""

    target = logger or logging.getLogger()
    for handler in target.handlers:
        if not isinstance(handler.formatter, RedactingFormatter):
            handler.setFormatter(RedactingFormatter(inner=handler.formatter or logging.Formatter()))
