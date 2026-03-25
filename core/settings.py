# core/settings.py

from datetime import timedelta
import os
from dotenv import load_dotenv
from decouple import config
from pathlib import Path
# from celery.schedules import crontab
from corsheaders.defaults import default_headers

# Load .env file
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

# SECURITY
SECRET_KEY = config('SECRET_KEY')
DEBUG = config('DEBUG', default=False, cast=bool)
DJANGO_ENV = config('DJANGO_ENV', default='development')


ALLOWED_HOSTS = ['*']

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',

    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'phonenumber_field',
    'cloudinary',
    'cloudinary_storage',
    'django_otp',
    'qrcode',
    'drf_spectacular',
    'django_filters',
    'auditlog',
    'accounts.apps.AccountsConfig',

    'django_qstash'
]

QSTASH_TOKEN = config('QSTASH_TOKEN')
QSTASH_URL = "https://qstash.upstash.io"

QSTASH_SIGNING_KEY = config('QSTASH_SIGNING_KEY', default=None)

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'core.middleware.RequestIDMiddleware',
    'core.middleware.IdempotencyMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'auditlog.middleware.AuditlogMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# Database Configuration based on Environment
if DJANGO_ENV == 'production':
    print("--- Using Production Database ---")
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('DB_NAME'),
            'USER': os.getenv('DB_USER'),
            'PASSWORD': os.getenv('DB_PASSWORD'),
            'HOST': os.getenv('DB_HOST'),
            'PORT': os.getenv('DB_PORT'),
        }
    }
else:
    print("--- Using Development Database ---")
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': config('POSTGRES_DB'),
            'USER': config('POSTGRES_USER', default='postgres'),
            'PASSWORD': config('POSTGRES_PASSWORD'),
            'HOST': 'localhost',
            'PORT': '5432',
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Accra'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / "media"

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Custom User Model
AUTH_USER_MODEL = 'accounts.User'

# CORS
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

CORS_ALLOW_HEADERS = list(default_headers) + [
    "x-idempotency-key",
    "x-request-id",
]

CORS_ALLOW_CREDENTIALS = True

# REST Framework + JWT
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}


SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=5),
    # Or timedelta(minutes=120) for 2 hours
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),  # Optional: extend refresh too
    'UPDATE_LAST_LOGIN': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# Cloudinary
DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'
CLOUDINARY_STORAGE = {
    'CLOUD_NAME': config('CLOUDINARY_CLOUD_NAME'),
    'API_KEY': config('CLOUDINARY_API_KEY'),
    'API_SECRET': config('CLOUDINARY_API_SECRET'),
}

# Dawurobo SMS OTP Settings
DAWUROBO_API_KEY = config('DAWUROBO_API_KEY')
DAWUROBO_ACCESS_TOKEN = config('DAWUROBO_ACCESS_TOKEN')
DAWUROBO_SENDER_ID = config('DAWUROBO_SENDER_ID', default='Dawurobo')

# Axes - Login brute force protection
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
]

AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 30
AXES_LOCK_OUT_AT_FAILURE = True

# drf-spectacular settings
SPECTACULAR_SETTINGS = {
    'TITLE': 'Snappx Backend API',
    'DESCRIPTION': 'Documentation for the Snappx REST API endpoints.',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SECURITY': [
        {
            'BearerAuth': []
        }
    ],
    'COMPONENTS': {
        'securitySchemes': {
            'BearerAuth': {
                'type': 'http',
                'scheme': 'bearer',
                'bearerFormat': 'JWT',
            }
        }
    },
}

# Email Backend configuration
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.sendgrid.net'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'apikey'
EMAIL_HOST_PASSWORD = os.environ.get('SENDGRID_API_KEY')

DEFAULT_FROM_EMAIL = f'no-reply@{os.environ.get("SNAAPX_DOMAIN", "snappx.app")}'
SERVER_EMAIL = EMAIL_HOST_USER

# Site Configuration
SITE_ID = 1

# CELERY_BROKER_URL = os.environ.get(
#     'CELERY_BROKER_URL',
#     'redis://localhost:6379/0'
# )
# CELERY_RESULT_BACKEND = CELERY_BROKER_URL

# Timezone setting
# CELERY_TIMEZONE = 'Africa/Accra'

# Acceptable content formats
# CELERY_ACCEPT_CONTENT = ['json']
# CELERY_TASK_SERIALIZER = 'json'
# CELERY_RESULT_SERIALIZER = 'json'
# CELERY_TASK_ACKS_LATE = True
# CELERY_TASK_REJECT_ON_WORKER_LOST = True
# CELERY_BROKER_TRANSPORT_OPTIONS = {'visibility_timeout': 3600}


# CELERY_BEAT_SCHEDULE = {
#     'process-daily-payouts-at-midnight': {
#         'task': 'accounts.tasks.process_daily_payouts',
#         'schedule': crontab(hour=0, minute=0),
#     },
#     # Financial Reconciliation at 3:00 AM
#     'reconcile-financial-integrity-at-3am': {
#         'task': 'accounts.tasks.reconcile_financial_integrity',
#         'schedule': crontab(hour=3, minute=0),
#     },
#     # Cleanup Idempotency Keys at 4:00 AM
#     'clear-idempotency-keys-daily': {
#         'task': 'accounts.tasks.clear_old_idempotency_keys',
#         'schedule': crontab(hour=4, minute=0),
#     },
#     # Goal Reminders at 8:00 AM
#     'send-goal-reminders-daily': {
#         'task': 'accounts.tasks.send_goal_reminders',
#         'schedule': crontab(hour=8, minute=0),
#     },
# }


