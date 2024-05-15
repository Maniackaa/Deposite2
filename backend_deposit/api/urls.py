from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from api import views


app_name = "api"

v1_router = DefaultRouter()
# v1_router.register("users", UsersViewSet, basename="users")
v1_router.register("payment", views.PaymentViewSet, basename='payment'),
v1_router.register("payment_status", views.PaymentStatusView, basename='payment_status'),
# v1_router.register("payment_card_data", views.PaymentInputCard, basename='payment_card_data'),
# v1_router.register("payment_sms_code", views.PaymentInputSmsCode, basename='payment_sms'),
# v1_router.register("payments", PaymentViewSet, basename="payments")

from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView


urlpatterns = [
    path("", include(v1_router.urls)),
    # path('payment_card_data/', PaymentInputCard.as_view()),
    # path('payment_status/', views.PaymentStatusView),

    path('token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),


]
