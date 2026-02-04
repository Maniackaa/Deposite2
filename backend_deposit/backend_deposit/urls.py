from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

# urlpatterns = [
    # path('admin/', admin.site.urls),
    # path('api/', include('api.urls')),
urlpatterns = [
    path('', include('deposit.urls', namespace='deposit')),
    path('admin/', admin.site.urls),
    path('auth/', include('users.urls', namespace='users')),
    path('ocr/', include('ocr.urls', namespace='ocr')),
    path("__debug__/", include("debug_toolbar.urls")),
    # JWT Token endpoints
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/token/verify/', TokenVerifyView.as_view(), name='token_verify'),
    # Birpay API (для ASU и др. — работа с Birpay только через этот API)
    path('api/birpay/', include('deposit.urls_birpay_api', namespace='api_birpay')),
]


handler403 = 'core.views.permission_denied'
