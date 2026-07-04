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
    # Choropleth map + its JSON endpoint (docs/map_timeslider_brief.md).
    path("map/", views.map_view, name="map"),
    path("api/choropleth/", views.choropleth_api, name="choropleth_api"),
    # Comparison-over-time tool (docs/comparison_tool_scoping_brief.md).
    path("compare/", views.compare_view, name="compare"),
    path("api/compare/", views.compare_api, name="compare_api"),
    path("api/compare/places/", views.compare_places_api, name="compare_places_api"),
]
