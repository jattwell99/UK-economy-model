"""
Explore surface — the read layer (docs/explore_surface_v1_brief.md).

Two server-rendered routes over LAD place data. No rankings, no verdicts; every
chart carries its provenance. The vintage-safe series query lives in selectors.
"""

from django.http import Http404, JsonResponse
from django.shortcuts import render

from .models import PlaceTier
from .selectors import (
    ChoroplethError,
    ambiguous_gss_codes,
    choropleth_data,
    coverage_notes,
    indicators_for_place,
    mappable_indicators,
    places_with_observations,
    resolve_place,
    series_payload,
)


def place_list(request):
    """List places (LAD + WPC) with observations, with case-insensitive name search."""
    query = request.GET.get("q", "").strip()
    places = list(places_with_observations(query or None))
    # A few Scottish WPC codes exist in two boundary sets; flag them so the template
    # links to the versioned URL instead of the code-only one (which resolves newest).
    ambiguous = ambiguous_gss_codes(places)
    for p in places:
        p.needs_version = p.gss_code in ambiguous
    return render(
        request,
        "explore/place_list.html",
        {"places": places, "query": query},
    )


def place_detail(request, gss_code, valid_from=None):
    """Place header + one trend chart per indicator (latest vintage per period)."""
    place = resolve_place(gss_code, valid_from=valid_from)
    if place is None:
        raise Http404("No place with observations for that GSS code.")
    charts = [series_payload(place, ind) for ind in indicators_for_place(place)]
    return render(
        request,
        "explore/place_detail.html",
        {
            "place": place,
            "charts": charts,
            "indicator_count": len(charts),
            "coverage_notes": coverage_notes(place),
        },
    )


def map_view(request):
    """Choropleth map page (Leaflet from CDN + static GeoJSON, LAD + WPC tiers)."""
    return render(
        request,
        "explore/map.html",
        {
            "indicators_lad": mappable_indicators(PlaceTier.LAD),
            "indicators_wpc": mappable_indicators(PlaceTier.WPC),
        },
    )


def choropleth_api(request):
    """JSON per-place values for a choropleth (docs/map_timeslider_brief.md §4)."""
    indicator = request.GET.get("indicator")
    if not indicator:
        return JsonResponse({"error": "indicator is required."}, status=400)
    tier = request.GET.get("tier", PlaceTier.LAD)
    period = request.GET.get("period") or None
    try:
        return JsonResponse(choropleth_data(indicator, tier=tier, period=period))
    except ChoroplethError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)
