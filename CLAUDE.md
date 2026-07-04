# CLAUDE.md — UK place-centric research engine

## What this is

A personal research engine modelling UK economic activity and societal outcomes
by place, built to civic-tool rigour. V1 goal: for any UK place, show economic
activity and how outcomes are trending. **Explore-the-data — no rankings, scores,
or opinions in the data layer.**

## Source of truth

- `docs/uk_place_engine_v1_spec.md` — the data model. Authoritative for tables,
  fields, keys, constraints.
- `docs/uk_place_engine_v1_build_plan.md` — build sequence and data-source catalogue.
- If code and docs disagree, either the docs win or you update the docs **in the
  same change**. Never let them drift.

## Stack (decided — don't re-litigate without asking)

- Django + Django admin as the V1 data-management surface.
- **PostgreSQL from day one, not sqlite.** The observation uniqueness design
  depends on Postgres NULL-in-unique-constraint semantics, and PostGIS comes
  later. Run locally via docker-compose (`docker-compose up -d db`).
- Python 3.12+ (dev container here is 3.11; keep code 3.11-compatible), a single
  Django app: `core`.
- Ingestion = Django management commands, one per source, following the existing
  `seed_v1` pattern (`core/management/commands/`).

## Layout

```
config/            Django project (settings read from env; see .env.example)
core/
  models.py        Place, PlaceCrosswalk, IndicatorDomain, Indicator, Source,
                   PlaceObservation, ActivityClass (Phases 0-2)
  admin.py         Admin with the autocomplete / list_select_related gotchas applied
  aggregation.py   Crosswalk roll-up gated by Indicator.is_additive (latest vintage per period)
  selectors.py     Read-side queries for the explore surface (latest-vintage-per-period series)
  views.py/urls.py Explore surface: /places/ list + /places/<gss_code>/ detail;
                   /map/ choropleth + /api/choropleth/ JSON endpoint
  templates/explore/  Server-rendered list + detail + map (Chart.js / Leaflet from cdnjs)
  static/geo/      Bundled generalised boundary GeoJSON for the map (lad.geojson)
  management/commands/
    seed_v1.py       Dimensions, SIC tree, geography (LAD Dec-2019 + WPC), crosswalk
    ingest_gva.py    ONS regional GVA (balanced) by LAD -> PlaceObservation (Phase 2)
    ingest_population.py  ONS mid-year LAD population estimates (Phase 3)
    derive_per_head.py    gva-per-head = GVA total x 1e6 / population (Phase 3)
    ingest_hpi.py    UK House Price Index (average price) by LAD, monthly (breadth)
    ingest_gdhi.py   ONS GDHI total £m + per-head £ by LAD, annual (breadth)
    ingest_nomis.py  Nomis API: claimant count, employment rate, pay, jobs density
    ingest_elections.py   HoC Library GE results by constituency -> WPC, 2015-2024
                          (Phase 4a: 2024; 4b: 2015/2017/2019 on old boundaries)
    ingest_fingertips.py  OHID Fingertips life expectancy at birth (England, LAD),
                          by sex, 3-year pooled (Phase 4c health)
    ingest_imd.py    English IoD 2019 -> LAD: decile-share + pop-weighted score
                          (Phase 4c deprivation, England only, both metrics)
    bootstrap_seed.py     Idempotent per-dataset self-seed on deploy (bundled seed_data/)
  tests/           Early guarantees + GVA, population/per-head, HPI and elections verticals
docs/              Spec + build plan (source of truth)
```

## Build order — one phase per session, do not skip ahead

1. Geography spine + crosswalk (Place, PlaceCrosswalk), seeded from ONS.
2. Dimensions (IndicatorDomain, Indicator, Source) + the SIC ActivityClass tree.
3. **First vertical:** ingest ONS regional GVA at LAD into PlaceObservation;
   prove the trend query and a crosswalk roll-up to constituency. GO/NO-GO gate.
4. Place breadth: economy, labour (Nomis), housing (Land Registry HPI).
5. Outcomes: health (Fingertips), deprivation, civic (election results at WPC tier).
6. Organisation cluster: Companies House + Charity Commission.

Each phase ends in: migration applied + a test + something verifiable in admin.

**Status:** Phases 0-3 are in place and verified against Postgres:
- 0-2: schema, admin, seed scaffold, early tests; geography loaded (382 LADs
  Dec-2019 + 650 WPCs); GVA balanced total ingested (7,980 obs, vintage
  2019-12-19), trend + crosswalk roll-up proven (the go/no-go gate).
