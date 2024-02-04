from django.conf.urls.static import static
from django.urls import path, include

from backend_deposit import settings
from ocr import views

app_name = 'ocr'

urlpatterns = [
    # Главная страница
    path('screen_create/', views.ScreenCreateView.as_view(), name='screen_create'),
    path('screen_detail/<int:pk>/', views.ScreenListDetail.as_view(), name='screen_detail'),
    path('screen_list/', views.ScreenListView.as_view(), name='screen_list'),

    # path('', views.home, name='index'),
    ]


