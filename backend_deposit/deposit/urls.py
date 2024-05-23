from django.conf.urls.static import static
from django.urls import path, include

from backend_deposit import settings
from . import views, views_api

app_name = 'deposit'

urlpatterns = [
    # Главная страница
    # path('', views.index, name='index'),
    path('', views.home, name='index'),
    # path('deposit_confirm/<str:phone>/<int:pay>/', views.deposit_confirm, name='deposit_confirm'),
    # path('deposit_confirm/', views.deposit_confirm, name='confirm'),
    # path('index', views.index, name='index'),
    # path('deposits/', DepositList.as_view(), name='deposits'),
    # path(r'^page(?P<page>\d+)/$', DepositList.as_view(), name='deposits'),
    path('deposit_created/', views.deposit_created, name='created'),
    path('deposit_status/<str:uid>/', views.deposit_status, name='status'),

    path('deposits/', views.deposits_list, name='deposits'),
    path('deposits_pending/', views.deposits_list_pending, name='deposits_pending'),
    path('deposits/<int:pk>/', views.deposit_edit, name='deposit_edit'),

    path('screen/', views_api.screen, name='screen'),
    path('sms/', views_api.sms, name='sms'),
    path('sms_forwarder/', views_api.sms_forwarder, name='sms_forwarder'),

    path('incomings/', views.incoming_list, name='incomings'),
    path('incomings2/', views.incoming_list2, name='incomings2'),
    path('incomings/<int:pk>/', views.IncomingEdit.as_view(), name='incoming_edit'),

    path('incomings_empty/', views.IncomingEmpty.as_view(), name='incomings_empty'),
    path('incomings_filter/', views.IncomingFiltered.as_view(), name='incomings_filter'),
    path('my_filter/', views.my_filter, name='my_filter'),
    path('incomings_search/', views.IncomingSearch.as_view(), name='incomings_search'),
    path('bank_color/', views.ColorBankCreate.as_view(), name='bank_color'),

    path('get_posts/', views.get_last, name='get_last'),

    path('stats/', views.get_stats, name='stats'),
    path('stats_card/', views.get_stats, name='stats_card'),
    path('stats_day/', views.get_stats, name='stats_day'),
    path('stats_day2/', views.get_stats2, name='stats_day2'),
    path('trash/', views.IncomingTrashList.as_view(), name='trash'),
    path('graph/', views.day_graph, name='graph'),

    path('messages/<int:pk>/', views.MessageView.as_view(), name='message_view'),
    path('messages/', views.MessageListView.as_view(), name='messages'),
    ]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