- 3: ONS LAD population estimates ingested (8,162 obs); `gva-per-head` derived
  from GVA total / population (7,749 obs) against a "Derived" source with a
  dual-input vintage. `population` is additive; `gva-per-head` is not.
- Explore surface (read layer, docs/explore_surface_v1_brief.md): `/places/` list
  + name search and `/places/<gss_code>/` detail with one trend chart per
  indicator (latest vintage per period) and a provenance label. No rankings.
  `rollup_place_value` and the display query both pick latest-vintage-per-period,
  never summing/plotting across vintages.

Breadth (docs/phase3_breadth_brief.md), one source per session:
- Housing (Source 1, done): UK House Price Index average price ingested at LAD,
  MONTH periods (`ingest_hpi`, vintage = HPI edition e.g. 2026-04). The seeded
  `median-house-price` was renamed to `average-house-price` (UK HPI is a
  mix-adjusted average, not a median; migration 0002 + seed_v1). Non-additive.
- Economy GDHI (Source 2, done): ONS "GDHI local authorities by ITL1 region"
  (12 bundled workbooks, 1997-2022). `gdhi-total` (£m, additive, seeded) from
  Table 1 and `gdhi-per-head` (£, non-additive) from Table 3 — per head is ONS's
  own published figure, NOT derived. CALENDAR_YEAR, latest year PROVISIONAL,
  vintage 2024-09-04. Sheets picked by title (not number).
- Labour market (Source 3, done): Nomis live API (`ingest_nomis`), current LAD
  geography TYPE424, full history, paginated (Nomis caps 25k rows/response).
  claimant-count (NM_162_1, MONTH, additive), employment-rate-16-64 (NM_17_5
  var=45 — the RATE lives in NM_17_5 not NM_17_1, CALENDAR/rolling), median-weekly-pay
  (NM_30_1 ASHE, annual), jobs-density (NM_57_1, annual). Suppressed cells skipped;
  vintage = pull date; NOMIS_API_KEY passed as &uid= when set (keyless works).
- bootstrap_seed loads each dataset independently (existence check); fetches HPI +
  Nomis over HTTPS, so the live DB gains new sources on deploy without a manual load.
  Phase 3 complete: the explore surface carries 10 indicators per place.

Phase 4a (civic — 2024 GE at WPC tier, done): HoC Library results-by-constituency
CSV (`ingest_elections --path`), bundled in `seed_data/elections/` because the
deploy can't reach parliament.uk (WAF blocks datacentre IPs — Railway too). Matches
ONS ID -> WPC Place (July-2024 set); 650/650 matched, 0 unmatched. Three indicators
under a `civic` domain, all POINT period at the election date (2024-07-04), source
"House of Commons Library — elections", vintage GE2024:
- `turnout` (%, non-additive) = (valid + invalid votes) / electorate × 100. This is
  the OFFICIAL turnout (all ballots cast, not just valid) — matches published figures.
- `winning-party-vote-share` (%, non-additive) = winner votes / valid votes × 100.
  Winner votes read from the `First party` column's vote count, falling back to
  `Of which other winner` when the winner isn't one of the tracked parties (Ind /
  Speaker / minor). No party dimension in V1 — party name kept only to locate the count.
- `majority` (count, additive) = the published Majority column.
This is the FIRST WPC-tier data. `selectors.EXPLORE_TIERS` now covers LAD + WPC (was
LAD-only), so a constituency renders on both the explore list (with a tier chip) and
the detail page. Crosswalk roll-up honours the flags: majority sums, turnout/share refuse.

Phase 4b (historic election series — 2015/2017/2019 at WPC tier, done): the same
`ingest_elections`, extended to the HoC files for GE2015 (CBP-7186), GE2017 (CBP-7979)
and GE2019 (CBP-8749), all bundled in `seed_data/elections/`. These three elections used
the SAME 2010-review boundary set (650 seats), DIFFERENT from the July-2024 set, so they
load as a SECOND versioned batch of WPC Places (valid_from 2010-05-06, valid_to
2024-07-03) created idempotently from the file. Key correctness points:
- Boundary versioning: results attach to the Place whose `[valid_from, valid_to]` window
  contains the election date — an **election-date-window resolver**, NOT the old
  "latest version wins" (which would misattribute historic results to 2024 seats).
- Code reuse verified, not assumed: England/Wales/NI codes are disjoint across the two
  sets, but **5 Scottish codes appear in both** (S14000021/27/45/48/51 — seats the 2023
  review left unchanged). They coexist as distinct Place rows via the `(gss_code,
  valid_from)` unique constraint. To keep the older seats reachable, `resolve_place`
  takes an optional `valid_from`, there's a `places/<gss>/v/<valid_from>/` route, and the
  explore list links the ambiguous ones (only) to the versioned URL (`ambiguous_gss_codes`).
