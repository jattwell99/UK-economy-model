"""Explore-surface routes. Mounted at the project root by config/urls.py."""

from django.urls import path

from . import views

app_name = "explore"

urlpatterns = [
    path("places/", views.place_list, name="place_list"),
    path("places/<str:gss_code>/", views.place_detail, name="place_detail"),
    # Versioned detail for GSS codes shared by two boundary sets (a few Scottish WPCs).
    path("places/<str:gss_code>/v/<str:valid_from>/", views.place_detail,
         name="place_detail_versioned"),
]
