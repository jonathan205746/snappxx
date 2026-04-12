# core/urls.py

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django_qstash.views import QStashWebhook

# ── SECURITY: Configurable admin path ────────────────────────────────────────
# Set ADMIN_PATH=some-random-string in your .env
# e.g. ADMIN_PATH=xk92p-control  → admin lives at /xk92p-control/
# Scanners and bots always try /admin/ — moving it makes their job much harder.
ADMIN_PATH = getattr(settings, "ADMIN_PATH", "admin")

urlpatterns = [
    path(f"{ADMIN_PATH}/", admin.site.urls),

    # Auth endpoints (login, tokens, signup, OTP)
    path("api/auth/", include("accounts.auth_urls")),

    # Application endpoints (groups, goals, payments, AI, notifications)
    path("api/accounts/", include("accounts.urls")),

    # QStash webhook (background task delivery)
    path("qstash/webhook/", QStashWebhook, name="qstash-webhook"),
]

# ── SECURITY: Swagger / OpenAPI schema ───────────────────────────────────────
# Swagger exposes your full API surface: every endpoint, every field name,
# every authentication scheme. In production this is a free recon tool for attackers.
#
# Rule:
#   development / staging  → open access (your team needs it)
#   production             → staff login required
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

if getattr(settings, "DJANGO_ENV", "development") != "production":
    # Open access in development — no login required
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path(
            "api/schema/swagger-ui/",
            SpectacularSwaggerView.as_view(url_name="schema"),
            name="swagger-ui",
        ),
    ]
else:
    # Production: both the raw schema JSON and the UI require staff login.
    # staff_member_required redirects to the Django admin login page.
    # Non-staff users see a login prompt, not the schema.
    from django.contrib.admin.views.decorators import staff_member_required
    from django.views.decorators.cache import never_cache

    urlpatterns += [
        path(
            "api/schema/",
            staff_member_required(never_cache(SpectacularAPIView.as_view())),
            name="schema",
        ),
        path(
            "api/schema/swagger-ui/",
            staff_member_required(
                never_cache(SpectacularSwaggerView.as_view(url_name="schema"))
            ),
            name="swagger-ui",
        ),
    ]
