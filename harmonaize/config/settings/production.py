# ruff: noqa: E501
import logging

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.redis import RedisIntegration

from .base import *  # noqa: F403
from .base import DATABASES
from .base import INSTALLED_APPS
from .base import MIDDLEWARE
from .base import REDIS_URL
from .base import SPECTACULAR_SETTINGS
from .base import env

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#secret-key
SECRET_KEY = env("DJANGO_SECRET_KEY")
# https://docs.djangoproject.com/en/dev/ref/settings/#allowed-hosts
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["harmonaize.org"])
FORCE_SCRIPT_NAME = env("DJANGO_FORCE_SCRIPT_NAME", default="")
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=["https://harmonaize.uk"])

# DATABASES
# ------------------------------------------------------------------------------
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)

# CACHES
# ------------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            # Mimicking memcache behaviour.
            # https://github.com/jazzband/django-redis#memcached-exceptions-behavior
            "IGNORE_EXCEPTIONS": True,
        },
    },
}

# SECURITY
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#secure-proxy-ssl-header
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# https://docs.djangoproject.com/en/dev/ref/settings/#secure-ssl-redirect
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
# https://docs.djangoproject.com/en/dev/ref/settings/#session-cookie-secure
SESSION_COOKIE_SECURE = True
# https://docs.djangoproject.com/en/dev/ref/settings/#session-cookie-name
SESSION_COOKIE_NAME = "__Secure-sessionid"
# https://docs.djangoproject.com/en/dev/ref/settings/#csrf-cookie-secure
CSRF_COOKIE_SECURE = True
# https://docs.djangoproject.com/en/dev/ref/settings/#csrf-cookie-name
CSRF_COOKIE_NAME = "__Secure-csrftoken"
# https://docs.djangoproject.com/en/dev/topics/security/#ssl-https
# https://docs.djangoproject.com/en/dev/ref/settings/#secure-hsts-seconds
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=31536000)
# https://docs.djangoproject.com/en/dev/ref/settings/#secure-hsts-include-subdomains
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS",
    default=True,
)
# https://docs.djangoproject.com/en/dev/ref/settings/#secure-hsts-preload
SECURE_HSTS_PRELOAD = env.bool("DJANGO_SECURE_HSTS_PRELOAD", default=True)
# https://docs.djangoproject.com/en/dev/ref/middleware/#x-content-type-options-nosniff
SECURE_CONTENT_TYPE_NOSNIFF = env.bool(
    "DJANGO_SECURE_CONTENT_TYPE_NOSNIFF",
    default=True,
)
# https://docs.djangoproject.com/en/dev/ref/settings/#csrf-trusted-origins
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=["https://harmonaize.org"])

# AZURE FRONT DOOR
# ------------------------------------------------------------------------------
# Origin lockdown: reject any request that did not arrive through Front Door. The value is injected
# by Terraform (var.frontdoor_id -> FRONTDOOR_ID); empty disables the check (see config/middleware.py).
FRONTDOOR_ID = env("FRONTDOOR_ID", default="")
# Run the check first so bypass attempts are rejected before any heavier processing.
MIDDLEWARE = ["config.middleware.FrontDoorIDMiddleware", *MIDDLEWARE]
# Front Door forwards the Container Apps FQDN as the Host (ACA ingress requires it) and the real
# public host in X-Forwarded-Host; trust it so Django builds correct absolute URLs (emails, redirects).
# https://docs.djangoproject.com/en/dev/ref/settings/#use-x-forwarded-host
USE_X_FORWARDED_HOST = True

# STATIC & MEDIA
# ------------------------
# Defaults to Azure Blob Storage (the upstream deployment target). Self-hosted
# instances without Azure can set DJANGO_DEFAULT_FILE_STORAGE=local to serve
# media from a local volume instead.
DJANGO_DEFAULT_FILE_STORAGE = env("DJANGO_DEFAULT_FILE_STORAGE", default="azure")

if DJANGO_DEFAULT_FILE_STORAGE == "local":
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
    MEDIA_URL = env("DJANGO_MEDIA_URL", default="/media/")
    MEDIA_ROOT = env("DJANGO_MEDIA_ROOT", default="/app/media/")
else:
    AZURE_ACCOUNT_KEY = env("DJANGO_AZURE_ACCOUNT_KEY")
    AZURE_ACCOUNT_NAME = env("DJANGO_AZURE_ACCOUNT_NAME")
    AZURE_CONTAINER = env("DJANGO_AZURE_CONTAINER_NAME")
    AZURE_URL_EXPIRATION_SECS = env.int("AZURE_URL_EXPIRATION_SECS", default=3600)
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.azure_storage.AzureStorage",
            "OPTIONS": {
                "location": "media",
                "overwrite_files": False,
                "expiration_secs": AZURE_URL_EXPIRATION_SECS,
            },
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

