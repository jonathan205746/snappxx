# core/settings.py

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

# ─────────────────────────────────────────────────────────────────────────────
# CORE IDENTITY
# ─────────────────────────────────────────────────────────────────────────────

SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
DJANGO_ENV = config("DJANGO_ENV", default="development")

# ── SECURITY: Hard guard — refuse to start if DEBUG is on in production ──────
# This prevents a misconfigured environment variable from exposing stack traces,
# SQL queries, and environment variables to the public internet.
if DJANGO_ENV == "production" and DEBUG:
    raise ImproperlyConfigured(
        "DEBUG=True is set but DJANGO_ENV=production. "
        "This exposes full stack traces and environment variables to the internet. "
        "Set DEBUG=False in your production environment."
    )

# ── SECURITY: Admin path — set ADMIN_PATH to a random string in production ───
# e.g. ADMIN_PATH=xk92p-manage   (then /admin/ returns 404, scanners get nothing)
# Default is 'admin' for local development convenience only.
ADMIN_PATH = config("ADMIN_PATH", default="admin")

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    ".vercel.app",
    "snappx-backend-ochre.vercel.app",
]

# ─────────────────────────────────────────────────────────────────────────────
# INSTALLED APPS
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# QSTASH
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# MIDDLEWARE
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# PASSWORD VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ─────────────────────────────────────────────────────────────────────────────
# THIRD-PARTY API KEYS
# ─────────────────────────────────────────────────────────────────────────────

GROQ_API_KEY = config("GROQ_API_KEY", default=None)

# ─────────────────────────────────────────────────────────────────────────────
# INTERNATIONALISATION
# ─────────────────────────────────────────────────────────────────────────────

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Accra"
USE_I18N = True
USE_TZ = True

# ─────────────────────────────────────────────────────────────────────────────
# STATIC & MEDIA FILES
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# CORS
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# REST FRAMEWORK + JWT
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# CLOUDINARY
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_FILE_STORAGE = "cloudinary_storage.storage.MediaCloudinaryStorage"
CLOUDINARY_STORAGE = {
    "CLOUD_NAME": config("CLOUDINARY_CLOUD_NAME"),
    "API_KEY": config("CLOUDINARY_API_KEY"),
    "API_SECRET": config("CLOUDINARY_API_SECRET"),
}

# ─────────────────────────────────────────────────────────────────────────────
# DAWUROBO SMS OTP
# ─────────────────────────────────────────────────────────────────────────────

DAWUROBO_API_KEY = config("DAWUROBO_API_KEY")
DAWUROBO_ACCESS_TOKEN = config("DAWUROBO_ACCESS_TOKEN")
DAWUROBO_SENDER_ID = config("DAWUROBO_SENDER_ID", default="Dawurobo")

# ─────────────────────────────────────────────────────────────────────────────
# AUTHENTICATION BACKENDS
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# DRF SPECTACULAR (OpenAPI / Swagger)
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# EMAIL
# ─────────────────────────────────────────────────────────────────────────────

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.sendgrid.net"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = "apikey"
EMAIL_HOST_PASSWORD = os.environ.get("SENDGRID_API_KEY")
DEFAULT_FROM_EMAIL = f'no-reply@{os.environ.get("SNAAPX_DOMAIN", "snappx.app")}'
SERVER_EMAIL = EMAIL_HOST_USER

SITE_ID = 1
ENVIRONMENT = config("ENVIRONMENT", default="development")

if ENVIRONMENT == "production":
    SITE_URL = config("SITE_URL")
else:
    SITE_URL = "http://127.0.0.1:8000"

ADMINS = [
    ("Pierre", "pierres717@gmail.com"),
]

# ─────────────────────────────────────────────────────────────────────────────
# ENCRYPTION & HASHING
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# PAYSTACK
# ─────────────────────────────────────────────────────────────────────────────

PAYSTACK_SECRET_KEY = config("PAYSTACK_SECRET_KEY", default=None)
PAYSTACK_PUBLIC_KEY = config("PAYSTACK_PUBLIC_KEY", default=None)

