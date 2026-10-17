"""Credential masking for everything the server shows or writes down.

Source settings, collection settings, webhook addresses, error texts, audit entries
and the admin all pass through these helpers, so a credential that was written once
(an S3 secret key, a password in a URL, an Authorization header) is never read back
through the API, the admin or a log line.

A value is masked when its key names a credential (`SECRET_WORDS`), when the
connector declares the key as a credential (`credential_settings` on the connector
class), or when it is the password part of a URL (`https://user:pass@host`).
Account identifiers (an S3 access key id, a user name) are masked too unless the
caller may manage the source (`identifiers=True`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MASK = "********"

SECRET_WORDS = (
    "secret",
    "password",
    "passwd",
    "passphrase",
    "token",
    "api_key",
    "apikey",
    "credential",
    "authorization",
    "private_key",
    "privatekey",
    "cookie",
    "signature",
    "session",
    "bearer",
)
# account identifiers: shown to editors, who manage sources, masked for everyone else
IDENTIFIER_WORDS = ("access_key", "accesskey", "username", "user_name", "login")

# a URL with a user part: scheme://user:password@host
_URL_PASSWORD = re.compile(r"(?P<pre>\b[a-z][a-z0-9+.-]*://[^/\s:@]*:)(?P<pw>[^@/\s]+)(?=@)", re.I)


def is_secret_key(key: str, extra: Iterable[str] = ()) -> bool:
    name = key.lower()
    return any(word in name for word in SECRET_WORDS) or name in {e.lower() for e in extra}


def is_identifier_key(key: str) -> bool:
    name = key.lower()
    return any(word in name for word in IDENTIFIER_WORDS)


def mask_text(text: str) -> str:
    """`text` with the password of every URL in it masked (`https://u:********@h`)."""

    if "://" not in text:
        return text
    return _URL_PASSWORD.sub(lambda m: m.group("pre") + MASK, text)


def mask_url(url: str) -> str:
    """A URL with its password, and the values of query parameters that name a credential
    (`?token=`, `?sig=`, `?key=`), masked."""

    url = mask_text(url)
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    query = [
        (k, MASK if is_secret_key(k, ("key", "sig", "code", "auth")) and v else v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(parts._replace(query=urlencode(query, safe="*")))


def redact(
    value: Any, *, identifiers: bool = False, credentials: Iterable[str] = (), depth: int = 0
) -> Any:
    """`value` (a settings dict, a list, a string) with every credential masked.

    `identifiers=True` leaves account identifiers readable (for editors); `credentials`
    names further keys to mask (a connector's declared credential settings).
    """

    extra = tuple(credentials)
    if depth > 20:
        return MASK
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            hidden = is_secret_key(name, extra) or (not identifiers and is_identifier_key(name))
            # numbers and switches (`max_tokens`, `use_session`) are never credentials
            if (
                hidden
                and item not in (None, "", [], {})
                and not isinstance(item, bool | int | float)
            ):
                out[name] = MASK
            else:
                out[name] = redact(
                    item, identifiers=identifiers, credentials=extra, depth=depth + 1
                )
        return out
    if isinstance(value, list | tuple):
        return [
            redact(v, identifiers=identifiers, credentials=extra, depth=depth + 1) for v in value
        ]
    if isinstance(value, str):
        return mask_text(value)
    return value


def connector_credentials(connector: str) -> tuple[str, ...]:
    """The setting keys a connector declares as credentials (`credential_settings`)."""

    try:
        from oss_pipeline.registry import connectors

        cls = connectors().get(connector)
    except Exception:  # noqa: BLE001 - masking by key name still applies
        return ()
    return tuple(getattr(cls, "credential_settings", ()) or ())


def restore_masked(new: Any, old: Any) -> Any:
    """Settings submitted back with masks in them (the admin form): every masked value
    replaced by the stored one, so editing a source never overwrites its secret."""

    if new == MASK:
        return old
    if isinstance(new, dict) and isinstance(old, dict):
        return {k: restore_masked(v, old.get(k)) for k, v in new.items()}
    if isinstance(new, list) and isinstance(old, list) and len(new) == len(old):
        return [restore_masked(n, o) for n, o in zip(new, old, strict=True)]
    if isinstance(new, str) and isinstance(old, str) and MASK in new and new == mask_text(old):
        return old
    return new
