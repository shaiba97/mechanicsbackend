from django.urls import path

from . import views

app_name = "authentication"

urlpatterns = [
    path("", views.auth, name="login"),
    path("login", views.auth, name="login"),
    path("admin-login", views.admin_login),
    path("refresh", views.refresh),
    path("logout", views.logout),
    path("change-password", views.change_password),
    path("me", views.me),
    path("providers", views.providers),
    path("<provider>", views.oauth_redirect),
    path("<provider>/callback", views.oauth_callback),
]