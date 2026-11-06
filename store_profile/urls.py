from django.urls import path

from . import views

urlpatterns = [
    path("profile", views.StoreProfileView.as_view()),
    path("settings", views.StoreSettingsView.as_view()),
    path("documents", views.KycDocumentView.as_view()),
]