if len(sys.argv) > 1 and sys.argv[1] in [
    "createsuperuser", "migrate", "makemigrations", "collectstatic",
    "shell", "dbshell", "test", "check", "flush", "runserver",
]:
    PAYSTACK_SECRET_KEY = PAYSTACK_SECRET_KEY or "sk_test_dummy_for_local"
    PAYSTACK_PUBLIC_KEY = PAYSTACK_PUBLIC_KEY or "pk_test_dummy_for_local"

# ─────────────────────────────────────────────────────────────────────────────
# SECURITY HEADERS
# These are read by Django's SecurityMiddleware (already in MIDDLEWARE above).
# ─────────────────────────────────────────────────────────────────────────────

# ── Clickjacking protection ───────────────────────────────────────────────────
# Tells browsers: this site must never be loaded in an iframe.
X_FRAME_OPTIONS = "DENY"

# ── MIME-type sniffing protection ─────────────────────────────────────────────
# Tells browsers: don't guess the content type — trust the Content-Type header.
# Prevents attacks where a .jpg containing JS gets executed as a script.
SECURE_CONTENT_TYPE_NOSNIFF = True

# ── XSS filter (legacy browsers) ─────────────────────────────────────────────
SECURE_BROWSER_XSS_FILTER = True

# ── Referrer policy ──────────────────────────────────────────────────────────
# Don't leak the full URL (which may contain tokens) to third-party sites.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

if DJANGO_ENV == "production":
    # ── SECURITY: Force HTTPS ─────────────────────────────────────────────────
    # Redirects all HTTP requests to HTTPS.
    # Only safe to enable once you have a real SSL certificate.
    SECURE_SSL_REDIRECT = True

    # ── SECURITY: HSTS ───────────────────────────────────────────────────────
    # Tells browsers: for the next 1 year, only connect to this domain over HTTPS.
    # Once set, this cannot easily be undone — browsers cache it.
    # Start with a short value (e.g. 3600) in staging, then increase to 31536000.
    SECURE_HSTS_SECONDS = 31536000          # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True   # applies to *.snappx.app too
    SECURE_HSTS_PRELOAD = True              # submit to browser preload lists

    # ── SECURITY: Secure cookies ─────────────────────────────────────────────
    # Session and CSRF cookies are only sent over HTTPS.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # ── SECURITY: Cookie flags ────────────────────────────────────────────────
    SESSION_COOKIE_HTTPONLY = True   # JS cannot read the session cookie
    CSRF_COOKIE_HTTPONLY = False     # CSRF token must be readable by JS for AJAX
    SESSION_COOKIE_SAMESITE = "Lax" # CSRF protection for cookie-based sessions

# ─────────────────────────────────────────────────────────────────────────────
# CACHE
# Used by: OTP lockout (accounts/otp_lockout.py)
# ─────────────────────────────────────────────────────────────────────────────

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
    # Fallback for local development where Redis isn't running
    # OTP lockout still works locally, just not across process restarts
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "snappx-otp-lockout",
        }
    }

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# Scrubs PII (phone numbers, emails) before log records are emitted.
# ─────────────────────────────────────────────────────────────────────────────

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        # ── SECURITY: PII scrubber ────────────────────────────────────────────
        # core/logging_filters.py — replaces phone numbers and emails with
        # masked versions before they hit the log output.
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
        # Financial events — INFO and above, PII-scrubbed
        "accounts.finance": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        # Security events — WARNING and above, PII-scrubbed
        # Used by: VerifyOTPView, AIChatView, ProfileUpdateView
        "accounts.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        # Django internals — only errors
        "django": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        # Django security framework events
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

# # core/settings.py

# import sys
# import os
# from datetime import timedelta
# from dotenv import load_dotenv
# from decouple import config
# from pathlib import Path
# import dj_database_url
# from corsheaders.defaults import default_headers
# from django.core.exceptions import ImproperlyConfigured

# # Load .env file
# BASE_DIR = Path(__file__).resolve().parent.parent
# load_dotenv(BASE_DIR / '.env')

