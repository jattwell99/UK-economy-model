"""
Admin registrations for the UK place engine (Phases 0-2).

The settings here are the "gotchas" section of the spec made concrete:
- autocomplete_fields on every self-referencing FK and every fact-table FK,
  so the admin never renders a <select> over thousands of rows.
- search_fields on each referenced model (required for autocomplete to work).
- list_filter / date_hierarchy / list_select_related on the fact table so the
  changelist stays fast and filterable.
"""

from django.contrib import admin

from .models import (
    Place,
    PlaceCrosswalk,
    IndicatorDomain,
    Indicator,
    Source,
    PlaceObservation,
    ActivityClass,
)


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ("name", "gss_code", "tier", "nation", "valid_from", "valid_to")
    list_filter = ("tier", "nation")
    search_fields = ("name", "gss_code")            # enables autocomplete elsewhere
    autocomplete_fields = ("parent",)
    ordering = ("tier", "name")


@admin.register(PlaceCrosswalk)
class PlaceCrosswalkAdmin(admin.ModelAdmin):
    list_display = ("from_place", "to_place", "basis", "weight")
    list_filter = ("basis",)
    autocomplete_fields = ("from_place", "to_place")
    search_fields = ("from_place__gss_code", "to_place__gss_code")
    list_select_related = ("from_place", "to_place")


@admin.register(IndicatorDomain)
class IndicatorDomainAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "parent")
    search_fields = ("name", "code")
    autocomplete_fields = ("parent",)


@admin.register(Indicator)
class IndicatorAdmin(admin.ModelAdmin):
    list_display = (
        "name", "code", "domain", "unit",
        "value_type", "is_additive", "subject_scope",
    )
    list_filter = ("domain", "value_type", "is_additive", "subject_scope")
    search_fields = ("name", "code")
    autocomplete_fields = ("domain",)


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("name", "publisher", "licence", "release_date")
    search_fields = ("name", "publisher")


@admin.register(PlaceObservation)
class PlaceObservationAdmin(admin.ModelAdmin):
    list_display = (
        "indicator", "place", "period_start",
        "value", "unit", "source", "vintage", "status",
    )
    list_filter = ("indicator__domain", "indicator", "status", "period_type", "source")
    date_hierarchy = "period_start"
    search_fields = ("place__name", "place__gss_code", "indicator__code")
    autocomplete_fields = ("indicator", "place", "source")
    list_select_related = ("indicator", "place", "source")


@admin.register(ActivityClass)
class ActivityClassAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "scheme", "level", "parent")
    list_filter = ("scheme", "level")
    search_fields = ("code", "name")
    autocomplete_fields = ("parent",)
