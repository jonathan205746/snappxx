import sys
import os
from datetime import timedelta
from dotenv import load_dotenv
from decouple import config
from pathlib import Path
import dj_database_url
from corsheaders.defaults import default_headers
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
DJANGO_ENV = config("DJANGO_ENV", default="development")


if DJANGO_ENV == "production" and DEBUG:
    raise ImproperlyConfigured(
        "DEBUG=True is set but DJANGO_ENV=production. "
        "This exposes full stack traces and environment variables to the internet. "
        "Set DEBUG=False in your production environment."
    )

ADMIN_PATH = config("ADMIN_PATH", default="admin")

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    ".vercel.app",
    "snappx-backend-ochre.vercel.app",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",

    "rest_framework",
    "rest_framework_simplejwt",

    "rest_framework_simplejwt.token_blacklist",

    "corsheaders",
    "phonenumber_field",
    "cloudinary",
    "cloudinary_storage",
    "django_otp",
    "qrcode",
    "drf_spectacular",
    "django_filters",
    "auditlog",
    "axes",
    "accounts.apps.AccountsConfig",
]

INSTALLED_APPS += ["django_qstash"]

QSTASH_TOKEN = config("QSTASH_TOKEN", default=None)
QSTASH_URL = config("ACTUAL_QSTASH_URL", default=config("QSTASH_URL", default="https://qstash.upstash.io"))
QSTASH_DOMAIN = config("QSTASH_DOMAIN", default=None)

if not QSTASH_DOMAIN:
    if DEBUG:
        QSTASH_DOMAIN = "http://127.0.0.1:8000"
    else:
        v_url = config("VERCEL_URL", default=None)
        if v_url:
            QSTASH_DOMAIN = f"https://{v_url}"
        else:
            QSTASH_DOMAIN = "https://snappx-backend-ochre.vercel.app"

if len(sys.argv) > 1 and sys.argv[1] in [
    "createsuperuser", "migrate", "makemigrations", "collectstatic",
    "shell", "dbshell", "test", "check", "flush", "runserver",
]:
    QSTASH_TOKEN = QSTASH_TOKEN or "dummy-token-for-local-commands"
    QSTASH_DOMAIN = QSTASH_DOMAIN or "https://snappx-backend-ochre.vercel.app"

DJANGO_QSTASH_DOMAIN = QSTASH_DOMAIN
QSTASH_CURRENT_SIGNING_KEY = config("QSTASH_CURRENT_SIGNING_KEY", default=None)

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",

    "core.middleware.RequestIDMiddleware",
    "core.middleware.IdempotencyMiddleware",

    "django.middleware.security.SecurityMiddleware",

    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "auditlog.middleware.AuditlogMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"

print(f"--- DJANGO_ENV = {DJANGO_ENV} ---")

if DJANGO_ENV == "production" or not DEBUG:
    print("--- Using Neon Database (Production / Preview) ---")
    DATABASES = {
        "default": dj_database_url.config(
            default=config("DATABASE_URL"),
            conn_max_age=600,
            ssl_require=True,
        )
    }
else:
    print("--- Using Development Database ---")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": config("POSTGRES_DB"),
            "USER": config("POSTGRES_USER", default="postgres"),
            "PASSWORD": config("POSTGRES_PASSWORD"),
            "HOST": "localhost",
            "PORT": "5432",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

GROQ_API_KEY = config("GROQ_API_KEY", default=None)

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Accra"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")
STATICFILES_DIRS = [os.path.join(BASE_DIR, "static")]

STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://snappexchange.vercel.app",
    "https://snappx.app",
]

FRONTEND_URL = config("FRONTEND_URL", default="https://www.snappx.app")
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https:\/\/[a-zA-Z0-9-]+\-snappexchange\.vercel\.app$",
    r"^https:\/\/[a-zA-Z0-9-]+\.snappx\.app$",
]

CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
    "x-idempotency-key",
    "x-request-id",
]

CORS_EXPOSE_HEADERS = ["x-request-id"]
CORS_URLS_REGEX = r"^/api/.*$"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",

    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/hour",
        "user": "1000/hour",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),

    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),

    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,

    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),

    "ALGORITHM": "HS512",
}

