from django.urls import include, path
from rest_framework.routers import DefaultRouter

from api.views import PaymentStatusView

app_name = "api"

# v1_router = DefaultRouter()
# v1_router.register("payments", PaymentUpdateStatusView.as_view, basename="payments")

urlpatterns = [
    path('payment_status/', PaymentStatusView.as_view()),
]