# # SECURITY
# SECRET_KEY = config('SECRET_KEY')
# DEBUG = config('DEBUG', default=False, cast=bool)
# DJANGO_ENV = config('DJANGO_ENV', default='development')

# ALLOWED_HOSTS = [
#     'localhost',
#     '127.0.0.1',
#     '.vercel.app',
#     'snappx-backend-ochre.vercel.app'
# ]

# # Application definition
# INSTALLED_APPS = [
#     'django.contrib.admin',
#     'django.contrib.auth',
#     'django.contrib.contenttypes',
#     'django.contrib.sessions',
#     'django.contrib.messages',
#     'django.contrib.staticfiles',
#     'django.contrib.sites',

#     'rest_framework',
#     'rest_framework_simplejwt',
#     'corsheaders',
#     'phonenumber_field',
#     'cloudinary',
#     'cloudinary_storage',
#     'django_otp',
#     'qrcode',
#     'drf_spectacular',
#     'django_filters',
#     'auditlog',
#     'accounts.apps.AccountsConfig',

# ]

# INSTALLED_APPS += ['django_qstash']
# QSTASH_TOKEN = config('QSTASH_TOKEN', default=None)
# # QSTASH_URL = config('QSTASH_URL', default='https://qstash.upstash.io')

# QSTASH_URL = config('ACTUAL_QSTASH_URL', default=config('QSTASH_URL', default='https://qstash.upstash.io'))

# QSTASH_DOMAIN = config('QSTASH_DOMAIN', default=None)
# if not QSTASH_DOMAIN:
#     if DEBUG:
#         QSTASH_DOMAIN = "http://127.0.0.1:8000"
#     else:
#         v_url = config('VERCEL_URL', default=None)
#         if v_url:
#             QSTASH_DOMAIN = f"https://{v_url}"
#         else:
#             QSTASH_DOMAIN = "https://snappx-backend-ochre.vercel.app"


# if len(sys.argv) > 1 and sys.argv[1] in [
#     'createsuperuser', 'migrate', 'makemigrations', 'collectstatic',
#     'shell', 'dbshell', 'test', 'check', 'flush', 'runserver'
# ]:
#     QSTASH_TOKEN = QSTASH_TOKEN or "dummy-token-for-local-commands"
#     QSTASH_DOMAIN = QSTASH_DOMAIN or "https://snappx-backend-ochre.vercel.app"

# DJANGO_QSTASH_DOMAIN = QSTASH_DOMAIN
# QSTASH_CURRENT_SIGNING_KEY = config('QSTASH_CURRENT_SIGNING_KEY', default=None)

# MIDDLEWARE = [
#     'corsheaders.middleware.CorsMiddleware',
#     'core.middleware.RequestIDMiddleware',
#     'core.middleware.IdempotencyMiddleware',
#     'django.middleware.security.SecurityMiddleware',
#     'whitenoise.middleware.WhiteNoiseMiddleware',
#     'django.contrib.sessions.middleware.SessionMiddleware',
#     'django.middleware.common.CommonMiddleware',
#     'django.middleware.csrf.CsrfViewMiddleware',
#     'django.contrib.auth.middleware.AuthenticationMiddleware',
#     'auditlog.middleware.AuditlogMiddleware',
#     'django.contrib.messages.middleware.MessageMiddleware',
#     'django.middleware.clickjacking.XFrameOptionsMiddleware',
# ]

# ROOT_URLCONF = 'core.urls'

# TEMPLATES = [
#     {
#         'BACKEND': 'django.template.backends.django.DjangoTemplates',
#         'DIRS': [BASE_DIR / 'templates'],
#         'APP_DIRS': True,
#         'OPTIONS': {
#             'context_processors': [
#                 'django.template.context_processors.debug',
#                 'django.template.context_processors.request',
#                 'django.contrib.auth.context_processors.auth',
#                 'django.contrib.messages.context_processors.messages',
#             ],
#         },
#     },
# ]

# WSGI_APPLICATION = 'core.wsgi.application'

