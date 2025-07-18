from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth.views import PasswordChangeDoneView
from django.contrib.auth.views import PasswordChangeView
from django.contrib.auth.views import PasswordResetConfirmView
from django.contrib.auth.views import PasswordResetCompleteView
from django.contrib.auth.views import PasswordResetDoneView
from django.contrib.auth.views import PasswordResetView
from django.urls import path, reverse_lazy
from . import views

app_name = 'users'

urlpatterns = [
    path('logout/', LogoutView.as_view(template_name='users/logged_out.html'),
         name='logout'),
    path('signup/', views.SignUp.as_view(), name='signup'),
    path('login/', LoginView.as_view(template_name='users/login.html'),
         name='login'),

    path('password_change/',
         PasswordChangeView.as_view(
             template_name='users/password_change_form.html',
             success_url='done/'),
         name='password_change'),

    path('password_change/done/',
         PasswordChangeDoneView.as_view(
             template_name='users/password_change_done.html'),
         name='password_change_done'),


    path('password_reset/',
         PasswordResetView.as_view(
                 template_name="users/password_reset_form.html",
                 email_template_name='users/password_reset_email.html',
                 success_url=reverse_lazy('users:password_reset_done')),
         name='password_reset'),

    path('password_reset/done/',
         PasswordResetDoneView.as_view(
             template_name='users/password_reset_done.html'),
         name='password_reset_done'),

    path('reset/<uidb64>/<token>/',
         PasswordResetConfirmView.as_view(
             template_name='users/password_reset_confirm.html',
             success_url=reverse_lazy("users:password_reset_complete")
         ),
         name='password_reset_confirm'),

    path('reset/done/',
         PasswordResetCompleteView.as_view(
             template_name='users/password_reset_complete.html'),
         name='password_reset_complete'),
    path('toggle_option/<str:value>/', views.toggle_option, name='toggle_option'),
]

# path('reset_password/', auth_views.PasswordResetView.as_view(
#     template_name="users/password_reset_form.html",
#     email_template_name='users/password_reset_email.html',
#     success_url=reverse_lazy('users:password_reset_done')
# ),