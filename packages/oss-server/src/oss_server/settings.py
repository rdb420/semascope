"""Django settings, built from the one Settings object every package shares."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

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
    AUTHENTICATION_BACKENDS = [
        "django.contrib.auth.backends.ModelBackend",
        "allauth.account.auth_backends.AuthenticationBackend",
    ]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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
        "USER": url.username,
        "PASSWORD": url.password,
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
STATIC_ROOT = _settings.semascope_data_dir / "static"
STATIC_ROOT.mkdir(parents=True, exist_ok=True)
STATICFILES_DIRS = [p for p in [BASE_DIR / "static"] if p.exists()]
WHITENOISE_ROOT = BASE_DIR / "static" / "web"  # built UI: /assets/*, /favicon.svg
WHITENOISE_INDEX_FILE = False
STORAGES = {
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

SEMASCOPE = _settings