# print(f"--- DJANGO_ENV = {DJANGO_ENV} ---")

# # Database Configuration based on Environment
# if DJANGO_ENV == 'production' or not DEBUG:
#     print("--- Using Neon Database (Production / Preview) ---")
#     DATABASES = {
#         'default': dj_database_url.config(
#             default=config('DATABASE_URL'),
#             conn_max_age=600,
#             ssl_require=True
#         )
#     }
# else:
#     print("--- Using Development Database ---")
#     DATABASES = {
#         'default': {
#             'ENGINE': 'django.db.backends.postgresql',
#             'NAME': config('POSTGRES_DB'),
#             'USER': config('POSTGRES_USER', default='postgres'),
#             'PASSWORD': config('POSTGRES_PASSWORD'),
#             'HOST': 'localhost',
#             'PORT': '5432',
#         }
#     }

# # Password validation
# AUTH_PASSWORD_VALIDATORS = [
#     {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
#     {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
#     {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
#     {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
# ]

# # ANTHROPIC_API_KEY = config('ANTHROPIC_API_KEY', default=None)

# # GEMINI_API_KEY = config('GEMINI_API_KEY', default=None)

# GROQ_API_KEY = config('GROQ_API_KEY', default=None)

# # Internationalization
# LANGUAGE_CODE = 'en-us'
# TIME_ZONE = 'Africa/Accra'
# USE_I18N = True
# USE_TZ = True

# STATIC_URL = '/static/'
# STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# STATICFILES_DIRS = [
#     os.path.join(BASE_DIR, 'static'),
# ]

# STORAGES = {
#     "staticfiles": {
#         "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
#     },
# }

# # Media files
# MEDIA_URL = '/media/'
# MEDIA_ROOT = BASE_DIR / "media"

# # Default primary key field type
# DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# # Custom User Model
# AUTH_USER_MODEL = 'accounts.User'

# # CORS
# CORS_ALLOWED_ORIGINS = [
#     "http://localhost:3000",
#     "http://127.0.0.1:3000",
#     "https://snappexchange.vercel.app",
#     "https://snappx.app"
# ]

# FRONTEND_URL = config("FRONTEND_URL", default="https://www.snappx.app")

# CORS_ALLOW_CREDENTIALS = True

# CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

# # For Vercel preview deployments (highly recommended during development)
# CORS_ALLOWED_ORIGIN_REGEXES = [
#     # Allows Vercel preview deployments
#     r"^https:\/\/[a-zA-Z0-9-]+\-snappexchange\.vercel\.app$",

#     # Allows all subdomains of snappx.app (e.g., https://www.snappx.app, https://dev.snappx.app)
#     r"^https:\/\/[a-zA-Z0-9-]+\.snappx\.app$",
# ]

# CORS_ALLOW_HEADERS = [
#     "accept",
#     "accept-encoding",
#     "authorization",
#     "content-type",
#     "dnt",
#     "origin",
#     "user-agent",
#     "x-csrftoken",
#     "x-requested-with",
#     "x-idempotency-key",      # ← Your custom header
#     "x-request-id",
# ]

# CORS_EXPOSE_HEADERS = ["x-request-id"]

# # Optional: Only apply CORS to API routes (cleaner)
# CORS_URLS_REGEX = r"^/api/.*$"

# # REST Framework + JWT
# REST_FRAMEWORK = {
#     'DEFAULT_AUTHENTICATION_CLASSES': (
#         'rest_framework_simplejwt.authentication.JWTAuthentication',
#     ),
#     'DEFAULT_PERMISSION_CLASSES': (
#         'rest_framework.permissions.IsAuthenticated',
#     ),
#     'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
#     'PAGE_SIZE': 20,
#     'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
# }


# SIMPLE_JWT = {
#     'ACCESS_TOKEN_LIFETIME': timedelta(minutes=30),
#     'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
#     'UPDATE_LAST_LOGIN': True,
#     'AUTH_HEADER_TYPES': ('Bearer',),
# }

