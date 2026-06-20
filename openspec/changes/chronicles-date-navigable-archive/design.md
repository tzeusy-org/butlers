# Design: Chronicles Date-Navigable Archive

## Decision 1 — The day is the single unit of navigation

Chronicles is a *retrospective archive*: the owner picks one settled day and
the whole page reconstructs it. The previous surface had two independent date
sources (the editorial header fixed to yesterday; the drilldown's
`useTimeWindow` defaulting to today), which read as two clocks on one page.

The redesign collapses these to one selected day, held in URL state and
clamped to settled days. This directly resolves the decoupling and makes the
recent-days index meaningful (clicking a day re-anchors the page).

Multi-day range analytics (the old `TimeWindowPicker` presets / custom range)
is intentionally **out of scope** for this change. It is a different
(analytics) use case; folding it back in would re-introduce the dual-control
ambiguity. Noted under Out of scope.

## Decision 2 — Date lives in the URL (`?date=YYYY-MM-DD`)

The page reads `?date=` via `useSearchParams` and falls back to the most
recent settled day (yesterday in owner tz) when absent. Benefits: deep-linkable
day views, browser-shareable, and trivially testable (assert the briefing
request from an initial URL). The previously-used `?from=&to=` window params
are dropped for Chronicles; the drilldown is driven by the page-owned day, not
by `useTimeWindow` (which stays as-is for `CostsPage`).

```
ChroniclesPage
  selectedDate  = clamp(?date= ?? yesterday, earliest_date, yesterday)
  ├── eyebrow:  [<-] <Time short-date>  [->]   (prev clamped at earliest, next at yesterday)
  ├── Voice:    date-relative greet + headline + serif elaboration
  ├── rail:     Attention (first) -> KPI strip -> Recent days (rows navigate)
  └── Drilldown(window = [startOfDay(selectedDate), endOfDay(selectedDate)])
        disclosed on demand; static (settled day); manual refresh only
```

## Decision 3 — Source-health attention is date-scoped (backend fix)

`source_health` items describe live connector state ("a source is erroring
*now*"). They are only meaningful for the most recent reconstructable day.
`compose_briefing_payload` now includes them only when
`_target_is_recent(target, tz, now)` (target is yesterday or today). For older
archive dates they are excluded, so a historical day is never mislabeled
`urgent` by a present-day outage.

`compose_briefing_payload` and `_fetch_source_health_items` take an injectable
`now` (default `datetime.now(UTC)`) so the gating and the 24h `last_error`
cutoff are deterministic under test. Anomalies, open-corrections, KPI, streaks,
and recent-days already anchor on the target window and are unchanged.

## Decision 4 — Bounded backward navigation (`earliest_date`)

An unbounded prev stepper would walk into infinite empty days. `ChroniclesBriefing`
gains `earliest_date`: `MIN(episodes.start_at)` converted to the owner-tz
calendar date (or null when there is no data). The stepper disables "previous"
at `earliest_date`. This keeps the archive honest about where its data begins.

## Decision 5 — Drilldown is static and disclosed on demand

A settled past day never changes, so the Chronicles drilldown drops
`useTimeWindow`, `TimeWindowPicker`, `useAutoRefresh`, and `AutoRefreshToggle`
(all still used elsewhere, so nothing is orphaned). It keeps `ManualRefreshButton`
(fed the day's `{from,to}`). The heavy Gantt/Map/aggregations body is wrapped
in a disclosure that mounts on expand, satisfying the existing spec scenario
"lazy-loaded on first interaction" that the prior always-expanded panel had
drifted from.

## Decision 6 — Editorial polish (doctrine-aligned)

- **Date-relative greet.** `deriveHeadlineLines` keys its subject off how far
  back the day is: "Yesterday", the weekday name within the last week, else the
  short date. The `urgent` predicate "left work." (ambiguous) is replaced.
- **Demoted provenance pill.** The `templated` / `llm·cached` labels are
  internal provenance the owner cannot act on. Only `stale` (the one state with
  a consequence: "may be out of date") shows a quiet indicator; the others
  render nothing. Manual refresh stays available.
- **Attention leads the rail.** "Anything I need to act on?" is the
  highest-value question; the attention list moves above the KPI strip.
- **KPI top-lane fix.** The "Top lane" cell put a string (`Email · 2.3h`) in
  the 32px tabular-numeral slot. The value becomes the hours (`2.3h`, tnum) and
  the lane name moves to the delta line.

All copy obeys the doctrine voice rules (sentence case, no em-dash, no
exclamation, owner-direct, past/present tense only). The selected-date label
renders via the `<Time>` primitive (`precision="short-date"`), not hand-rolled
formatting. The stepper reuses `Button` + lucide `ChevronLeft`/`ChevronRight`
and the existing eyebrow tokens; no raw hex.

## Out of scope

- Multi-day / custom range analytics in the Chronicles drilldown.
- A pop-over calendar month picker (no calendar primitive exists today; the
  prev/next stepper plus the navigable recent-days index cover v1).
- A live "today so far" mode (Chronicles shows settled days only).
- Updating the stale `archetype="workspace"` reference to `ChroniclesPage` in
  `about/lay-and-land/frontend.md` (the code and the dashboard-chronicles spec
  are already editorial; the topology doc lags). This drift predates this change
  and is tracked in bu-26j38.

## Consciously deferred (from the adversarial review)

- **Eyebrow date label in UTC+13/+14 zones.** The selected-day label renders
  via the doctrine-mandated `<Time precision="short-date">`, whose date-only
  handling anchors at UTC noon and so reads one day early only for UTC+13/+14
  zones. The owner default is Asia/Singapore (UTC+8), which is exact. The
  drilldown window itself is exact for every zone (see Decision 1 / the
  `dayWindowInTz` helper). Bypassing `<Time>` for a calendar-date label to fix
  the exotic-zone edge was judged not worth violating the single-formatter rule.
- **`earliest_date` ignores override tombstones.** It is `MIN(start_at)` over
  non-tombstoned base episodes. An override that tombstones the single earliest
  episode could leave the backward bound one day early. The bound is advisory
  (it only stops infinite empty backward stepping), so the extra override join
  was not worth the cost.
