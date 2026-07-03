# Phase 3 breadth — build brief (economy · labour · housing)

Save at `docs/phase3_breadth_brief.md`. Companion to the spec, build plan and
source catalogue. This fills the **"outcomes trending"** half of V1: place
observations for housing, economy and the labour market. Most target indicators
are already in the seed.

Source facts below were checked against live publisher pages in mid-2026 —
re-confirm the current release/edition and column headers at build time.

## Sequence — one source per session
1. **Housing — UK House Price Index** (cleanest file drop; introduces MONTH periods)
2. **Economy — GDHI** (a near-clone of the GVA ingester)
3. **Labour — Nomis** (the different animal: a live API, sampled data — do last)

Do them in this order and finish each before starting the next.

## Cross-cutting rules (every ingester)
- **Prerequisite:** the `aggregation.py` vintage double-count fix must be in
  **before** HPI, because monthly data generates many vintages fast. Confirm it
  landed in the explore-surface work; if not, fix first.
- **`period_type` is not always annual.** HPI = `MONTH`; GDHI = `CALENDAR_YEAR`;
  Nomis varies (claimant count monthly, APS/ASHE/jobs-density annual). Never
  default to calendar year.
- **`is_additive`:** only true totals sum (GDHI total). Everything per-head, rate,
  average or index is **non-additive** (HPI average price, employment rate, pay,
  jobs density, GDHI per head). The roll-up must refuse them.
- **Match** the source's geography/area code (GSS) to `Place` (LAD, latest
  version). Report unmatched codes — boundary churn is expected.
- **Provenance** on every row: `source` + `vintage`. A new edition is new rows.
- All three sources are **UK-wide** — no nation fragmentation this phase.

## Seed changes needed first
- **Add `gdhi-total`** (domain economy, unit £m, `CURRENCY`, additive, PLACE) — not
  currently seeded.
- **Rename `median-house-price` → `average-house-price`** ("Average house price
  (UK HPI)"). The UK HPI publishes a mix-adjusted **average**, not a median, so the
  seeded name is wrong for this source. `RATIO`, non-additive, unit £.
- Confirm already seeded and reuse as-is: `claimant-count`, `employment-rate-16-64`,
  `median-weekly-pay`, `jobs-density`, `gdhi-per-head`.
- Update `seed_v1` INDICATORS so seed and DB stay aligned.

---

## Source 1 — Housing: UK House Price Index
- **Publisher/licence:** HM Land Registry (for ONS / Registers of Scotland / LPS NI).
  UK-wide, National Statistic, OGL.
- **Get it:** CSV from GOV.UK "UK House Price Index: data downloads <Month Year>"
  (the full dataset CSV, or the per-attribute "Average price" CSV), or the SPARQL /
  linked-data API at `landregistry.data.gov.uk`. Keyless.
  ref: https://www.gov.uk/government/collections/uk-house-price-index-reports
- **Grain / cadence:** LAD (plus region/country). Monthly; **NI quarterly**; from 1995.
- **Columns (average-price CSV):** `Date`, `RegionName`, `AreaCode` (GSS),
  `AveragePrice`, `Index`, monthly/annual change, `SalesVolume`.
- **Feeds:** `average-house-price` (value = `AveragePrice`, £, non-additive).
  Optionally `SalesVolume` as a second indicator (COUNT, additive) if you want it.
- **Period:** `MONTH` — `period_start` = 1st of month, `period_end` = month end.
- **Vintage:** the HPI edition (e.g. `2026-05`). Revision period is 13 months, so
  reloading later brings new vintages — exactly what the design expects.
- **Caveats:** low-transaction LADs are volatile (analyse long-term, not
  month-to-month); NI is quarterly at LAD.

## Source 2 — Economy: GDHI
- **Dataset:** ONS "Regional gross disposable household income: local authorities by
  ITL1 region" — the **same ONS workbook template as your GVA files**, so the GVA
  parser is ~90% reusable.
  ref: https://www.ons.gov.uk/economy/regionalaccounts/grossdisposablehouseholdincome/datasets/regionalgrossdisposablehouseholdincomelocalauthoritiesbyitl1region
- **Grain / cadence:** LAD (~361 on 2024 boundaries), annual, 1997–latest, UK-wide.
- **Feeds two indicators, and note the asymmetry with GVA:**
  - `gdhi-total` (£m, additive) — the summable total.
  - `gdhi-per-head` (£, non-additive) — **published directly in the file, so ingest
    it, do NOT derive it.** (Unlike `gva-per-head`, which you had to compute because
    the GVA files only carry totals. Here ONS gives you per-head, so take theirs —
    single-source provenance, cleaner.)
- **Period:** `CALENDAR_YEAR`. Latest year is **provisional** → set
  `status = PROVISIONAL`. Vintage = release date (e.g. `2025-09-10`).
- Reuse the GVA parser; point it at the total-£m table for `gdhi-total` and the
  per-head table for `gdhi-per-head`.

## Source 3 — Labour: Nomis API
- **The different animal:** a live REST API, not a spreadsheet.
- **Base pattern:** `https://www.nomisweb.co.uk/api/v01/dataset/{ID}.data.csv?geography=...&measures=...&time=...`
- **Auth:** register a free API key (raises rate limits); store as env var
  `NOMIS_API_KEY`, pass as `&uid=`. ref: https://www.nomisweb.co.uk
- **Resolve dataset IDs live, don't hardcode:** query the catalogue at
  `https://www.nomisweb.co.uk/api/v01/dataset/def.sdmx.json?search=*keyword*`
  and the geography/measures concepts per dataset. IDs look like `NM_x_y` and do
  change; confirm against the live catalogue. Datasets to wire:
  - **Claimant count** → `claimant-count`. Monthly (`MONTH`). A COUNT, additive.
    (Designated experimental statistics — note it.)
  - **APS employment rate 16–64** → `employment-rate-16-64`. Annual rolling; RATE,
    non-additive. Model-based; carries confidence intervals.
  - **ASHE median gross weekly pay (residence)** → `median-weekly-pay`. Annual
    (April pay period); RATIO, non-additive. 1% employee sample.
  - **Jobs density** → `jobs-density`. Annual; RATIO, non-additive.
- **Geography:** request the Nomis LAD geography TYPE; the code in the response is
  the GSS code → match `Place`. Nomis can also serve **parliamentary constituency**
  geography directly, which is how you'd get labour data at WPC tier without
  apportionment (see out-of-scope note).
- **Handle:** skip suppressed / non-numeric cells; confidence intervals may be
  ignored for V1 (or stored later); `period_type` per dataset; vintage = the
  dataset's latest release tag or the pull date.

## Definition of done (per source)
- Observations landing for that source's indicators across most LADs and the
  available periods, with correct `period_type` and `is_additive`.
- Provenance on every row; unmatched codes reported, not dropped silently.
- Non-additive indicators refuse crosswalk roll-up (existing test should cover the
  new ones — add cases).
- Spot check in admin: e.g. a place's house-price series is monthly and trends
  sensibly; GDHI per-head is in a plausible £ range.

## Out of scope this phase
Phase 4 outcomes (health, deprivation, civic) and Phase 5 organisations. Do **not**
attempt constituency roll-ups of the non-additive housing/labour indicators — they
can't be apportioned. If you want them at constituency, pull Nomis at constituency
geography directly instead.