# # Cloudinary
# DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'
# CLOUDINARY_STORAGE = {
#     'CLOUD_NAME': config('CLOUDINARY_CLOUD_NAME'),
#     'API_KEY': config('CLOUDINARY_API_KEY'),
#     'API_SECRET': config('CLOUDINARY_API_SECRET'),
# }

# # Dawurobo SMS OTP Settings
# DAWUROBO_API_KEY = config('DAWUROBO_API_KEY')
# DAWUROBO_ACCESS_TOKEN = config('DAWUROBO_ACCESS_TOKEN')
# DAWUROBO_SENDER_ID = config('DAWUROBO_SENDER_ID', default='Dawurobo')

# # Axes - Login brute force protection
# AUTHENTICATION_BACKENDS = [
#     'django.contrib.auth.backends.ModelBackend',
# ]

# AXES_FAILURE_LIMIT = 5
# AXES_COOLOFF_TIME = 30
# AXES_LOCK_OUT_AT_FAILURE = True

# # drf-spectacular settings
# SPECTACULAR_SETTINGS = {
#     'TITLE': 'Snappx Backend API',
#     'DESCRIPTION': 'Documentation for the Snappx REST API endpoints.',
#     'VERSION': '1.0.0',
#     'SERVE_INCLUDE_SCHEMA': False,
#     'SECURITY': [
#         {
#             'BearerAuth': []
#         }
#     ],
#     'COMPONENTS': {
#         'securitySchemes': {
#             'BearerAuth': {
#                 'type': 'http',
#                 'scheme': 'bearer',
#                 'bearerFormat': 'JWT',
#             }
#         }
#     },
# }

# # Email Backend configuration
# EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
# EMAIL_HOST = 'smtp.sendgrid.net'
# EMAIL_PORT = 587
# EMAIL_USE_TLS = True
# EMAIL_HOST_USER = 'apikey'
# EMAIL_HOST_PASSWORD = os.environ.get('SENDGRID_API_KEY')

# DEFAULT_FROM_EMAIL = f'no-reply@{os.environ.get("SNAAPX_DOMAIN", "snappx.app")}'
# SERVER_EMAIL = EMAIL_HOST_USER

# # Site Configuration
# SITE_ID = 1

# ENVIRONMENT = config("ENVIRONMENT", default="development")

# if ENVIRONMENT == "production":
#     SITE_URL = config("SITE_URL")
# else:
#     SITE_URL = "http://127.0.0.1:8000"


# ADMINS = [
#     ('Pierre', 'pierres717@gmail.com'),
# ]

# FIELD_ENCRYPTION_KEY = config('FIELD_ENCRYPTION_KEY')

# PAYSTACK_SECRET_KEY = config('PAYSTACK_SECRET_KEY', default=None)
# PAYSTACK_PUBLIC_KEY = config('PAYSTACK_PUBLIC_KEY', default=None)

# # Dummy values for local management commands
# if len(sys.argv) > 1 and sys.argv[1] in [
#     'createsuperuser', 'migrate', 'makemigrations', 'collectstatic',
#     'shell', 'dbshell', 'test', 'check', 'flush', 'runserver'
# ]:
#     PAYSTACK_SECRET_KEY = PAYSTACK_SECRET_KEY or "sk_test_dummy_for_local"
#     PAYSTACK_PUBLIC_KEY = PAYSTACK_PUBLIC_KEY or "pk_test_dummy_for_local"


# # Security

# HASH_SALT = config('HASH_SALT', default=None)

# if not HASH_SALT:
#     if DJANGO_ENV in ('production', 'staging') or not DEBUG:
#         raise ImproperlyConfigured(
#             "HASH_SALT environment variable is required in production."
#         )
#     else:
#         # IMPORTANT: This value must never change during development or
#         # all existing momo_number_hash values will be invalidated.
#         HASH_SALT = "dev-only-insecure-hash-salt-please-set-in-production"
#         import warnings
#         warnings.warn(
#             "HASH_SALT not set in .env — using fallback. "
#             "Set HASH_SALT in your .env to avoid rehashing users on every setup.",
#             stacklevel=2
#         )
