# UK place-centric research engine — V1 build sequence & data source review

**Companion to:** `uk_place_engine_v1_spec.md` (the schema). This document is the
*what fills it, from where, and in what order*. Two intertwined tracks: the
source catalogue (§5) says what data exists and how to get it; the build sequence
(§4) says the order to load it so dependencies resolve. **Grounding:** source
facts below were checked against live publisher pages in mid-2026. Endpoints,
cadences and dataset editions drift — re-verify each source's "latest release" at
build time.

---

## 1. Five cross-cutting realities that shape everything

**1.1 The UK is four statistical systems, not one — and it splits exactly where
outcomes live.** Economic and labour data is largely UK-wide (ONS GVA/GDP, Land
Registry house prices, Nomis). But health, education and deprivation are
*devolved*:
- Health: OHID Fingertips (England) vs Public Health Scotland, Public Health
  Wales / StatsWales, and DoH/NISRA in Northern Ireland.
- Deprivation: English IoD (2019, updated 2025), Welsh WIMD (2019), Scottish SIMD
  (2020), NI NIMDM (2017) — different geographies, domains, weights and years.
  The producers state outright deprivation **cannot be compared across nations**.
- Charities: Charity Commission (England & Wales) vs OSCR (Scotland) vs CCNI.

**Consequence:** `nation` must be a first-class, queryable attribute on Place
(derived from the GSS prefix: E/W/S/N/K). Never assume an indicator exists
UK-wide. Never create a single "UK deprivation" indicator — model each nation's
index as its own indicator and refuse to average them.

**1.2 Geography is the hardest part and must be built first, correctly.** Boundary
versions churn: the July 2024 Westminster set (650 seats) *replaced* the 2019 set;
local authorities have reorganised repeatedly (2019/2021/2023/2025); GSS codes
retire. `valid_from`/`valid_to` versioning and PlaceCrosswalk exist precisely for
this. Get it wrong and every observation is misfiled.

**1.3 Grain mismatch is the norm.** Primary grain is LAD. But deprivation
publishes at LSOA / Data Zone / SOA (below LAD); some health at MSOA; some
economic series at ITL (above LAD); vote share is native to constituency. The
catalogue states each source's native grain and the transform to land it at LAD
or WPC.

**1.4 Everything gets revised — this is why `vintage` is in the key.** ONS issued
an October 2025 correction to regional GVA volume estimates; NI GDP for 2023 was
withheld pending population estimates; Fingertips maintains a standing revisions
log and re-based deprivation deciles to IMD 2025. (indicator, place, period,
source, vintage) uniqueness is load-bearing, not pedantry.

**1.5 Registered office ≠ where an organisation operates.** Companies House
records a registered office, which for many companies is an accountant's or
formation agent's address (London-skewed). Treat the registered-office site as
`is_primary` for V1 but flag it; prefer multiple OrganisationSite rows once
trading-address data is available.

---

## 2. Access patterns (this, not the topic, drives the ingestion code)

**Keyless REST / query APIs** — Nomis; HM Land Registry SPARQL; postcodes.io
(postcode → LAD + 2024 constituency); ONS Open Geography Portal (ArcGIS REST /
WFS); OHID Fingertips; electionresults.parliament.uk.

**Free API key required** — Companies House Public Data API; Charity Commission
Register API (beta).

**Bulk file downloads** — Companies House monthly bulk + delta; Charity
Commission daily full-register extract; ONS dataset CSVs (GVA/GDP/GDHI);
IoD/devolved deprivation spreadsheets; HoC Library election CSV/XLSX; ONS Open
Geography lookup CSVs (the crosswalk seeds).

**Licensing.** Overwhelmingly OGL v3 (free reuse incl. commercial, attribution
required). Companies House / Charity Commission are free under their own terms;
officer/trustee names are personal data — handle under UK GDPR. ONS boundary
files carry combined OS + ONS IP under OGL.

---

## 3. The starter indicator set (seeded by `seed_v1 --dimensions`)

`additive` drives whether a value may be rolled up through the crosswalk.

