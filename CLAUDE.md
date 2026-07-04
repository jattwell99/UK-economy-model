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
  views.py/urls.py Explore surface: /places/ list + /places/<gss_code>/ detail
  templates/explore/  Server-rendered list + detail (Chart.js from cdnjs)
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

## Confirm, don't assume

- ONS ArcGIS FeatureServer URLs and CSV column names drift between editions —
  verify against the live source before trusting an ingest run.
- Crosswalk weights are interim (ward-count) until population best-fit weights
  from LSOA/OA lookups are wired in.
- The UK is four statistical systems. `nation` is first-class on Place. Never
  create a single UK-wide deprivation indicator — model each nation separately.

## Tests worth writing early (all present in `core/tests/`)

- Crosswalk roll-up: additive indicators sum; non-additive ones raise, not sum.
- Place versioning: 2019 and 2024 constituency sets coexist as distinct rows.
- Observation uniqueness: same place/period/indicator with a different vintage =
  two rows; identical = rejected by the constraint.

Run them with `python manage.py test core`.

## Working style

Prefer small, verifiable steps and paste-ready output. Flag errors and dubious
assumptions directly. Engage as a peer.
