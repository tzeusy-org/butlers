# Design — Free/busy generalization + owner scheduling preferences

## Context

Two pieces of existing reality constrain this change:

1. **Free/busy already exists, scoped to conflict detection.** `find_conflicts`
   is an `@abc.abstractmethod` on `CalendarProvider` (calendar.py:1714). The
   Google implementation (calendar.py:2380-2458) POSTs to `/freeBusy` with
   `timeMin`/`timeMax`/`timeZone`/`items=[{"id": calendar_id}]`, validates the
   `calendars` → `<id>` → `busy[]` shape, parses each `{start, end}` window via
   `_parse_google_datetime`, and returns them as synthetic `(busy)`
   `CalendarEvent`s. It is hardwired to **one** calendar and the **candidate
   event's own** `start_at`/`end_at`. The `calendar` OAuth scope already in use
   (`https://www.googleapis.com/auth/calendar`) authorizes `/freeBusy`.

2. **The slot generator is life-blind.** `_build_suggested_slots`
   (calendar.py:8288) takes the candidate and the conflict list, then steps
   forward from `max(candidate.start_at, last_conflict_end)` in
   `duration + 15min` increments. It never consults working hours, days of week,
   or a residence timezone, so it can return 6am or Sunday slots.

## Decisions

### D1 — Generalize `/freeBusy` into a windowed, multi-calendar method

Add to the `CalendarProvider` ABC:

```
async def get_free_busy(
    self, *, calendar_ids: list[str], start_at: datetime, end_at: datetime,
    timezone: str | None = None,
) -> list[BusyWindow]
```

returning merged busy windows across the requested calendars. The Google
implementation **reuses the existing `/freeBusy` request body and the existing
`calendars`/`busy[]` parsing** — the only changes are: `items` becomes the full
`calendar_ids` list, the window comes from the arguments (not a candidate
event), and busy windows from all returned calendars are unioned.

`find_conflicts` is then refactored to a thin caller:
`get_free_busy(calendar_ids=[calendar_id], start_at=candidate.start_at,
end_at=candidate.end_at)` and wraps the result back into `(busy)` events so its
existing callers (`_evaluate_conflicts`) are unchanged. This keeps the single
`/freeBusy` parse in one place rather than duplicating it.

Rejected alternative: a brand-new provider method that re-implements the
`/freeBusy` POST. Rejected because the bead is explicit that this is a
generalization — duplicating the request/parse would drift the two paths.

### D2 — `calendar_find_free_slots` ranks, never books

New MCP tool: `calendar_find_free_slots(duration_minutes, search_start,
search_end, calendar_ids=None, constraints=None, limit=...)`. It calls
`get_free_busy` over the search window, subtracts busy windows to get free gaps,
clips the gaps to the owner's scheduling preferences (D3), splits each clipped
gap into `duration_minutes` candidate slots, ranks them (earliest-first, with
constraint matches preferred), and returns up to `limit`. It performs **no**
provider write; committing a slot is a separate `calendar_create_event` call.
The `constraints` argument is a small structured object (e.g.
`{"part_of_day": "morning", "avoid_weekdays": ["FR"]}`) so the one cheap NL parse
("mornings only", "avoid Fridays") happens at the call site, not inside the
deterministic finder.

### D3 — Owner scheduling preferences are DISTINCT from notification quiet hours

**Modeling decision (resolves bu-vj0ax8):** do NOT reuse
`time-aware-delivery`'s `delivery_preferences.quiet_hours_start/end`. That row is
keyed by `butler_name` and answers _"when may this butler ping me?"_ — a
**notification** concern. Slot ranking needs _"when may a meeting be placed on my
day?"_ — a **life/availability** concern owned by the human, not any one butler.
Conflating them would mean (a) every butler would need identical hours, and
(b) widening notification quiet hours (to silence pings) would silently shrink
the bookable day.

We therefore **extend the `time-aware-delivery` capability** (the spec named in
the bead) with a **separate, owner-scoped** concept:
`owner_scheduling_preferences` — a single owner row (not per-butler) with
`earliest_meeting_time`, `latest_meeting_time`, `meeting_days` (allowed weekdays),
`timezone` (residence/owner timezone), and `no_meeting_blocks` (recurring
intervals like a daily lunch). New MCP tools
`scheduling_preferences_get` / `scheduling_preferences_set` read/write it.

Why on the `time-aware-delivery` spec and not a new capability: both concepts are
"when is it OK to reach the owner / occupy the owner's time," they share the
timezone primitive, and the bead names `time-aware-delivery/spec.md`. They are
kept as **separate requirements with separate storage** so the distinction is
explicit and neither leaks into the other.

`_build_suggested_slots` and `calendar_find_free_slots` both read
`owner_scheduling_preferences` and clip/reject candidate slots outside the
allowed hours/days and inside `no_meeting_blocks`. When no row exists, both fall
back to today's behavior (no life-constraint filtering) so the change is
non-breaking.

## Risks / Trade-offs

- **Two "hours" stores could confuse operators.** Mitigated by distinct table,
  distinct MCP tool names, and explicit spec scenarios stating which one governs
  notifications vs. scheduling.
- **`get_free_busy` over many calendars / long windows is heavier.** Google
  `/freeBusy` caps the window; the finder caps the search window and slot count.
  The common case (one or two calendars, a 1-2 week window) is one POST.
- **Refactoring `find_conflicts` risks regressing conflict detection.**
  Mitigated: `find_conflicts` keeps its exact signature and return shape; only
  its body delegates. Existing conflict tests are the regression guard.

## Test Strategy

- Unit: `get_free_busy` merges busy windows across multiple calendar ids; single
  busy calendar matches the old `find_conflicts` output; empty result on no busy.
- Unit: `find_conflicts` (post-refactor) returns the same conflicts as before for
  the single-calendar candidate window (golden regression).
- Unit: `calendar_find_free_slots` subtracts busy windows, respects duration,
  clips to owner scheduling prefs (no 6am / no Sunday / skips lunch block), and
  ranks earliest-first; returns empty when the window is fully busy.
- Unit: `_build_suggested_slots` with owner prefs set never proposes a slot
  outside hours/days; with no prefs row, behaves as today.
- API: `POST /api/calendar/workspace/find-time` returns ranked slots and a
  validation error on bad duration/window.
- Unit: `scheduling_preferences_set/get` round-trip; invalid timezone rejected;
  no row → defaults (no constraints).
