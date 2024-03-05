from django.conf.urls.static import static
from django.urls import path, include

from backend_deposit import settings
from ocr import views, views_api

app_name = 'ocr'

urlpatterns = [
    # Главная страница
    path('screen_create/', views.ScreenCreateView.as_view(), name='screen_create'),
    path('screen_detail/<int:pk>/', views.ScreenListDetail.as_view(), name='screen_detail'),
    path('screen_list/', views.ScreenListView.as_view(), name='screen_list'),
    path('create_screen/', views_api.create_screen, name='create_screen'),
    path('reponse_screen/', views_api.response_screen, name='response_screen'),
    path('response_screen_atb/', views_api.response_screen_atb, name='response_screen_atb'),
    path('response_bank1/', views_api.response_bank1, name='response_bank1'),
    path('receive_pay/', views_api.receive_pay, name='receive_pay'),
    # path('', views.home, name='index'),
    ]


