"""
Read-side query helpers for the explore surface (docs/explore_surface_v1_brief.md).

The one rule that matters here: observations are append-only by vintage, so a
(place, indicator, period) can have several rows. Everything user-facing shows
ONE series per indicator = the latest vintage per period. Never all vintages.
"""

from collections import Counter
from datetime import date

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
    "imd-most-deprived-decile-share-england": ("England only", {"E"}),
    "imd-average-score-england": ("England only", {"E"}),
}


# Short, factual "what this measures" notes for indicators whose meaning isn't obvious
# from the name. Descriptive only — never a ranking or a "which is worse" judgment.
INDICATOR_DESCRIPTORS = {
    "imd-most-deprived-decile-share-england":
        "Share of the area's neighbourhoods (LSOAs) in England's most-deprived 10% — "
        "measures how concentrated the most extreme deprivation is.",
    "imd-average-score-england":
        "Population-weighted average of the area's neighbourhood deprivation scores — "
        "measures the overall level across the whole area.",
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
        "descriptor": INDICATOR_DESCRIPTORS.get(indicator.code),
    }


# ---------------------------------------------------------------------------
# Choropleth map (docs/map_timeslider_brief.md)
# ---------------------------------------------------------------------------

class ChoroplethError(Exception):
    """Bad choropleth request the view turns into a 4xx (unknown indicator / a total)."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def mappable_indicators(tier=PlaceTier.LAD):
    """Indicators offerable in the colour picker: NON-additive (never choropleth a
    total, §8.2) and actually observed at this tier."""
    return [
        {"code": i.code, "name": i.name, "unit": i.unit}
        for i in Indicator.objects.filter(
            is_additive=False, placeobservation__place__tier=tier,
        ).distinct().order_by("domain__code", "code")
    ]


def _quantile_breaks(values, n_classes=6):
    """Quantile class boundaries for a choropleth: n_classes+1 increasing edges.

    Quantile (equal-count) classing gives spatial contrast that a linear scale loses
    to extreme outliers (City of London's GVA-per-head). The edges are ACTUAL DATA
    VALUES, so the legend can label each band with its real range — the colour never
    hides a magnitude difference. Collapses duplicate edges (skewed / few-value data)
    so bands stay meaningful, which may yield fewer than n_classes bands.
    """
    if not values:
        return []
    s = sorted(values)
    edges = [s[round(k / n_classes * (len(s) - 1))] for k in range(n_classes + 1)]
    out = [edges[0]]
    for e in edges[1:]:
        if e > out[-1]:
            out.append(e)
    return out if len(out) > 1 else [s[0], s[-1] if s[-1] > s[0] else s[0] + 1]


# WPC boundary versions -> the static geometry layer they map to (docs §8.3).
_WPC_BOUNDARY_LAYER = {
    date(2024, 7, 4): "wpc-2024",   # 2023-review set (GE2024)
    date(2010, 5, 6): "wpc-2010",   # 2010-review set (GE2015/17/19)
}


def _layer_key(tier, boundary_from):
    """Which static GeoJSON layer matches this tier + boundary version."""
    if tier == PlaceTier.LAD:
        return "lad"
    if tier == PlaceTier.WPC:
        return _WPC_BOUNDARY_LAYER.get(boundary_from, "wpc-2024")
    return None


def available_years(indicator, tier):
    """Sorted distinct period years for an indicator across ALL boundary versions.

    Drives the time slider (brief §5). Spans both WPC boundary eras so a constituency
    indicator exposes 2015/17/19 AND 2024. A single entry => a static, single-point
    indicator (IMD, a 2024-only seat) — the client hides the slider rather than fake
    motion.
    """
    years = (PlaceObservation.objects
             .filter(indicator=indicator, place__tier=tier)
             .dates("period_start", "year", order="ASC"))
    return [str(d.year) for d in years]


def _resolve_period(qs, period):
    """Narrow a queryset to a single period and return (qs, resolved_label).

    period = 'YYYY-MM' (monthly), 'YYYY' (that year), or None (latest available).
    """
    if period:
        parts = period.split("-")
        qs = qs.filter(period_start__year=int(parts[0]))
        if len(parts) > 1:
            qs = qs.filter(period_start__month=int(parts[1]))
        return qs, period
    latest = qs.order_by("-period_start").values_list("period_start", flat=True).first()
    if latest is None:
        return qs.none(), None
    return qs.filter(period_start__year=latest.year), str(latest.year)


def choropleth_data(indicator_code, tier=PlaceTier.LAD, period=None):
    """Per-place values for a choropleth: latest vintage for the chosen period.

    Honesty (brief §8): additive totals are refused (never choropleth a total); the
    no_data list is EVERY in-tier place lacking a value this period — that folds in
    both nation-level absence (England-only over W/S/N) and within-coverage holes
    (English LADs where HPI/LE don't reach). Missing must look missing, not zero.
    """
    try:
        indicator = Indicator.objects.get(code=indicator_code)
    except Indicator.DoesNotExist:
        raise ChoroplethError(f"Unknown indicator {indicator_code!r}.", status=404)
    if indicator.is_additive:
        raise ChoroplethError(
            f"{indicator_code!r} is an additive total — a choropleth of a total is "
            f"misleading (see §8.2). Use its per-head / rate equivalent.", status=400)

    base = PlaceObservation.objects.filter(indicator=indicator, place__tier=tier)
    qs, resolved = _resolve_period(base, period)

    # One value per place: newest period_start within the window, then newest vintage.
    # (order by place_id, not the "place" FK, which would expand to Place.Meta.ordering
    # and break DISTINCT ON.)
    rows = (qs.select_related("place")
              .order_by("place_id", "-period_start", "-vintage")
              .distinct("place_id"))
    values, vfroms = {}, Counter()
    for o in rows:
        values[o.place.gss_code] = float(o.value)
        vfroms[o.place.valid_from] += 1

    # Period-driven boundary resolver (brief §8.3): observations for one period all sit
    # on a single boundary version (2015/17/19 on the 2010-review seats, 2024 on the
    # 2023-review seats — the years never overlap), so the matched places' valid_from
    # tells us which boundary set — and therefore which geometry layer — this period
    # belongs to. The universe (for no_data) and the layer both follow from it, so a
    # 2019 period paints 2019 values onto 2010-review shapes, never 2024 ones.
    boundary_from = vfroms.most_common(1)[0][0] if vfroms else None
    if boundary_from is not None:
        universe = set(Place.objects.filter(
            tier=tier, valid_from=boundary_from).values_list("gss_code", flat=True))
    else:
        universe = set(Place.objects.filter(tier=tier).values_list("gss_code", flat=True))
    no_data = sorted(universe - set(values))

    nums = list(values.values())
    scale = {"min": min(nums), "max": max(nums)} if nums else {"min": None, "max": None}
    breaks = _quantile_breaks(nums)   # quantile edges (actual data values) for the legend
    cov = PARTIAL_COVERAGE.get(indicator.code)
    coverage = {
        "nations": sorted(cov[1]) if cov else None,   # null = UK-wide
        "note": cov[0] if cov else None,
    }
    return {
        "indicator": indicator.code,
        "tier": tier,
        "period": resolved,
        "periods": available_years(indicator, tier),   # slider ticks (one => static)
        "layer": _layer_key(tier, boundary_from),       # geometry to match this period
        "values": values,
        "unit": indicator.unit,
        "value_type": indicator.value_type,
        "is_additive": indicator.is_additive,
        "scale": scale,
        "breaks": breaks,   # quantile band edges; band i covers [breaks[i], breaks[i+1])
        "coverage": coverage,
        "no_data": no_data,
    }
