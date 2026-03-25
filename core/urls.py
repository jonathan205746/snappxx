# core/urls.py

from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path('admin/', admin.site.urls),

    # Auth Endpoints (Login, Tokens, Signup)
    path('api/auth/', include('accounts.auth_urls')),

    # Application/Group Management Endpoints (Groups)
    path('api/accounts/', include('accounts.urls')),

    # Swagger Docs Endpoints
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]
