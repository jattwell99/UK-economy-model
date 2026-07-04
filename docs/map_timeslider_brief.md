# Interactive map + time slider — build brief

Save at `docs/map_timeslider_brief.md`. Companion to the spec, build plan and explore
brief. This adds the first **spatial** view of the data: a choropleth of the UK where
colour encodes a chosen indicator, hover reveals values, a tier toggle switches LAD ↔
WPC, and a time slider steps through periods.

The multi-region **comparison-over-time** tool is a **separate** scoping pass — not in
this brief — because "comparable" is a design decision, not a widget (see §12).

## 1. Purpose
The model is place-shaped, but the explore surface only shows one place at a time.
A choropleth makes the *spatial* structure visible and is the natural home for the
tier switching the versioned-`Place` + crosswalk machinery was built for.

## 2. Architecture — fits the existing stack, no build step
- **Leaflet** from CDN with a GeoJSON layer (server-rendered page + a little JS, same
  spirit as the Chart.js pages). No SPA, no bundler. MapLibre GL is a later option if
  WPC (650 polygons) ever feels heavy — it won't at this scale, so start with Leaflet.
- A Django **JSON endpoint** returns `{gss_code: value}` for a chosen indicator/tier/
  period; the front end colours each polygon by looking its code up. The join key is
  the **GSS code** already on every `Place` and every observation.
- No PostGIS needed. This is a *display* map — geometry lives in static GeoJSON files,
  not the database. (PostGIS remains deferred; nothing here requires it.)
- No browser storage APIs — hold map state in JS variables only.

## 3. Geometry — sourcing, vintage, simplification
- **Source:** ONS Open Geography Portal (same place the geography spine was seeded
  from). Fetch the **generalised** boundary resolution (BGC / BUC), never full — full
  boundaries are enormous. If the portal is reachable from the environment (it was for
  the seed), the agent fetches; if blocked, I'll upload — same pattern as the data
  files.
- **Simplify further** with mapshaper (`-simplify` then `-o format=geojson`), reproject
  to **WGS84 (EPSG:4326)** for Leaflet, and keep each layer roughly **under ~1–2 MB**.
- **Bundle as static files** (e.g. `static/geo/lad.geojson`, `static/geo/wpc-2024.geojson`)
  and commit them — they're static; don't fetch at request time.
- Each feature must carry its **GSS code** property (`LAD**CD`, `PCON**CD`).
- **Vintage must match the Place spine / period (critical — see §8.3):**
  - LAD layer must match the **Dec-2019 LAD spine** the data is keyed to, or the ~7
    post-2019 unitaries drift (the same drift already logged on ingest).
  - WPC layer: **July-2024** set for 2024 results; the **2010-review** set for
    2015/17/19. V1 can ship LAD + WPC-2024 and add WPC-2010 with the historic view.

## 4. The choropleth endpoint
`GET /api/choropleth/?indicator=<code>&tier=<LAD|WPC>&period=<YYYY or YYYY-MM>`

Returns:
```
{
  "values": { "E07000223": 28150, ... },   # one value per place, LATEST VINTAGE for that period
  "unit": "£",
  "value_type": "RATIO",
  "is_additive": false,
  "scale": { "min": ..., "max": ... },      # for the legend
  "coverage": { "nations": ["E","W","S"], "note": "England only" | null },
  "no_data": ["N09000001", ...]             # places in-tier with no value this period
}
```
- **Reuse the latest-vintage-per-period logic** from the explore surface
  (`order_by(period_start, -vintage).distinct(period_start)` per place) — one value
  per place, never summed across vintages.
- Respect boundary versioning: resolve places by the vintage matching the selected
  tier/period (the date-window resolver from Phase 4b).

## 5. Interactions
- **Hover** a region → tooltip with region name, value + unit, period.
- **Tier toggle (LAD ↔ WPC):** swap the GeoJSON layer *and* the endpoint's `tier` — LAD
  polygons + LAD values, or WPC polygons + WPC values.
- **Time slider:** steps through the periods available for the chosen indicator,
  re-shading on change. Be `period_type`-aware — monthly series (HPI from 1995,
  claimant from 1986) have many steps, so step by **year** by default (or add a play
  button) rather than rendering hundreds of ticks.
