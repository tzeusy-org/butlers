# Chronicles Date-Navigable Archive

## Why

`/chronicles` is the retrospective "what was my day" surface, but today it can
only show one day: the page hardcodes `targetDate = yesterdayInTimeZone(ownerTz)`
(`frontend/src/pages/ChroniclesPage.tsx`) and exposes no way to pick another
date. A retrospective tool that cannot look at any day but yesterday is a
contradiction: the owner cannot answer "what was last Tuesday like?" at all.

Two further defects compound this:

1. **The recent-days index is dead UI.** `RecentDaysIndex` renders a list of
   prior days next to a view that cannot change days, and its own docstring
   admits "the index is read-only; clicking a row is not wired in v1." A
   control that advertises navigation and then refuses it is worse than
   omitting it.
2. **A latent backend correctness bug blocks safe date navigation.**
   `editorial._fetch_source_health_items` reads `datetime.now(UTC)` and the
   current adapter/checkpoint state, ignoring the requested `target` date. The
   moment the page lets the owner view an older day, that day would surface
   *today's* connector errors as if they belonged to it, and (because
   source-health severity is `high`) could mislabel a quiet historical day as
   `urgent`.

The backend already serves an arbitrary `?date=` to `/api/chronicler/briefing`
deterministically, with no LLM call and no cross-schema read, so this is mostly
a frontend change over an already-capable API, plus the one source-health fix.

## What Changes

- **Archive date navigation (frontend).** The editorial landing gains a
  prev/next day stepper in the date eyebrow and makes the recent-days index
  rows navigable. The selected day is held in URL state (`?date=YYYY-MM-DD`),
  so a day view is deep-linkable. The stepper is clamped to
  `[earliest_date, most-recent-settled-day]`: it cannot advance past yesterday
  (today is incomplete) nor step before the earliest chronicled day.
- **The day is the unit (frontend).** Selecting a day drives the whole page:
  the editorial briefing and the drilldown both reflect that single day. The
  redundant `TimeWindowPicker` and auto-refresh controls are removed from the
  Chronicles drilldown, which is always a settled past day and therefore
  static. The drilldown collapses behind a single disclosure affordance and
  lazy-mounts on expand, realizing the spec's existing "lazy-loaded on first
  interaction" intent.
- **Source-health attention is date-scoped (backend).**
  `editorial.compose_briefing_payload` only includes `source_health` attention
  items for the most recent settled day (or today). `_fetch_source_health_items`
  and `compose_briefing_payload` take an injectable `now` for deterministic
  classification. Anomalies and open-corrections remain per-day as before.
- **Bounded archive (backend, additive).** `ChroniclesBriefing` gains an
  `earliest_date` field (the earliest chronicled calendar day in owner tz, or
  null) so the frontend can bound backward navigation.
- **Editorial polish (frontend).** The greeting line is date-relative (it no
  longer says "Yesterday" when viewing an older day); the briefing-source pill
  is demoted to a quiet stale-only indicator; the attention list leads the
  index rail; and the "Top lane" KPI cell stops packing a label into the
  numeric slot.

No manifesto change. Chronicler stays retrospective, deterministic per-event,
and LLM-free on this surface: viewing any past date reuses the existing
day-close Tier-2 cache or the templated fallback. No new endpoints, no schema
change.

## Capabilities

### Modified Capabilities

- `dashboard-chronicles`: the editorial landing becomes a date-navigable
  retrospective archive. The recent-days index becomes a navigation control;
  the day selection is URL state and drives both the briefing and the
  drilldown. The drilldown is static (a settled day) and disclosed on demand.
  The briefing/attention contract gains date-scoped source-health and an
  `earliest_date` bound. Auto-refresh is removed from this surface.

## Impact

- **Backend:** `src/butlers/chronicler/editorial.py` (source-health gating,
  injectable `now`, `earliest_date`); `roster/chronicler/api/models.py`
  (`ChroniclesBriefing.earliest_date`); `roster/chronicler/api/router.py`
  (map `earliest_date` into the response). No new endpoints, no migration.
- **Frontend:** `frontend/src/pages/ChroniclesPage.tsx` (URL date state,
  stepper, demoted pill, reordered rail, date-relative greet, KPI fix);
  `frontend/src/components/chronicles/RecentDaysIndex.tsx` (navigable rows);
  `frontend/src/components/chronicles/ChroniclesDrilldownPanel.tsx`
  (day-driven window, disclosure, removed time-window/auto-refresh controls);
  `frontend/src/api/types.ts` (`earliest_date`). `TimeWindowPicker` is removed
  from Chronicles and deleted if it becomes unused.
- **No LLM call paths added.** Date navigation reuses the existing
  cached/templated `voice_paragraph` path.
- **Specs touched:** `dashboard-chronicles` MODIFIED.
