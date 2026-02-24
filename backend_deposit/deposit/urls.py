from django.conf.urls.static import static
from django.urls import path, include

from backend_deposit import settings
from . import views, views_api

app_name = 'deposit'

urlpatterns = [
    # Главная страница
    path('', views.incoming_list, name='incomings'),
    path('index/', views.incoming_list, name='index'),  # Алиас для совместимости
    # path('', views.home, name='index'),
    # path('deposit_confirm/<str:phone>/<int:pay>/', views.deposit_confirm, name='deposit_confirm'),
    # path('deposit_confirm/', views.deposit_confirm, name='confirm'),
    # path('deposits/', DepositList.as_view(), name='deposits'),
    # path(r'^page(?P<page>\d+)/$', DepositList.as_view(), name='deposits'),
    # path('deposit_created/', views.deposit_created, name='created'),
    # path('deposit_status/<str:uid>/', views.deposit_status, name='status'),
    #
    # path('deposits/', views.deposits_list, name='deposits'),
    # path('deposits_pending/', views.deposits_list_pending, name='deposits_pending'),
    # path('deposits/<int:pk>/', views.deposit_edit, name='deposit_edit'),

    path('screen/', views_api.screen_new, name='screen'),
    # path('screen_new/', views_api.screen_new, name='screen_new'),
    path('sms/', views_api.sms, name='sms'),
    path('sms_forwarder/', views_api.sms_forwarder, name='sms_forwarder'),

    path('incomings/', views.incoming_list, name='incomings'),
    path('incomings/<int:pk>/', views.IncomingEdit.as_view(), name='incoming_edit'),

    path('incomings_empty/', views.IncomingEmpty.as_view(), name='incomings_empty'),
    path('incomings_filter/', views.IncomingFiltered.as_view(), name='incomings_filter'),
    path('incoming_my_filter/', views.IncomingMyCardsView.as_view(), name='incoming_my_filter'),
    path('my_filter/', views.my_filter, name='my_filter'),
    path('incomings_search/', views.IncomingSearch.as_view(), name='incomings_search'),
    path('incoming-stat/', views.IncomingStatSearchView.as_view(), name='incoming_stat_search'),
    path('incoming_checks/', views.IncomingCheckList.as_view(), name='incoming_checks'),
    path('incoming_recheck/<int:pk>/', views.incoming_recheck, name='incoming_recheck'),
    path('bank_color/', views.ColorBankCreate.as_view(), name='bank_color'),

    path('get_posts/', views.get_last, name='get_last'),

    path('stats/', views.get_stats, name='stats'),
    path('stats_card/', views.get_stats, name='stats_card'),
    path('stats_day/', views.get_stats, name='stats_day'),
    path('stats_day2/', views.get_stats2, name='stats_day2'),
    path('trash/', views.IncomingTrashList.as_view(), name='trash'),
    path('graph/', views.day_graph, name='graph'),
    path('operator_speed_graph/', views.operator_speed_graph, name='operator_speed_graph'),

    path('messages/<int:pk>/', views.MessageView.as_view(), name='message_view'),
    path('messages/', views.MessageListView.as_view(), name='messages'),

    path('check_sms/', views.check_sms, name='check_sms'),
    path('check_screen/', views.check_screen, name='check_screen'),
    path('iframe/', views.iframe_view, name='iframe_view'),
    path('iframe-proxy/', views.iframe_proxy_view, name='iframe_proxy'),

    # Работа с um и asu-pay
    # path('test_transactions/', views.test_transactions, name='test_transactions'),
    path('asu-webhook/', views.WebhookReceive.as_view(), name='asu-webhook'),
    path('asu-withdraw-webhook/', views.WithdrawWebhookReceive.as_view(), name='asu-withdraw-webhook'),
    path('bkash-webhook/', views.BkashWebhook.as_view(), name='bkash-webhook'),

    # path('withdraw_test/', views.withdraw_test, name='withdraw_test'),

    path('moshennik_list/', views.moshennik_list, name='moshennik_list'),
    path('painter_list/', views.painter_list, name='painter_list'),
    path('birpay_orders/', views.BirpayOrderView.as_view(), name='birpay_orders'),
    path('birpay_orders/raw/<int:birpay_id>/', views.BirpayOrderRawView.as_view(), name='birpay_order_raw'),
    path('birpay_orders/info/<int:birpay_id>/', views.BirpayOrderInfoView.as_view(), name='birpay_order_info'),
    path('birpay_panel/', views.BirpayPanelView.as_view(), name='birpay_panel'),
    # path('birpay_my_filter/', views.BirpayMyFilterView.as_view(), name='birpay_my_filter'),
    path('assigned_cards/', views.assign_cards_to_user, name='assign_cards_to_user'),
    path('show_birpay_order_log/<str:query_string>/', views.show_birpay_order_log, name='show_birpay_order_log'),
    # path('test/', views.test, name='test'),
    path('users_stat/', views.BirpayUserStatView.as_view(), name='users_stat'),
    path('incomings/mark_as_jail/<int:pk>/', views.mark_as_jail, name='mark_as_jail'),
    path('api/incoming_balance_info/<int:incoming_id>/', views.get_incoming_balance_info, name='get_incoming_balance_info'),
    path('api/birpay-orders/', views_api.BirpayOrderListAPIView.as_view(), name='birpay_orders_api'),
    path('requisite-zajon/', views.RequsiteZajonListView.as_view(), name='requisite_zajon_list'),
    path('requisite-zajon/logs/', views.RequsiteZajonChangeLogListView.as_view(), name='requisite_zajon_change_logs'),
    path('requisite-zajon/<int:pk>/', views.RequsiteZajonUpdateView.as_view(), name='requisite_zajon_edit'),
    path('requisite-zajon/<int:pk>/toggle-active/', views.RequsiteZajonToggleActiveView.as_view(), name='requisite_zajon_toggle_active'),
    
    # Тестовая страница для создания BirpayOrder (только для суперюзера)
    path('birpay_orders/create/', views.BirpayOrderCreateView.as_view(), name='birpay_order_create'),
    
    # Страница управления Z-ASU (только для суперюзера)
    path('z-asu-management/', views.ZASUManagementView.as_view(), name='z_asu_management'),
    # Проверка статуса заявки на birpay-gate по Merchant Tx ID
    path('birpay-gate-status-check/', views.BirpayGateStatusCheckView.as_view(), name='birpay_gate_status_check'),
    # Поиск данных по Merchant Tx ID (xlsx → BirpayOrder → xlsx)
    path('merchant-tx-id-search/', views.MerchantTxIdSearchView.as_view(), name='merchant_tx_id_search'),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
