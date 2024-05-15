from django.contrib import admin
from django.urls import path, include

from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

urlpatterns = [
    path('', include('deposit.urls', namespace='deposit')),
    path('', include('payment.urls', namespace='payment')),
    path('admin/', admin.site.urls),
    path('auth/', include('users.urls', namespace='users')),
    path('ocr/', include('ocr.urls', namespace='ocr')),
    path("__debug__/", include("debug_toolbar.urls")),
    path('api/', include('api.urls')),

    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='docs'),
    path(
        'api/redoc/',
        SpectacularRedocView.as_view(url_name='schema'),
        name='redoc'
    ),
]


handler403 = 'core.views.permission_denied'
handler500 = 'core.views.server_error'