# EMAIL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#default-from-email
DEFAULT_FROM_EMAIL = env(
    "DJANGO_DEFAULT_FROM_EMAIL",
    default="HarmonAIze <noreply@harmonaize.org>",
)
# https://docs.djangoproject.com/en/dev/ref/settings/#server-email
SERVER_EMAIL = env("DJANGO_SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)
# https://docs.djangoproject.com/en/dev/ref/settings/#email-subject-prefix
EMAIL_SUBJECT_PREFIX = env(
    "DJANGO_EMAIL_SUBJECT_PREFIX",
    default="[HarmonAIze] ",
)
ACCOUNT_EMAIL_SUBJECT_PREFIX = EMAIL_SUBJECT_PREFIX

# ADMIN
# ------------------------------------------------------------------------------
# Django Admin URL regex.
ADMIN_URL = env("DJANGO_ADMIN_URL")

# Anymail / SMTP
# ------------------------------------------------------------------------------
SENDGRID_API_KEY = env("SENDGRID_API_KEY", default="")
if SENDGRID_API_KEY:
    INSTALLED_APPS += ["anymail"]
    EMAIL_BACKEND = "anymail.backends.sendgrid.EmailBackend"
    ANYMAIL = {
        "SENDGRID_API_KEY": SENDGRID_API_KEY,
        "SENDGRID_API_URL": env("SENDGRID_API_URL", default="https://api.sendgrid.com/v3/"),
    }
elif env("EMAIL_HOST", default=""):
    # SMTP fallback — used when SendGrid is unavailable (e.g. SSL interception on network)
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("EMAIL_HOST")
    EMAIL_PORT = env.int("EMAIL_PORT", default=587)
    EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
    EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
    EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


# LOGGING
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#logging
# See https://docs.djangoproject.com/en/dev/topics/logging for
# more details on how to customise your logging configuration.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "formatters": {
        "verbose": {
            "format": "%(levelname)s %(asctime)s %(module)s %(process)d %(thread)d %(message)s",
        },
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"level": "INFO", "handlers": ["console"]},
    "loggers": {
        "django.db.backends": {
            "level": "ERROR",
            "handlers": ["console"],
            "propagate": False,
        },
        # Errors logged by the SDK itself
        "sentry_sdk": {"level": "ERROR", "handlers": ["console"], "propagate": False},
        "django.security.DisallowedHost": {
            "level": "ERROR",
            "handlers": ["console"],
            "propagate": False,
        },
    },
}

# Sentry
# ------------------------------------------------------------------------------
SENTRY_DSN = env("SENTRY_DSN", default="")
SENTRY_LOG_LEVEL = env.int("DJANGO_SENTRY_LOG_LEVEL", logging.INFO)

if SENTRY_DSN and SENTRY_DSN.startswith("http"):
    sentry_logging = LoggingIntegration(
        level=SENTRY_LOG_LEVEL,  # Capture info and above as breadcrumbs
        event_level=logging.ERROR,  # Send errors as events
    )
    integrations = [
        sentry_logging,
        DjangoIntegration(),
        CeleryIntegration(),
        RedisIntegration(),
    ]
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=integrations,
        environment=env("SENTRY_ENVIRONMENT", default="production"),
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0),
    )

# django-rest-framework
# -------------------------------------------------------------------------------
# Tools that generate code samples can use SERVERS to point to the correct domain
SPECTACULAR_SETTINGS["SERVERS"] = [
    {"url": "https://harmonaize.org", "description": "Production server"},
]

# OpenAI API Configuration
# ------------------------------------------------------------------------------
# Production OpenAI API key - required for embedding functionality
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")

# Your stuff...
# ------------------------------------------------------------------------------


# INSECURE DEFAULTS
# ------------------------------------------------------------------------------
# Production never runs with the development defaults from base.py, but it is
# not blocked by them either:
# - No ANALYSIS_DEIDENTIFICATION_SALT set: derive a stable secret salt from
#   DJANGO_SECRET_KEY. Set the salt explicitly so rotating the secret key does
#   not change analysis pseudonyms (scripts/init_envs.py --production does this).
# - Armadillo/Keycloak still on admin/admin: the system check warning below is
#   shown on every manage.py command (including migrate on deploy).
import hashlib  # noqa: E402
import hmac  # noqa: E402

from django.core.checks import Warning as CheckWarning  # noqa: E402
from django.core.checks import register  # noqa: E402

from .base import ANALYSIS_ARMADILLO_PASSWORD  # noqa: E402
from .base import ANALYSIS_DEIDENTIFICATION_SALT  # noqa: E402
from .base import ANALYSIS_ENABLED  # noqa: E402
from .base import ANALYSIS_KEYCLOAK_ADMIN_PASSWORD  # noqa: E402
from .base import ANALYSIS_KEYCLOAK_SYNC_ENABLED  # noqa: E402
from .base import INSECURE_ANALYSIS_DEFAULT_CREDENTIAL  # noqa: E402
from .base import INSECURE_ANALYSIS_DEIDENTIFICATION_SALT  # noqa: E402

if ANALYSIS_DEIDENTIFICATION_SALT in ("", INSECURE_ANALYSIS_DEIDENTIFICATION_SALT):
    ANALYSIS_DEIDENTIFICATION_SALT = hmac.new(
        SECRET_KEY.encode(),
        b"harmonaize-analysis-deidentification-salt",
        hashlib.sha256,
    ).hexdigest()


@register("security")
def check_insecure_analysis_credentials(app_configs, **kwargs):
    if not ANALYSIS_ENABLED:
        return []
    defaults = ("", INSECURE_ANALYSIS_DEFAULT_CREDENTIAL)
    insecure = []
    if ANALYSIS_ARMADILLO_PASSWORD in defaults:
        insecure.append("ANALYSIS_ARMADILLO_PASSWORD")
    if ANALYSIS_KEYCLOAK_SYNC_ENABLED and ANALYSIS_KEYCLOAK_ADMIN_PASSWORD in defaults:
        insecure.append("ANALYSIS_KEYCLOAK_ADMIN_PASSWORD")
    return [
        CheckWarning(
            f"{name} is empty or the default 'admin'.",
            hint="Set a strong password for the Armadillo/Keycloak admin account and this setting.",
            id="harmonaize.W001",
        )
        for name in insecure
    ]
