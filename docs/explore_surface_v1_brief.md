# Explore surface — V1 build brief

Save at `docs/explore_surface_v1_brief.md`. Companion to the spec and build plan.

## Purpose
The first user-facing read layer. For any UK place, show its indicators trending
over time. This is the "explore the data" half of the V1 goal made visible — it
**reads** what the admin manages. Explore the data: no rankings, scores, deciles,
or verdicts anywhere.

## Scope (V1 — keep tight)
- **LAD place pages only.** That's where all current indicators live natively
  (gva-balanced-total, gva-per-head, population), so no apportionment is needed.
- **Two routes:**
  - `/places/` — list of LADs that have observations, searchable by name.
  - `/places/<gss_code>/` — detail: place header + one trend chart per indicator.
- **Deferred (not this session):** constituency pages, cross-place comparison or
  ranking, maps, auth.

## The read query — get this exactly right
Observations are append-only by vintage, so a `(place, indicator, period)` can
have more than one row. For display, show **one series per indicator = the latest
vintage per period.** On Postgres:

```python
series = (PlaceObservation.objects
          .filter(place=place, indicator=indicator)
          .order_by("period_start", "-vintage")
          .distinct("period_start"))   # one row per period, max vintage wins
```

Never plot all vintages — it double-plots points. This is the **same root cause**
as the `core/aggregation.py` roll-up double-count flagged earlier: while you're in
query logic, fix `rollup_place_value` too so it picks the latest vintage per
`from_place`-period (or requires an explicit vintage) rather than `Sum`-ing across
all vintages.

## Rendering
- **Server-rendered Django templates + Chart.js from CDN** (cdnjs). No SPA, no
  build step — consistent with the out-of-the-box-Django stack.
- One line chart per indicator (a small-multiples grid reads well). X = period
  (year), Y = value. Use the indicator's `unit` on the Y axis.
- **Provenance is required on every chart.** Label each with its source name and
  vintage, e.g. "ONS Regional accounts · vintage 2019-12-19". Every number
  traceable to its edition is the civic-rigour principle — surface it, don't hide
  it. Where a chart is a derived indicator (per-head), show the derived source and
  its combined vintage string.

## Constraints — the no-opinions line
- No rankings, deciles, red/amber/green, "good/bad" colouring, or verdicts.
  Neutral trend lines only.
- A single **national median** context line per chart is acceptable (it's context,
  not a ranking) — but keep it clearly labelled and skip it if it adds real
  complexity this session.
- **Non-additive indicators are shown only for the geography they're natively
  measured at.** Never fabricate a rolled-up per-head or rate. (Not an issue while
  scope is LAD-only, but state it so it survives into the constituency phase.)

## Place list / search (`/places/`)
- List LADs with at least one observation: name + GSS code, linking to detail.
- Simple case-insensitive name search (`name__icontains`). Order by name.

## Place header (`/places/<gss_code>/`)
Name, tier, nation, GSS code, `valid_from`/`valid_to`, and a count of indicators
available for the place.

## Definition of done
- Both routes work; the detail page renders a trend chart per indicator, each with
  a provenance label.
- Display uses **latest-vintage-per-period** — one clean series per indicator.
- No rankings or judgments anywhere in the output.
- A test asserting the series selector returns exactly one point per period even
  when two vintages of the same `(place, indicator, period)` exist.
- `core/aggregation.py` roll-up no longer double-counts across vintages (add/extend
  a test).

## Explicitly out of scope this session
Phase 3 ingesters (GDHI, Nomis, HPI), constituency roll-up pages, maps, auth.
One surface, done well — then stop.
