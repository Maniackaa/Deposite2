from django.conf.urls.static import static
from django.urls import path, include

from backend_deposit import settings
from . import views

app_name = 'payment'

urlpatterns = [

    # path('', views.index, name='index'),
    path('invoice/', views.invoice, name='pay_check'),
    path('pay_to_card_create/', views.pay_to_card_create, name='pay_to_card_create'),
    path('pay_to_m10_create/', views.pay_to_m10_create, name='pay_to_m10_create'),
    path('pay_result/<str:pk>/', views.PayResultView.as_view(), name='pay_result'),
    path('payments/', views.PaymentListView.as_view(), name='payment_list'),
    path('payments/<str:pk>/', views.PaymentEdit.as_view(), name='payment_edit'),

    path('payment_type_not_worked/', views.payment_type_not_worked, name='payment_type_not_worked'),


    # path('test/<str:pk>/', views.test, name='test'),
    # path('java/', views.java, name='java'),
    path('invoice_test_start/', views.invoice_test, name='invoice_test'),
    path('send_request/', views.send_request, name='send_request'),
    path('receive_request/', views.receive_request, name='receive_request'),
    ]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
