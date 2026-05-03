from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django_qstash.views import QStashWebhook


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

from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

if getattr(settings, "DJANGO_ENV", "development") != "production":
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path(
            "api/schema/swagger-ui/",
            SpectacularSwaggerView.as_view(url_name="schema"),
            name="swagger-ui",
        ),
    ]
else:
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