ENVIRONMENT = config("ENVIRONMENT", default="development")

if ENVIRONMENT == "production":
    SITE_URL = config("SITE_URL")
else:
    SITE_URL = "http://127.0.0.1:8000"


# ADMINS = [
#     ('Backend Team', 'pierres717@gmail.com'),
# ]

ADMINS = [
    'pierres717@gmail.com',
]

FIELD_ENCRYPTION_KEY = config('FIELD_ENCRYPTION_KEY')


# # core/settings.py

# from datetime import timedelta
# import os
# from dotenv import load_dotenv
# from decouple import config
# from pathlib import Path
# from celery.schedules import crontab
# from corsheaders.defaults import default_headers

# # Load .env file
# BASE_DIR = Path(__file__).resolve().parent.parent
# load_dotenv(BASE_DIR / '.env')

# # SECURITY
# SECRET_KEY = config('SECRET_KEY')
# DEBUG = config('DEBUG', default=False, cast=bool)
# DJANGO_ENV = config('DJANGO_ENV', default='development')


# ALLOWED_HOSTS = ['*']

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

#     # 'accounts',
# ]

# MIDDLEWARE = [
#     'corsheaders.middleware.CorsMiddleware',
#     'core.middleware.RequestIDMiddleware',
#     'core.middleware.IdempotencyMiddleware',
#     'django.middleware.security.SecurityMiddleware',
#     'whitenoise.middleware.WhiteNoiseMiddleware',
#     'django.contrib.sessions.middleware.SessionMiddleware',
#     'corsheaders.middleware.CorsMiddleware',
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

# # Database Configuration based on Environment
# if DJANGO_ENV == 'production':
#     print("--- Using Production Database ---")
#     DATABASES = {
#         'default': {
#             'ENGINE': 'django.db.backends.postgresql',
#             'NAME': os.getenv('DB_NAME'),
#             'USER': os.getenv('DB_USER'),
#             'PASSWORD': os.getenv('DB_PASSWORD'),
#             'HOST': os.getenv('DB_HOST'),
#             'PORT': os.getenv('DB_PORT'),
#         }
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

# # Internationalization
# LANGUAGE_CODE = 'en-us'
# TIME_ZONE = 'Africa/Accra'
# USE_I18N = True
# USE_TZ = True

# # Static files (CSS, JavaScript, Images)
# STATIC_URL = '/static/'
# STATICFILES_DIRS = [BASE_DIR / "static"]
# STATIC_ROOT = BASE_DIR / "staticfiles"

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
# ]

# CORS_ALLOW_HEADERS = list(default_headers) + [
#     "x-idempotency-key",
#     "x-request-id",
# ]

# CORS_ALLOW_CREDENTIALS = True

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
#     'ACCESS_TOKEN_LIFETIME': timedelta(minutes=5),
#     # Or timedelta(minutes=120) for 2 hours
#     'REFRESH_TOKEN_LIFETIME': timedelta(days=30),  # Optional: extend refresh too
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

# CELERY_BROKER_URL = os.environ.get(
#     'CELERY_BROKER_URL',
#     'redis://localhost:6379/0'
# )
# CELERY_RESULT_BACKEND = CELERY_BROKER_URL

# # Timezone setting
# CELERY_TIMEZONE = 'Africa/Accra'

# # Acceptable content formats
# CELERY_ACCEPT_CONTENT = ['json']
# CELERY_TASK_SERIALIZER = 'json'
# CELERY_RESULT_SERIALIZER = 'json'
# CELERY_TASK_ACKS_LATE = True
# CELERY_TASK_REJECT_ON_WORKER_LOST = True
# CELERY_BROKER_TRANSPORT_OPTIONS = {'visibility_timeout': 3600}


# CELERY_BEAT_SCHEDULE = {
#     'process-daily-payouts-at-midnight': {
#         'task': 'accounts.tasks.process_daily_payouts',
#         'schedule': crontab(hour=0, minute=0),
#     },
#     # Financial Reconciliation at 3:00 AM
#     'reconcile-financial-integrity-at-3am': {
#         'task': 'accounts.tasks.reconcile_financial_integrity',
#         'schedule': crontab(hour=3, minute=0),
#     },
#     # Cleanup Idempotency Keys at 4:00 AM
#     'clear-idempotency-keys-daily': {
#         'task': 'accounts.tasks.clear_old_idempotency_keys',
#         'schedule': crontab(hour=4, minute=0),
#     },
#     # Goal Reminders at 8:00 AM
#     'send-goal-reminders-daily': {
#         'task': 'accounts.tasks.send_goal_reminders',
#         'schedule': crontab(hour=8, minute=0),
#     },
# }


# ENVIRONMENT = config("ENVIRONMENT", default="development")

# if ENVIRONMENT == "production":
#     SITE_URL = config("SITE_URL")
# else:
#     SITE_URL = "http://127.0.0.1:8000"


# # ADMINS = [
# #     ('Backend Team', 'pierres717@gmail.com'),
# # ]

# ADMINS = [
#     'pierres717@gmail.com',
# ]

# FIELD_ENCRYPTION_KEY = config('FIELD_ENCRYPTION_KEY')
