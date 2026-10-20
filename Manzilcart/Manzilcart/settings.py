"""
Django settings for ManzilCart - a C2C marketplace for pre-owned and refurbished furniture
in the United Arab Emirates (launching in Abu Dhabi and Ras Al Khaimah).

Design notes
------------
* All environment specific values are read from environment variables (see `.env.example`).
* The same code base serves development, staging and production; behaviour is switched
  with `DJANGO_ENV` and a handful of flags instead of forking settings modules.
* Business/tunable values (OTP limits, auction windows, page sizes) live in the
  "ManzilCart configuration" section at the bottom so they are easy to find and change.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# The .env file is optional and must never be committed.
load_dotenv(BASE_DIR / ".env")


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------
def env(name: str, default: str | None = None) -> str | None:
    """Return a trimmed environment variable, or ``default`` when unset/blank."""
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value != "" else default


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:  # pragma: no cover - misconfiguration
        raise ImproperlyConfigured(f"{name} must be an integer, got {raw!r}.") from exc


def env_list(name: str, default: tuple[str, ...] = ()) -> list[str]:
    raw = env(name)
    if raw is None:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


ENVIRONMENT = (env("DJANGO_ENV", "development") or "development").lower()
IS_PRODUCTION = ENVIRONMENT == "production"
IS_TESTING = ENVIRONMENT in {"testing", "test"}

DEBUG = env_bool("DJANGO_DEBUG", default=not IS_PRODUCTION)

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be set when DJANGO_ENV=production."
        )
    SECRET_KEY = "dev-only-insecure-key-never-use-in-production-0000000000000"

ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    ("localhost", "127.0.0.1", "[::1]", "testserver") if not IS_PRODUCTION else (),
)
if IS_PRODUCTION and not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS must be set when DJANGO_ENV=production."
    )

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Cookies / sessions.
SESSION_COOKIE_NAME = "manzilcart_session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 60 * 60 * 24 * 14)
SESSION_SAVE_EVERY_REQUEST = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False  # Must stay readable by our own JS for AJAX posts.

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

if IS_PRODUCTION:
    # HTTPS and transport hardening. Assumes TLS terminates at the proxy/load balancer.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 60 * 60 * 24 * 365)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    # Daphne must come first so it can take over the `runserver` command.
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "channels",
    # ManzilCart apps - a modular monolith, ordered roughly by dependency.
    "core",
    "locations",
    "accounts",
    "catalog",
    "moderation",
    "conversations",
    "deals",
    "auctions",
    "notifications",
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
    # Resolves the marketplace (emirate) for the current request.
    "core.middleware.MarketplaceMiddleware",
]

ROOT_URLCONF = "Manzilcart.urls"

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
                "core.context_processors.marketplace",
            ],
        },
    },
]

WSGI_APPLICATION = "Manzilcart.wsgi.application"
ASGI_APPLICATION = "Manzilcart.asgi.application"

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_ENGINE = (env("DB_ENGINE", "postgresql") or "postgresql").lower()

if DB_ENGINE in {"postgres", "postgresql"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", "manzilsample"),
            "USER": env("DB_USER", "postgres"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "localhost"),
            "PORT": env("DB_PORT", "5432"),
            "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 0 if not IS_PRODUCTION else 60),
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {"connect_timeout": env_int("DB_CONNECT_TIMEOUT", 5)},
        }
    }
elif DB_ENGINE == "sqlite":
    # Convenience fallback (lightweight local runs / CI without PostgreSQL).
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": env("DB_NAME", str(BASE_DIR / "db.sqlite3")),
        }
    }
else:  # pragma: no cover - misconfiguration
    raise ImproperlyConfigured(f"Unsupported DB_ENGINE: {DB_ENGINE!r}")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# Cache and channel layer (Redis in production, in-process for development)
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL")

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": env("CACHE_KEY_PREFIX", "manzilcart"),
        }
    }
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [REDIS_URL]},
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "manzilcart-locmem",
            "KEY_PREFIX": env("CACHE_KEY_PREFIX", "manzilcart"),
        }
    }
    # In-memory layer is only correct for single-process development / tests.
    CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    }


# ---------------------------------------------------------------------------
# Email (Django 6.1 "MAILERS" API - aliases replace a single EMAIL_BACKEND)
# ---------------------------------------------------------------------------
EMAIL_SUBJECT_PREFIX = env("EMAIL_SUBJECT_PREFIX", "[ManzilCart] ")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "ManzilCart <no-reply@manzilcart.local>")
SERVER_EMAIL = env("SERVER_EMAIL", DEFAULT_FROM_EMAIL)

if IS_PRODUCTION and env("EMAIL_HOST"):
    _smtp_options = {
        "HOST": env("EMAIL_HOST"),
        "PORT": env_int("EMAIL_PORT", 587),
        "USER": env("EMAIL_HOST_USER"),
        "PASSWORD": env("EMAIL_HOST_PASSWORD"),
        "USE_TLS": env_bool("EMAIL_USE_TLS", True),
        "TIMEOUT": env_int("EMAIL_TIMEOUT", 10),
    }
    MAILERS = {
        # Transactional account / deal / auction mail.
        "default": {
            "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "OPTIONS": dict(_smtp_options),
        },
        # Operations team alerts (kept separate so deliverability and credentials
        # can be changed without touching member-facing mail).
        "ops": {
            "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "OPTIONS": dict(_smtp_options),
        },
    }
else:
    # Development / test: print mail to the console. Django's test runner replaces
    # every alias with the locmem backend, so `mail.outbox` works in tests.
    MAILERS = {
        "default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"},
        "ops": {"BACKEND": "django.core.mail.backends.console.EmailBackend"},
    }

ADMINS = [("ManzilCart Operations", addr) for addr in env_list("ADMINS_EMAILS")]


# ---------------------------------------------------------------------------
# Static and media files
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))

if IS_PRODUCTION:
    # Manifest + compression keeps a small VM cheap: Whitenoise serves the hashed files.
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        },
    }
else:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }

WHITENOISE_MAX_AGE = env_int("WHITENOISE_MAX_AGE", 60 * 60 * 24 * 365)

# Reject oversized uploads before they are written to disk.
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024


# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "Asia/Dubai")
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Authentication redirects
# ---------------------------------------------------------------------------
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:dashboard"
LOGOUT_REDIRECT_URL = "locations:landing"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = env("DJANGO_LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        # ManzilCart application loggers.
        "manzilcart": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}
# NOTE: never log OTP codes, session tokens or full phone numbers. Application code
# is responsible for redaction; see core/logging_utils.py for helpers.


# ===========================================================================
# ManzilCart configuration
# ===========================================================================

# --- Marketplace / emirate behaviour ---------------------------------------
# Session key that remembers the visitor's selected emirate.
EMIRATE_SESSION_KEY = "manzilcart.emirate_slug"
# URL segments that must never be interpreted as an emirate slug.
EMIRATE_RESERVED_SLUGS = [
    "admin",
    "accounts",
    "my",
    "api",
    "static",
    "media",
    "healthz",
    "favicon.ico",
    "robots.txt",
    "sitemap.xml",
]
LISTINGS_PER_PAGE = env_int("LISTINGS_PER_PAGE", 24)

# --- Uploads ---------------------------------------------------------------
MAX_IMAGE_UPLOAD_SIZE = env_int("MAX_IMAGE_UPLOAD_SIZE", 5 * 1024 * 1024)
ALLOWED_IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]
ALLOWED_IMAGE_MIME_TYPES = ["image/jpeg", "image/png", "image/webp"]
MAX_IMAGES_PER_LISTING = env_int("MAX_IMAGES_PER_LISTING", 10)

# --- Mobile verification (WhatsApp OTP) ------------------------------------
OTP_LENGTH = env_int("OTP_LENGTH", 6)
OTP_TTL_SECONDS = env_int("OTP_TTL_SECONDS", 300)
OTP_MAX_ATTEMPTS = env_int("OTP_MAX_ATTEMPTS", 5)
OTP_RESEND_COOLDOWN_SECONDS = env_int("OTP_RESEND_COOLDOWN_SECONDS", 60)
OTP_MAX_SENDS_PER_PHONE_PER_HOUR = env_int("OTP_MAX_SENDS_PER_PHONE_PER_HOUR", 5)
OTP_MAX_SENDS_PER_IP_PER_HOUR = env_int("OTP_MAX_SENDS_PER_IP_PER_HOUR", 20)

# --- Notifications ---------------------------------------------------------
# "mock" records notifications without contacting any provider (development /
# staging). "live" uses the real WhatsApp / SMS / SES providers (not enabled yet).
NOTIFICATIONS_MODE = env("NOTIFICATIONS_MODE", "mock").lower()
NOTIFICATION_MAX_ATTEMPTS = env_int("NOTIFICATION_MAX_ATTEMPTS", 5)
NOTIFICATION_BACKOFF_BASE_SECONDS = env_int("NOTIFICATION_BACKOFF_BASE_SECONDS", 60)
NOTIFICATION_BATCH_SIZE = env_int("NOTIFICATION_BATCH_SIZE", 50)
OPS_TEAM_EMAIL = env("OPS_TEAM_EMAIL", "operations@manzilcart.local")
# Absolute base used in emailed confirmation links (no trailing slash).
SITE_URL = env("SITE_URL", "http://127.0.0.1:8000").rstrip("/")
SMS_SENDER_ID = env("SMS_SENDER_ID", "ManzilCart")
WHATSAPP_OTP_TEMPLATE = env("WHATSAPP_OTP_TEMPLATE", "manzilcart_otp")

# --- Auctions --------------------------------------------------------------
# Confirmation window offered to a bidder the seller has locked in.
AUCTION_CONFIRMATION_WINDOW_HOURS = env_int("AUCTION_CONFIRMATION_WINDOW_HOURS", 6)
AUCTION_MIN_DURATION_HOURS = env_int("AUCTION_MIN_DURATION_HOURS", 1)
AUCTION_MAX_DURATION_DAYS = env_int("AUCTION_MAX_DURATION_DAYS", 14)
# A new bid must be at least this much higher than the highest valid bid.
AUCTION_MIN_BID_INCREMENT = env_int("AUCTION_MIN_BID_INCREMENT", 1)
AUCTION_MAX_BIDS_PER_USER = env_int("AUCTION_MAX_BIDS_PER_USER", 50)
AUCTIONS_PER_PAGE = env_int("AUCTIONS_PER_PAGE", 20)

# --- Deals and chat --------------------------------------------------------
MESSAGES_PER_PAGE = env_int("MESSAGES_PER_PAGE", 50)
CHAT_MESSAGE_MAX_LENGTH = env_int("CHAT_MESSAGE_MAX_LENGTH", 2000)
# Burst protection for chat; the durable record itself is the Message table.
CHAT_MESSAGES_PER_MINUTE = env_int("CHAT_MESSAGES_PER_MINUTE", 30)
OFFER_HISTORY_PER_PAGE = env_int("OFFER_HISTORY_PER_PAGE", 25)