- **Click a region → its detail** (reuse the existing place-detail view / `series_payload`
  in a side panel or link). For the 5 colliding Scottish WPC codes, the click must
  carry the boundary `valid_from` so it lands on the right version (the discriminator
  route from Phase 4b).

## 6. Indicator picker
- Offer indicators to colour by. But see §8.2 — **default the picker to normalised
  indicators** (rates, per-head, densities); additive raw totals need special handling.

## 7. Legend
- Sequential colour scale keyed to the indicator's range, labelled with the **unit**.
- A distinct **"no data"** swatch (grey / hatch), always present when any region lacks
  data (see §8.1).

## 8. The three things to get right (the honesty of the map)
Your data model makes each of these *easier* to do honestly than most — use its signals.

**8.1 Missing data must look missing, not zero.** The single biggest correctness item.
A region with no value (England-only indicator over Wales/Scotland/NI; NI on the GB-only
labour indicators; a place with a coverage gap) must render as a distinct **grey/hatch
"no data"** fill with a legend entry — **never** the light end of the colour scale,
which reads as "very low" and actively lies. This is the map equivalent of the coverage
notes. Drive it from the endpoint's `no_data` list.

**8.2 Colour a choropleth by rates, not totals.** A choropleth conflates magnitude with
area — a large region shaded dark looks like "more" partly because it's big. So encode
**normalised** indicators (per-head, rates, densities — your `is_additive = false`
ones). For additive **totals** (`is_additive = true`: GVA total, GDHI total, population,
majority), either **exclude them from the colour picker** or **flag clearly** that a
total-on-a-map is misleading and point to the per-head equivalent. This is the spatial
inverse of the "never sum a rate" rule: **never choropleth a total.** Use `is_additive`
to enforce it.

**8.3 Boundary vintage must match the period.** The WPC layer shown must match the
period's boundaries — 2024 set for 2024, 2010-review set for 2015/17/19 — or seats
won't line up with data. Same versioning problem already solved in the data; now solved
in the geometry.

## 9. Constraints
- No build step; Leaflet + static GeoJSON; endpoint reuses existing query logic.
- No new rankings/opinions: the map shades facts. No "best/worst" colouring language,
  no implied league table — a diverging "good/bad" palette would cross the line; use a
  neutral sequential scale.
- Push to **`ukmodel`** (deploy remote), not `origin`.

## 10. Definition of done
- A map page renders a choropleth for a chosen normalised indicator at LAD tier; hover
  shows region + value + unit.
- Tier toggle swaps geometry + data (LAD ↔ WPC).
- Time slider steps through available periods and re-shades.
- **Missing regions render as a distinct "no data" fill with a legend entry** — verified
  for an England-only indicator (Wales/Scotland/NI grey, not dark) and NI on a GB-only
  labour indicator.
- Additive totals are excluded from / flagged in the colour picker (never a plain
  choropleth of a total).
- Click a region → its detail (correct boundary version for the colliding Scottish
  codes).
- Boundary vintage matches the period/tier shown.
- Existing list/detail/LAD/WPC pages unregressed.
- Tests: endpoint returns correct value + latest vintage; `no_data` populated for a
  partial-coverage indicator; additive indicator flagged/excluded.

## 11. Suggested sequence
1. Geometry: fetch (or receive) LAD + WPC-2024 boundaries, simplify, reproject, bundle
   as static, verify GSS codes join to `Place`.
2. Endpoint: `/api/choropleth/` for one indicator at LAD, latest-vintage-per-period.
3. Base map: Leaflet + LAD GeoJSON, colour by the endpoint, hover tooltip, legend.
4. Missing-data honesty + non-additive handling (§8.1, §8.2) — do this before adding
   more indicators, so the honesty is baked in.
5. Tier toggle (add WPC layer + tier param).
6. Time slider.
7. Click-to-detail linking (with the version discriminator for colliding codes).

## 12. Out of scope (separate scoping pass)
- **Multi-region comparison-over-time tool.** Needs a decision on what "comparable"
  means (same tier + matching vintage; normalised metrics only; and/or a "similar
  places" peer grouping — which edges toward classification, a modelling choice). Scope
  that deliberately before building, same as the data-side design decisions.
- PostGIS, peer/"similar places" grouping, swipe/side-by-side comparisons.