- Winner-vote location adapts per file: party columns are read from each header (UKIP in
  2015/2017, BRX in 2019, RUK in 2024); winner = named `First party` column → else
  `Of which other winner` → else (2015, which lacks that column) Majority + runner-up
  (`Second party`) votes. turnout basis is identical every year — all files carry
  "Invalid votes", so `(valid+invalid)/electorate` throughout (no fallback needed).
- Honest separation (no stitching): the 3 historic elections form a trend on the
  OLD-boundary Place; the 2024 seat keeps its single dot. Cross-boundary vote
  apportionment is deliberately out of scope. WPC explore entries carry an era hint
  (e.g. "2010–2024" vs "2024–"). Old-boundary WPC↔LAD crosswalks are not built (the
  crosswalk remains 2024-only). Phase 4b complete: 4 elections, 7,800 civic obs.

Phase 4c (health — life expectancy at birth, England, done): OHID Fingertips API
(`ingest_fingertips`, fetched live — Fingertips is reachable, no bundled file). England
only: English observations of a UK-wide-methodology indicator go on the shared indicator
codes; other nations join later from NRS/NISRA/PHW. Key shape decisions from the spike:
- Life expectancy at birth (Fingertips 90366) is published BY SEX ONLY — there is no
  "Persons" figure and averaging M/F is wrong. So the seeded `life-expectancy-birth` was
  SPLIT (migration 0003) into `life-expectancy-birth-male` and `life-expectancy-birth-female`.
- 3-year POOLED series (`"2001 - 03"` → period_start 2001, period_end 2003), not the
  volatile single years. Area type 301 ("Districts & UAs 2020/21"), closest to our
  Dec-2019 spine; Buckinghamshire UA (E06000060) is unmatched (Bucks is a 2020 unitary;
  our spine has the 4 old districts) — logged, like the Nomis post-2019 unitaries.
- Category Type filtered to the headline (blank) — Fingertips also carries a within-area
  LSOA-deprivation-decile breakdown we don't want. ~14k obs, England only.
- **Healthy life expectancy at birth (90362) is DEFERRED, not shipped:** Fingertips
  publishes it at UPPER-tier (County/UTLA) only, and we do not model that geography. It
  stays seeded but unpopulated. This is the FIRST time LAD-only has cost us data — a real
  spine gap (see "Confirm, don't assume"), not just this indicator's problem.
- Explore surface: partial-coverage indicators now carry a coverage note. Covered charts
  show a caption ("England only"); on a place OUTSIDE the coverage the detail page lists
  the absent indicators with the reason (`selectors.PARTIAL_COVERAGE` / `coverage_notes`)
  instead of a silent blank — applied to LE (England only) AND the pre-existing GB-only
  `employment-rate-16-64` / `median-weekly-pay` (no NI). Notes are LAD-only (these
  indicators don't live at WPC).

Phase 4c (deprivation — English IoD 2019, done): `ingest_imd` fetches IoD 2019
"File 7" live from gov.uk (reachable — no upload). One file carries LSOA code, LAD
code+name (2019), IMD Score, IMD Decile, and mid-2015 population, so no separate
LSOA→LAD lookup or population file is needed. LSOAs are aggregated through to LAD (they
are NOT Place rows); raw LSOA ranks are never stored. 317/317 English LADs matched, 0
unmatched. Deprivation is per-nation and NEVER merged UK-wide — these are England-only
codes; devolved nations join later from their own indices. TWO metrics kept side by
side (a deliberate no-ranking choice — dropping one would editorialise about which kind
of deprivation counts):
- `imd-most-deprived-decile-share-england` (%, RATE, non-additive) = share of the LAD's
  LSOAs in England's most-deprived national decile. Ranking-derived; measures
  concentration of extreme deprivation. Saturates at 0 for 123/317 LADs — a reason not
  to rely on it alone, hence keeping both.
- `imd-average-score-england` (score, INDEX, non-additive) = population-weighted mean of
  LSOA IMD scores. Cardinal; measures overall level; discriminates across the full range.
The seeded `imd-most-deprived-decile-share` was renamed to the England code and the score
indicator added (migration 0004). POINT period at the edition date (2019-09-26), vintage
IoD2019; the 2025 update loads later as a new vintage + period. Blackpool tops the score
(uniformly deprived) while Middlesbrough tops the decile-share (most LSOAs in the worst
decile) — the divergence proving the two aren't redundant. Explore surface: each IMD
chart carries a short factual descriptor (concentration vs overall level, no judgment)
plus the England-only coverage note. This completes the England outcomes work.

Map — choropleth (docs/map_timeslider_brief.md), steps 1-3 done (LAD base map):
- Geometry: ONS UGCB (ultra-generalised clipped) Dec-2019 LAD, matching the spine
  vintage; simplified with mapshaper to WGS84, `static/geo/lad.geojson` (~260KB, props
  gss_code+name). GSS join to Place is 382/382 both ways (no drift — geometry and spine
  share the Dec-2019 vintage). `STATICFILES_DIRS` added so project-root static/ is served.
- Endpoint `GET /api/choropleth/?indicator=&tier=LAD&period=` (`selectors.choropleth_data`)
  returns the brief's §4 shape (values / unit / value_type / is_additive / scale /
  coverage / no_data). Reuses latest-vintage-per-period (order by place_id, -period_start,
  -vintage, distinct place_id — NOT the "place" FK, which expands to Place.Meta ordering
  and breaks DISTINCT ON). period = YYYY / YYYY-MM / latest.
