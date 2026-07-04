"""
Read-side query helpers for the explore surface (docs/explore_surface_v1_brief.md).

The one rule that matters here: observations are append-only by vintage, so a
(place, indicator, period) can have several rows. Everything user-facing shows
ONE series per indicator = the latest vintage per period. Never all vintages.
"""

from .models import Indicator, Place, PlaceObservation, PlaceTier


# Tiers that carry explorable observations (LAD economy/labour/housing, WPC civic).
EXPLORE_TIERS = [PlaceTier.LAD, PlaceTier.WPC]


# Indicators that do NOT cover the whole UK, so a place outside their nations shows a
# note instead of a silent blank. label = why; nations = GSS prefixes that DO have data.
# All of these are LAD-tier, so coverage notes are only surfaced on LAD places.
PARTIAL_COVERAGE = {
    "life-expectancy-birth-male": ("England only", {"E"}),
    "life-expectancy-birth-female": ("England only", {"E"}),
    "employment-rate-16-64": ("Great Britain only — no Northern Ireland", {"E", "W", "S"}),
    "median-weekly-pay": ("Great Britain only — no Northern Ireland", {"E", "W", "S"}),
}


def coverage_notes(place):
    """Partial-coverage indicators NOT available for this place's nation, with the reason.

    Only LAD places carry these indicators, so WPC (and any covered LAD) get no notes.
    """
    if place.tier != PlaceTier.LAD:
        return []
    missing = [code for code, (_lbl, nations) in PARTIAL_COVERAGE.items()
               if place.nation not in nations]
    if not missing:
        return []
    names = {i.code: i.name for i in Indicator.objects.filter(code__in=missing)}
    notes = [{"indicator": names.get(code, code), "note": PARTIAL_COVERAGE[code][0]}
             for code in missing if code in names]
    return sorted(notes, key=lambda n: n["indicator"])


def places_with_observations(search=None):
    """Places (LAD + WPC) with at least one observation, name-searchable, by name."""
    qs = (
        Place.objects.filter(tier__in=EXPLORE_TIERS, observations__isnull=False)
        .distinct()
        .order_by("name", "tier", "valid_from")
    )
    if search:
        qs = qs.filter(name__icontains=search)
    return qs


def resolve_place(gss_code, valid_from=None):
    """Resolve a GSS code to a single Place.

    Default = the latest boundary version. A few Scottish WPC codes (e.g. S14000021)
    exist in BOTH the 2010-review and 2023-review sets, so an optional valid_from
    picks a specific version and keeps the older seat reachable by URL.
    """
    qs = Place.objects.filter(tier__in=EXPLORE_TIERS, gss_code=gss_code)
    if valid_from is not None:
        return qs.filter(valid_from=valid_from).first()
    return qs.order_by("-valid_from").first()


def ambiguous_gss_codes(places):
    """GSS codes shared by more than one Place in `places` (need a versioned URL)."""
    seen, dupes = set(), set()
    for p in places:
        if p.gss_code in seen:
            dupes.add(p.gss_code)
        seen.add(p.gss_code)
    return dupes


def indicators_for_place(place):
    """Distinct indicators that have observations for this place."""
    return (
        Indicator.objects.filter(placeobservation__place=place)
        .distinct()
        .order_by("domain__code", "code")
    )


def latest_series(place, indicator):
    """One observation per period — the latest vintage wins.

    Postgres DISTINCT ON: order by the distinct field first, then -vintage so the
    row kept per period is the newest edition (see the brief).
    """
    return (
        PlaceObservation.objects.filter(place=place, indicator=indicator)
        .select_related("source", "indicator")
        .order_by("period_start", "-vintage")
        .distinct("period_start")
    )


def series_payload(place, indicator):
    """Chart-ready payload for one indicator: points + unit + provenance.

    provenance is the distinct (source, vintage) pairs actually shown — normally
    one, but surfaced honestly if a series spans editions.
    """
    rows = list(latest_series(place, indicator))
    points = [
        {"year": o.period_start.year, "value": float(o.value)}
        for o in rows
    ]
    provenance = []
    seen = set()
    for o in rows:
        key = (o.source.name, o.vintage)
        if key not in seen:
            seen.add(key)
            provenance.append({"source": o.source.name, "vintage": o.vintage})
    cov = PARTIAL_COVERAGE.get(indicator.code)
    return {
        "indicator_code": indicator.code,
        "indicator_name": indicator.name,
        "unit": indicator.unit,
        "points": points,
        "provenance": provenance,
        "coverage": cov[0] if cov else None,
    }