DEFAULT_FILE_STORAGE = "cloudinary_storage.storage.MediaCloudinaryStorage"
CLOUDINARY_STORAGE = {
    "CLOUD_NAME": config("CLOUDINARY_CLOUD_NAME"),
    "API_KEY": config("CLOUDINARY_API_KEY"),
    "API_SECRET": config("CLOUDINARY_API_SECRET"),
}

DAWUROBO_API_KEY = config("DAWUROBO_API_KEY")
DAWUROBO_ACCESS_TOKEN = config("DAWUROBO_ACCESS_TOKEN")
DAWUROBO_SENDER_ID = config("DAWUROBO_SENDER_ID", default="Dawurobo")

AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    "django.contrib.auth.backends.ModelBackend",
]

AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1
AXES_LOCK_OUT_AT_FAILURE = True
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
AXES_RESET_ON_SUCCESS = True
AXES_ENABLE_ADMIN = True
AXES_VERBOSE = False

SPECTACULAR_SETTINGS = {
    "TITLE": "Snappx Backend API",
    "DESCRIPTION": "Documentation for the Snappx REST API endpoints.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_AUTHENTICATION": ["rest_framework.authentication.SessionAuthentication"],
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAdminUser"],
    "SECURITY": [{"BearerAuth": []}],
    "COMPONENTS": {
        "securitySchemes": {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            }
        }
    },
}

TELEGRAM_BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None)
TELEGRAM_WEBHOOK_SECRET = config("TELEGRAM_WEBHOOK_SECRET", default=None)

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.resend.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = "resend"
EMAIL_HOST_PASSWORD = os.environ.get("RESEND_API_KEY")
DEFAULT_FROM_EMAIL = f'no-reply@{os.environ.get("SNAAPX_DOMAIN", "snappx.app")}'
SERVER_EMAIL = DEFAULT_FROM_EMAIL

SITE_ID = 1
ENVIRONMENT = config("ENVIRONMENT", default="development")

if ENVIRONMENT == "production":
    SITE_URL = config("SITE_URL")
else:
    SITE_URL = "http://127.0.0.1:8000"

ADMINS = [
    ("Pierre", "pierres717@gmail.com"),
]

FIELD_ENCRYPTION_KEY = config("FIELD_ENCRYPTION_KEY")

HASH_SALT = config("HASH_SALT", default=None)

if not HASH_SALT:
    if DJANGO_ENV in ("production", "staging") or not DEBUG:
        raise ImproperlyConfigured(
            "HASH_SALT environment variable is required in production."
        )
    else:
        HASH_SALT = "dev-only-insecure-hash-salt-please-set-in-production"
        import warnings
        warnings.warn(
            "HASH_SALT not set in .env — using fallback. "
            "Set HASH_SALT in your .env to avoid rehashing users on every setup.",
            stacklevel=2,
        )

PAYSTACK_SECRET_KEY = config("PAYSTACK_SECRET_KEY", default=None)
PAYSTACK_PUBLIC_KEY = config("PAYSTACK_PUBLIC_KEY", default=None)

if len(sys.argv) > 1 and sys.argv[1] in [
    "createsuperuser", "migrate", "makemigrations", "collectstatic",
    "shell", "dbshell", "test", "check", "flush", "runserver",
]:
    PAYSTACK_SECRET_KEY = PAYSTACK_SECRET_KEY or "sk_test_dummy_for_local"
    PAYSTACK_PUBLIC_KEY = PAYSTACK_PUBLIC_KEY or "pk_test_dummy_for_local"

X_FRAME_OPTIONS = "DENY"

SECURE_CONTENT_TYPE_NOSNIFF = True

SECURE_BROWSER_XSS_FILTER = True


SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

if DJANGO_ENV == "production":
    SECURE_SSL_REDIRECT = True

    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = False
    SESSION_COOKIE_SAMESITE = "Lax"

_UPSTASH_REDIS_URL = config("UPSTASH_REDIS_URL", default=None)

if _UPSTASH_REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": _UPSTASH_REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "SOCKET_CONNECT_TIMEOUT": 5,
                "SOCKET_TIMEOUT": 5,
                "CONNECTION_POOL_KWARGS": {"ssl_cert_reqs": None},
            },
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "snappx-otp-lockout",
        }
    }

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "pii_scrubber": {
            "()": "core.logging_filters.PIIScrubberFilter",
        },
    },
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["pii_scrubber"],
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "accounts.finance": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "accounts.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