- Honesty baked in from the start (§8): `no_data` = every in-tier place lacking a value
  this period, folding in BOTH nation-absence (England-only over W/S/N, from
  PARTIAL_COVERAGE) AND within-England holes (HPI/LE don't reach every LAD) — the map
  renders these as a distinct grey, NEVER the light end of the scale. Additive totals are
  refused (400) and excluded from the picker (`mappable_indicators`) — never choropleth a
  total. Neutral sequential palette (Blues), no diverging good/bad.
- Leaflet from cdnjs (SRI-pinned), no build step. gva-per-head verified: City of London
  £7.9M/head is the high-outlier tell.
- Colour scale is QUANTILE-classed (`_quantile_breaks`, returned as `breaks` = actual
  data-value edges), not linear — a linear scale put 368/369 LADs in the palest band
  (City of London compresses everything); quantile gives ~62 LADs/band. The legend
  labels each band with its REAL value range, so the top band (`32,864 – 7,937,859`)
  exposes the outlier's magnitude rather than hiding it. Neutral sequential; no-data grey
  stays outside the classing.
- Tier toggle (LAD ↔ WPC, done): WPC-2024 layer `static/geo/wpc-2024.geojson` (UGCB
  July-2024, 650/650 GSS join). The toggle swaps geometry + the endpoint `tier` param and
  rebuilds the picker per tier (civic turnout/vote-share at WPC — majority excluded as
  additive; economy/health/etc at LAD). The endpoint scopes to the CURRENT boundary set
  (`valid_to IS NULL`) so the 5 codes shared across WPC eras don't collide (S14000021
  resolves to its 2024 value) — the time slider will swap this for a per-period
  date-window resolver + the 2010 WPC layer.
