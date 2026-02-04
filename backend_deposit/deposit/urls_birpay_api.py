"""
URL-маршруты REST API Birpay (обёртка над BirpayClient).
Базовый префикс: /api/birpay/
"""
from django.urls import path

from . import views_birpay_api

app_name = 'api_birpay'

urlpatterns = [
    # Реквизиты
    path('requisites/', views_birpay_api.BirpayRequisitesListAPIView.as_view(), name='requisites-list'),
    path('requisites/<int:requisite_id>/', views_birpay_api.BirpayRequisiteUpdateAPIView.as_view(), name='requisite-update'),
    path('requisites/<int:requisite_id>/set-active/', views_birpay_api.BirpayRequisiteSetActiveAPIView.as_view(), name='requisite-set-active'),
    # Refill (пополнение) — find до <id>, иначе "find" попадёт в id
    path('refill-orders/', views_birpay_api.BirpayRefillOrdersListAPIView.as_view(), name='refill-orders-list'),
    path('refill-orders/find/', views_birpay_api.BirpayRefillOrderFindAPIView.as_view(), name='refill-order-find'),
    path('refill-orders/<int:refill_id>/amount/', views_birpay_api.BirpayRefillOrderAmountAPIView.as_view(), name='refill-order-amount'),
    path('refill-orders/<int:refill_id>/approve/', views_birpay_api.BirpayRefillOrderApproveAPIView.as_view(), name='refill-order-approve'),
    # Payout (выплаты)
    path('payout-orders/', views_birpay_api.BirpayPayoutOrdersListAPIView.as_view(), name='payout-orders-list'),
    path('payout-orders/find/', views_birpay_api.BirpayPayoutOrderFindAPIView.as_view(), name='payout-order-find'),
    path('payout-orders/<int:withdraw_id>/approve/', views_birpay_api.BirpayPayoutOrderApproveAPIView.as_view(), name='payout-order-approve'),
    path('payout-orders/<int:withdraw_id>/decline/', views_birpay_api.BirpayPayoutOrderDeclineAPIView.as_view(), name='payout-order-decline'),
]
