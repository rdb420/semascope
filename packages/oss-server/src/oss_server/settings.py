"""Django settings, built from the one Settings object every package shares."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse, urlsplit, urlunsplit

import django_stubs_ext
from django.core.exceptions import ImproperlyConfigured

from oss_core.config import get_settings

django_stubs_ext.monkeypatch()
_settings = get_settings()
OIDC_ENABLED = bool(_settings.semascope_oidc_server_url and _settings.semascope_oidc_client_id)
BASE_DIR = Path(__file__).resolve().parent

DEBUG = _settings.semascope_debug
# Outside debug mode (settings_test turns it on) the server, worker and beat refuse to start
# with an empty or placeholder secret, rather than run with one anyone can read in the repo.
if not DEBUG and (_weak := _settings.insecure_secrets()):
    raise ImproperlyConfigured(
        f"refusing to start: {', '.join(_weak)} "
        f"{'is' if len(_weak) == 1 else 'are'} empty or a placeholder such as 'change-me'. "
        "Run `oss init` for a new install, or put generated values in .env and restart "
        "(see docs/operations.md#secrets)."
    )
SECRET_KEY = _settings.semascope_secret_key.get_secret_value()
ALLOWED_HOSTS = [_settings.semascope_host, "server", "localhost", "127.0.0.1"]


def csrf_trusted_origins(host: str, https_port: int) -> list[str]:
    """The browser origins Caddy serves: the host, with the port unless it is 443."""

    origins = [f"https://{host}"]
    if https_port != 443:
        origins.append(f"https://{host}:{https_port}")
    return origins


CSRF_TRUSTED_ORIGINS = csrf_trusted_origins(
    _settings.semascope_host, _settings.semascope_https_port
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "oss_server.catalogue",
    "oss_server.knowledge",
    "oss_server.curation",
    "oss_server.audit",
]

# The sign-in lockout comes first: it refuses a locked account or address before any
# backend checks the password (oss_server.ratelimit).
AUTHENTICATION_BACKENDS = [
    "oss_server.ratelimit.LoginLockoutBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Optional OpenID Connect through django-allauth; active only when an issuer is configured.
if OIDC_ENABLED:
    INSTALLED_APPS += [
        "allauth",
        "allauth.account",
        "allauth.socialaccount",
        "allauth.socialaccount.providers.openid_connect",
    ]
    SOCIALACCOUNT_PROVIDERS = {
        "openid_connect": {
            "APPS": [
                {
                    "provider_id": "oidc",
                    "name": _settings.semascope_oidc_name or "Single sign-on",
                    "client_id": _settings.semascope_oidc_client_id,
                    "secret": (
                        _settings.semascope_oidc_client_secret.get_secret_value()
                        if _settings.semascope_oidc_client_secret
                        else ""
                    ),
                    "settings": {"server_url": _settings.semascope_oidc_server_url},
                }
            ]
        }
    }
    # local sign-up is closed; a first single sign-on creates the account only when
    # SEMASCOPE_OIDC_AUTO_SIGNUP is true, with SEMASCOPE_OIDC_DEFAULT_ROLE (oss_server.signup)
    ACCOUNT_ADAPTER = "oss_server.signup.ClosedSignupAccountAdapter"
    SOCIALACCOUNT_ADAPTER = "oss_server.signup.SignOnAccountAdapter"
    SOCIALACCOUNT_AUTO_SIGNUP = True
    SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
    ACCOUNT_EMAIL_VERIFICATION = "none"
    LOGIN_REDIRECT_URL = "/"
    AUTHENTICATION_BACKENDS += ["allauth.account.auth_backends.AuthenticationBackend"]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "oss_server.audit.log.AuditContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "oss_server.security.SecurityHeadersMiddleware",
]
if OIDC_ENABLED:
    MIDDLEWARE.append("allauth.account.middleware.AccountMiddleware")

ROOT_URLCONF = "oss_server.urls"
WSGI_APPLICATION = "oss_server.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]


def _database() -> dict[str, object]:
    url = urlparse(_settings.database_url)
    if url.scheme.startswith("sqlite"):
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": url.path or ":memory:"}
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": url.path.lstrip("/"),
        "USER": unquote(url.username or ""),
        "PASSWORD": unquote(url.password or ""),
        "HOST": url.hostname,
        "PORT": url.port or 5432,
        "CONN_MAX_AGE": 60,
    }


DATABASES = {"default": _database()}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en-au"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = _settings.semascope_data_dir / "static"  # created by collectstatic (prepare)
STATICFILES_DIRS = [p for p in [BASE_DIR / "static"] if p.exists()]
WHITENOISE_ROOT = BASE_DIR / "static" / "web"  # built UI: /assets/*, /favicon.svg
WHITENOISE_INDEX_FILE = False
STORAGES = {
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}


def cache_url(valkey_url: str) -> str:
    """The Valkey database after the broker's (`/0` -> `/1`): the cache never shares a
    database with Celery's queues, so clearing it cannot drop a queued task."""

    parts = urlsplit(valkey_url)
    db = parts.path.strip("/")
    number = int(db) if db.isdigit() else 0
    return urlunsplit(parts._replace(path=f"/{(number + 1) % 16}"))


# Rate limits and the sign-in lockout count here (oss_server.ratelimit), as do allauth's
# own limits: Valkey, so every server process shares the counters, in a database of its
# own. Without a stack (an SQLite database: tests, a laptop) the counters stay in this
# process's memory.
CACHES: dict[str, dict[str, object]] = {
    "default": (
        {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
        if _settings.database_url.startswith("sqlite")
        else {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": cache_url(_settings.valkey_url),
            "KEY_PREFIX": "semascope",
            "TIMEOUT": 3600,
            "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
        }
    )
}

# Request bodies: JSON and forms up to SEMASCOPE_REQUEST_MAX_MB; uploaded files are checked
# one by one against SEMASCOPE_UPLOAD_MAX_MB (api/root.py) and spooled to disk above 2.5 MB.
# Caddy refuses larger requests before they reach the server (deploy/caddy/Caddyfile).
DATA_UPLOAD_MAX_MEMORY_SIZE = max(1, _settings.semascope_request_max_mb) * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000
DATA_UPLOAD_MAX_NUMBER_FILES = 100

SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"
# SecurityMiddleware sets nosniff, Referrer-Policy and Cross-Origin-Opener-Policy on every
# response; Caddy replaces rather than repeats them (deploy/caddy/Caddyfile)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    # URL passwords masked in every line, tracebacks included (oss_server.logredact)
    "formatters": {"redacted": {"()": "oss_server.logredact.RedactingFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "redacted"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

SEMASCOPE = _settings