| Indicator | Domain | Unit | value_type | additive | Native grain | Source |
|-----------|--------|------|-----------|----------|--------------|--------|
| GVA (balanced), total | Economy | £m | CURRENCY | yes | LAD | ONS |
| GVA per head | Economy | £ | RATIO | **no** | LAD | ONS |
| GDHI per head | Economy | £ | RATIO | **no** | LAD | ONS |
| Employment rate (16–64) | Labour | % | RATE | **no** | LAD | Nomis (APS) |
| Claimant count | Labour | count | COUNT | yes | LAD | Nomis |
| Median gross weekly pay (residence) | Labour | £ | RATIO | **no** | LAD | Nomis (ASHE) |
| Jobs density | Labour | ratio | RATIO | **no** | LAD | Nomis |
| Median house price | Housing | £ | RATIO | **no** | LAD | Land Registry HPI |
| Healthy life expectancy at birth | Health | years | RATIO | **no** | LAD | Fingertips (Eng) |
| Life expectancy at birth | Health | years | RATIO | **no** | LAD | Fingertips (Eng) |
| Share of LSOAs in most-deprived decile | Community | % | RATE | **no** | LAD (derived) | English IoD |
| Turnout | Civic | % | RATE | **no** | WPC | HoC Library |
| Winning-party vote share | Civic | % | RATE | **no** | WPC | HoC Library |
| Majority | Civic | count | COUNT | yes | WPC | HoC Library |

Note how few are additive. That is the point of the flag: almost everything
interesting is a rate or per-head figure that must **not** be summed across
places, and the crosswalk roll-up must refuse it.

---

## 4. Build sequence

### Phase 0 — Geography spine & crosswalk *(nothing works until this does)*
1. Load Place with the **LAD** set and the **Westminster constituency (July
   2024)** set from the ONS Open Geography Portal. Populate gss_code, name, tier,
   valid_from; derive nation from the code prefix.
2. Record boundary versions with valid_from/valid_to.
3. Seed PlaceCrosswalk from the ONS **"Ward to WPC to LAD to UTLA (July 2024)"**
   lookup. Use population (or household) best-fit weights; the scaffold ships an
   interim WARD_COUNT basis.
4. Stand up geocoding: postcodes.io (or ONSPD) for postcode → LAD + 2024
   constituency (needed in Phase 5).
5. Create Source rows as you wire each feed.

**Checkpoint:** you can resolve any GSS code to a versioned place, and translate a
value between a constituency and its overlapping LADs.

### Phase 1 — Dimensions
1. IndicatorDomain tree; 2. Indicator starter set (§3); 3. ActivityClass from
**SIC 2007** (scheme = "SIC-2007"); 4. the ~12 Source records from §5.
(`seed_v1 --dimensions --sic`, pass `--sic-csv` for the full division/group/class tree.)

### Phase 2 — First vertical: GVA end to end *(go/no-go)*
1. Ingest ONS regional GVA (balanced) by LAD, annual, into PlaceObservation. Set
   source, vintage (release edition), status.
2. Validate the V1 query: GVA trend for a chosen place.
3. Test the crosswalk: roll LAD total GVA up to a constituency using population
   weights — permitted because total GVA is additive; confirm the code **refuses**
   the same roll-up on GVA-per-head. (`core.aggregation.rollup_place_value`; see
   `core/tests/test_crosswalk_rollup.py`.)

**This is the checkpoint before breadth.**

### Phase 3 — Place observations: economy, labour, housing
Economy: GDHI per head; business demography (Nomis). Labour via **Nomis**:
employment rate, claimant count, ASHE median pay, jobs density (mind APS sample
caveats/suppression). Housing: **UK House Price Index** at LAD.

### Phase 4 — Outcomes: health, deprivation, education, civic
Health (England): OHID Fingertips. Deprivation: derive LAD-level indicators from
LSOA ranks (e.g. share in most-deprived decile) — never store raw ranks, never
merge nations. Education (England): DfE GIAS. Civic: election results are
constituency-native and attach to PlaceObservation at the WPC tier directly.

### Phase 5 — Organisation cluster
Organisation + OrganisationIdentifier from **Companies House** (bulk + API) and
**Charity Commission**. OrganisationClassification maps SIC codes to
ActivityClass (mark one primary). OrganisationSite via postcodes.io geocoding.
OrganisationObservation: charity financials are a clean first org source.

### Phase 6 — Refresh discipline & provenance
Idempotent upserts keyed on the observation composite key. A new release is a
**new vintage row**, never an overwrite. Respect each source's cadence.

---

## 5. Source catalogue (abridged)