- Time slider (step 6, done): driven by the ACTUAL periods for the selected indicator
  (`available_years`, returned as `periods` on each response), stepped BY YEAR — monthly
  series (HPI) collapse to year ticks via `.dates("period_start","year")`, not hundreds
  of ticks. SINGLE-PERIOD indicators (IMD's one 2019 point) hide the slider and label the
  period static — no faked motion. A play button auto-advances.
- Historic WPC boundaries move WITH the period (§8.3): the current-boundary scoping was
  replaced by a per-period resolver. Observations for one period all sit on one boundary
  version (2015/17/19 on the 2010-review seats, 2024 on the 2023-review seats — years
  never overlap), so the matched places' `valid_from` determines both the universe and
  the geometry `layer` returned. The client swaps `static/geo/wpc-2010.geojson` ↔
  `wpc-2024.geojson` as the slider crosses 2024. 2010 layer = ONS Dec-2021 UGCB (the
  2010-review boundaries were stable 2010-2023), 650/650 GSS join to the old WPC Places.
  The 5 colliding Scottish codes resolve to the OLD seat's value on a historic period and
  the NEW seat's on 2024 — no cross-era bleed.
- NOTE: `claimant-count` is additive (a count total), so it is NOT choropleth-able (§8.2
  refuses it, 400) — the brief's slider example named it, but the monthly-stepping path is
  verified with `average-house-price` (monthly, non-additive) instead.
- Click-to-detail (step 7, done — the map is now complete): clicking a region navigates
  to its existing place-detail page (reuses `series_payload`; no new query machinery). The
  click carries the boundary `valid_from` of the layer ON SCREEN (the endpoint returns it
  as `boundary`), so WPC uses `/places/<gss>/v/<valid_from>/` — the 5 colliding Scottish
  codes open the OLD seat on a historic period and the NEW seat on 2024 (never newest-wins
  guessing); LAD uses the plain `/places/<gss>/`. The map feature (steps 1-7) is complete.
- The multi-region comparison-over-time tool remains a SEPARATE scoping pass (brief §12):
  "comparable" is a design decision (same tier + matching vintage, normalised only, and/or
  a peer-grouping that edges toward classification) — scope before building.

Organisation cluster models (Organisation, OrganisationIdentifier,
OrganisationSite, OrganisationClassification, OrganisationObservation) are
specified in the spec but deliberately not yet added — they belong to a later
session per the build order.

## Conventions

- Surrogate PKs everywhere; natural keys are unique + indexed fields.
- Every FK in admin uses `autocomplete_fields`; fact tables get `list_filter`,
  `date_hierarchy`, and `list_select_related`.
- Observations are append-only by vintage: a new release is a **new row**, never
  an overwrite.
- `is_additive` gates crosswalk roll-ups. Summing a rate/ratio/per-head across
  places is always wrong — `core.aggregation.rollup_place_value` refuses it.

## Deployment & environment gotchas (rediscovered too often — read this)

- **The deploy remote is `ukmodel` (github.com/jattwell99/UK-economy-model), NOT
  `origin`.** Railway builds from `ukmodel/main`. Shipping = push the branch to
  `ukmodel` and fast-forward `ukmodel/main` there. `origin`
  (`jattwell99/first-repository`) is a separate scaffold repo and has UNRELATED git
  history (no common ancestor) — pushing there does NOT deploy anything.
- **The commit-signing stop-hook warning is expected noise — ignore it.** This
  environment has no SSH signing key, so GitHub marks commits "Unverified"; the
  committer email (`noreply@anthropic.com`) is already correct. Do not amend/rebase
  to chase it.
- Live site: https://web-production-f8c36.up.railway.app — reachable over HTTPS to
  verify after a deploy. The container can reach gov.uk / ONS / Nomis but NOT
  parliament.uk (WAF 403) and NOT the Railway DB ports (egress blocked), which is why
  election files are bundled in `seed_data/` and loaded by `bootstrap_seed` on deploy.

## Confirm, don't assume

- ONS ArcGIS FeatureServer URLs and CSV column names drift between editions —
  verify against the live source before trusting an ingest run.
- Crosswalk weights are interim (ward-count) until population best-fit weights
  from LSOA/OA lookups are wired in.
- The UK is four statistical systems. `nation` is first-class on Place. Never
  create a single UK-wide deprivation indicator — model each nation separately.
- **Nation coverage is NOT uniformly UK-wide, even now.** As of Phase 4b: GVA, GDHI,
  population, HPI, claimant-count, jobs-density and all civic indicators cover E/W/S/N;
  but `employment-rate-16-64` and `median-weekly-pay` (both Nomis APS/ASHE) have **no
  Northern Ireland** data (NI labour stats come from NISRA, not these Nomis geographies)
  — they are GB-only. Check per-indicator coverage before assuming a place has a value.
- Geography-vintage drift: the LAD spine is the **Dec-2019** set (382). Nomis reports
  ~7 post-2019 unitaries (Cumberland, Westmorland & Furness, North Yorkshire, North/West
  Northamptonshire, Buckinghamshire, Somerset) that have no matching Place, so their rows
  are dropped on ingest (logged as "unmatched"). Refreshing the LAD vintage is future work.
- **No upper-tier (County / UTLA) geography yet — and it now costs us data.** The spine
  is LAD + WPC only. Healthy life expectancy at birth (and other OHID indicators) is
  published at County/UTLA, not LAD, so it can't be ingested until an upper-tier tier is
  modelled (`PlaceTier` already reserves REGION/ITL slots; a County/UTLA tier + an
  LAD→UTLA lookup is the missing piece). This is the first indicator blocked purely on the
  spine, not the source — worth revisiting before broadening the health/outcomes picture.

## Tests worth writing early (all present in `core/tests/`)

- Crosswalk roll-up: additive indicators sum; non-additive ones raise, not sum.
- Place versioning: 2019 and 2024 constituency sets coexist as distinct rows.
- Observation uniqueness: same place/period/indicator with a different vintage =
  two rows; identical = rejected by the constraint.

Run them with `python manage.py test core`.

## Working style

Prefer small, verifiable steps and paste-ready output. Flag errors and dubious
assumptions directly. Engage as a peer.
