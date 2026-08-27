from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
REPOSITORY_DIR = BASE_DIR.parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [value.strip() for value in os.getenv(name, default).split(",") if value.strip()]


def database_config() -> dict[str, object]:
    raw_url = os.getenv("SIGNAGE_DATABASE_URL", "")
    if not raw_url:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }

    parsed = urlparse(raw_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError("SIGNAGE_DATABASE_URL must use postgresql://")

    query = parse_qs(parsed.query)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": query.get("host", [parsed.hostname or ""])[0],
        "PORT": parsed.port or "",
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"connect_timeout": 5},
    }


SECRET_KEY = os.getenv("SIGNAGE_SECRET_KEY", "dev-only-change-me")
DEBUG = env_bool("SIGNAGE_DEBUG", False)
ALLOWED_HOSTS = env_list(
    "SIGNAGE_ALLOWED_HOSTS",
    "signage.vve.local,192.168.110.222,localhost,127.0.0.1,testserver",
)
CSRF_TRUSTED_ORIGINS = env_list("SIGNAGE_CSRF_TRUSTED_ORIGINS", "https://signage.vve.local")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"

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
                "core.context_processors.brand",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {"default": database_config()}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

LANGUAGE_CODE = "ru"
TIME_ZONE = os.getenv("SIGNAGE_TIME_ZONE", "Asia/Sakhalin")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = Path(os.getenv("SIGNAGE_STATIC_ROOT", "/srv/signage/static"))
STATICFILES_DIRS = [
    REPOSITORY_DIR / "apps" / "web" / "static",
    REPOSITORY_DIR / "apps" / "web" / "public",
]

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.getenv("SIGNAGE_MEDIA_ROOT", BASE_DIR / "media"))
SIGNAGE_UPLOAD_ROOT = Path(os.getenv("SIGNAGE_UPLOAD_ROOT", BASE_DIR / "uploads"))
SIGNAGE_SITE_SNAPSHOT_ROOT = Path(
    os.getenv("SIGNAGE_SITE_SNAPSHOT_ROOT", BASE_DIR / "site-snapshots")
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

SESSION_COOKIE_NAME = "signage_admin_session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

DATA_UPLOAD_MAX_MEMORY_SIZE = 16 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 4 * 1024 * 1024