- **ONS Open Geography Portal** — geography spine. Feeds Place, PlaceCrosswalk.
  ArcGIS REST / WFS + CSV/GeoJSON, keyless, OGL. Source of the July 2024
  constituency boundaries and the Ward→WPC→LAD→UTLA lookup.
- **ONS Postcode Directory / postcodes.io** — geocoding backbone. Feeds
  OrganisationSite. Keyless REST, OGL. Centroid-based (boundary-straddle caveat).
- **ONS Regional GVA / GDP (balanced)** — economic core. Feeds PlaceObservation.
  LAD + ITL. Annual, CSV/XLSX, OGL. Regularly restated → store vintage.
- **ONS GDHI** — disposable income. PlaceObservation. Annual CSV, OGL.
- **Nomis (ONS)** — labour-market workhorse; one API, many datasets. LAD, ward,
  **and constituency**. Free API, OGL. APS sample caveats; LFS/APS reweighting
  volatility 2023–24.
- **HM Land Registry — UK House Price Index** — headline house prices. LAD.
  Monthly (GB), quarterly (NI). SPARQL/CSV, keyless, OGL. UK-wide, unusually.
- **HM Land Registry — Price Paid Data** — transaction-level (E&W only). Prefer
  HPI at LAD for V1.
- **OHID Fingertips** — public health, **England only**. LAD (also MSOA/ICB/GP).
  JSON/CSV API, OGL. Values carry CIs; maintains a revisions log.
- **Deprivation** — four separate, non-comparable indices: English IoD (LSOA,
  2019/2025), Welsh WIMD (LSOA, 2019), Scottish SIMD (Data Zone, 2020), NI NIMDM
  (SOA, 2017). Derived LAD-level indicators; model per-nation, never merge.
- **House of Commons Library election results** — canonical results. WPC tier
  (turnout, vote share, majority, electorate). CSV/XLSX, event-driven,
  Parliamentary/OGL-style reuse.
- **Companies House Public Data API** — company register. Feeds Organisation,
  OrganisationIdentifier (COMPANIES_HOUSE), OrganisationClassification (SIC 2007),
  OrganisationSite. Free API + monthly bulk/delta. Officer/PSC names are personal
  data. Registered office ≠ operating site (§1.5).
- **Charity Commission (E&W)** — charity register. Feeds Organisation,
  OrganisationIdentifier (CHARITY_COMMISSION), OrganisationObservation (income,
  expenditure, employees). Free API (beta) + daily full extract. E&W only.
- **DfE GIAS + performance** — education orgs & outcomes (England).
- **NHS ODS** — health-body identifiers (OrganisationIdentifier, ODS).

---

## 6. Nation-coverage matrix

✓ = UK-wide single source; letters = separate national sources.

| Domain | England | Wales | Scotland | N. Ireland | Single UK source? |
|--------|---------|-------|----------|------------|-------------------|
| Geography / boundaries | ✓ | ✓ | ✓ | ✓ | Yes — ONS OGP |
| GVA / GDP / GDHI | ✓ | ✓ | ✓ | lag | Yes — ONS |
| Labour market | ✓ | ✓ | ✓ | ✓ | Yes — Nomis |
| House prices | ✓ | ✓ | ✓ | quarterly | Yes — LR UK HPI |
| Elections (Westminster) | ✓ | ✓ | ✓ | ✓ | Yes — HoC Library |
| Health | OHID | PHW | PHS | DoH/NISRA | **No** |
| Deprivation | IoD | WIMD | SIMD | NIMDM | **No (non-comparable)** |
| Charities | CC E&W | CC E&W | OSCR | CCNI | **No** |

Economic, labour, housing and electoral data is UK-wide; health, deprivation and
the charity register are per-nation.

---

## 7. Recommended V1 cut

- **Places:** England & Wales, LAD + WPC tiers, versioned, with the crosswalk.
- **Indicators:** the ~14 in §3, spanning economy, labour, housing, health,
  civic, deprivation.
- **Organisations:** Companies House (all) + Charity Commission (E&W), classified
  via SIC and the charity scheme, geocoded to LAD.
- **Deferred:** Scotland & NI outcome breadth, education and NHS org types,
  org-to-org relationships, geometry/PostGIS.

Prove Phase 2 (GVA end to end) before committing to the breadth in Phases 3–5.
