from django.urls import path
from . import views

urlpatterns = [
    path('login/',          views.login_view,             name='login'),
    path('logout/',         views.logout_view,            name='logout'),
    path('me/',             views.me_view,                name='me'),
    path('password-reset/', views.password_reset_request, name='password-reset-request'),
    path('register/', views.register_view, name='register'),
    path('password-reset/confirm/', views.password_reset_confirm, name='password-reset-confirm'),
]
