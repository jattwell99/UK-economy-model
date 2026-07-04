# LAD-vintage refresh — scoping brief

Save at `docs/lad_vintage_refresh_scoping_brief.md`. A **scoping** brief: its job is to
size the problem and recommend an approach BEFORE any code. The output is a proposal and
a recommended path, not a migration.

## 1. Why this, why now
The spine is fixed at the **December 2019 LAD set (382 places)**. Several sources now
publish on newer boundaries, so on every ingest ~30 places silently drop (logged, not
errored). This is the one issue **degrading every indicator** rather than adding a new
one, and it compounds: each new source published on current geography inherits the loss.
The point of this pass is to find out whether fixing it is a contained remap or a
spine-and-crosswalk ripple — that answer decides whether it comes before or after the
queued quick wins (affordability, age structure).

## 2. The core distinction to characterise
Not all "drift" is the same, and the two kinds need very different handling. **Classify
every drifted place into one of these**, with counts:

**(a) Pure recodes — same real place, new GSS code.** e.g. Barnsley/Sheffield metros
recoded (E08 range shifted). The place is unchanged on the ground; only its identifier
moved. Fix = a **code remap**: teach the spine the new code maps to the same `Place`, so
new-geography observations attach and old ones stay valid. Low-risk.

**(b) Restructures — real boundary change, places merged/created.** e.g. several districts
abolished and merged into one new unitary (Buckinghamshire, North Yorkshire, Somerset,
Cumbria → Cumberland + Westmorland & Furness, Northamptonshire → North/West Northants).
Here the *geography itself* changed: old districts no longer exist, a new unitary covers
their area. This is NOT a remap — it's a genuine new `Place` with a new boundary, and the
old districts should be versioned out (valid_to) the way the WPC boundary change was
handled. Higher-risk, touches versioning and possibly historic data.

The whole sizing question turns on the (a)/(b) split. Recodes are tidy; restructures are
the WPC-boundary-versioning pattern again, at LAD tier.

## 3. Questions the scoping must answer (show me, with numbers)
1. **The (a)/(b) breakdown:** exactly which drifted codes are pure recodes vs genuine
   restructures, with counts. Name the restructure groups (which old districts → which
   new unitary).
2. **Historic-data impact:** are any EXISTING observations keyed to codes that would
   move or be superseded? GVA/GDHI/population/HPI/etc. were ingested on the Dec-2019
   spine — do any of them use codes that the refresh would recode or retire? If a place
   is restructured, what happens to its historic pre-restructure observations (they're
   real data for a real former area — presumably kept, versioned, not deleted)?
3. **Crosswalk impact:** the WPC↔LAD crosswalk is 2024-WPC↔LAD only. Does refreshing the
   LAD spine break or require rebuilding it? Which weights/mappings are affected?
4. **Boundary geometry:** the map uses `static/geo/lad.geojson` at Dec-2019. A spine
   refresh means the geometry must move in lockstep (same discipline as the WPC eras) —
   is a matching newer LAD boundary file available, and does the tier/period handling
   need the same version-awareness the WPC layers got?
5. **Versioning model:** does the existing `Place` versioning (valid_from/valid_to,
   already used for WPC eras) handle restructured LADs as-is, or does it need extension?

## 4. The approach options to weigh
Lay these out with a recommendation, don't pre-pick:

- **Option A — recodes only (minimal).** Remap the pure-recode cases so those places
  stop dropping; leave genuine restructures for later. Fixes the tidy majority cheaply,
  defers the hard part. Honest partial fix.
- **Option B — full refresh (recodes + restructures) to the current LAD set.** Bring the
  spine to current boundaries: remap recodes, version-in the new unitaries (valid_from),
  version-out the abolished districts (valid_to), keeping their historic observations as
  the versioned-out area's data. The complete fix; the WPC-era pattern applied at LAD.
- **Option C — defer entirely, document the loss.** If it turns out bigger than the
  quick wins are worth right now, quantify exactly what's lost and when, and schedule it
  as its own phase.

## 5. Non-negotiables (whatever the approach)
- **Append-only / no data destruction.** A restructured-out district's historic
  observations are real data for a real former area — version it out, never delete.
  Same discipline as retiring the old WPC boundary set.
- **Geometry moves with the spine.** If the LAD spine gains current boundaries, the map's
  LAD layer/period handling must stay honest (right shapes for the right era), exactly as
  the WPC 2010/2024 layers do.
- **No silent anything.** The current failure is silent drop-on-ingest; the fix must not
  replace it with a different silent behaviour. Unmatched should still be loud.

## 6. Definition of done (for the SCOPING pass)
- The (a)/(b) breakdown with counts and named restructure groups.
- The historic-data, crosswalk, geometry, and versioning impacts, each answered concretely.
- A recommended option (A/B/C) with reasoning, and an honest size estimate ("contained
  remap" vs "spine-and-crosswalk phase").
- THEN STOP for my decision. No migration, no spine changes, no commits beyond scratch
  cleanup.

## 7. Why it's scoped before built
"Refresh the LAD spine" could be a tidy remap (Option A territory) or a
versioning-and-crosswalk ripple (Option B). That size decides whether it goes BEFORE the
queued quick wins (affordability, age structure) — fix the foundation first — or gets
scheduled as its own phase while a quick win goes first. Size it, then choose.
