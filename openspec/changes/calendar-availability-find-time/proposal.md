## Why

The calendar workspace is read-mostly: it can show conflicts when you create an
event, but it cannot answer the inverse question — _"where do I have an open
hour next week?"_ The plumbing to answer it already exists but is hidden inside
conflict detection. `find_conflicts` is an `@abc.abstractmethod` on
`CalendarProvider` (calendar.py:1714) whose Google implementation POSTs to
`/freeBusy` (calendar.py:2404) with `timeMin`/`timeMax`/`items` and parses the
returned `busy[]` windows for **one** calendar over the **candidate event's own**
narrow window. There is no way to ask for free/busy across **multiple**
calendars over an **arbitrary** window, and no MCP tool or API route that turns
free/busy into ranked open slots.

Worse, the one slot generator that does exist — `_build_suggested_slots`
(calendar.py:8288) — has no notion of the owner's life: it walks forward from the
last conflict in fixed 15-minute steps and will happily propose a 6am slot or a
Sunday. The only stored "hours" preference today is
`time-aware-delivery`'s `delivery_preferences.quiet_hours_start/end`, which is
**per-butler NOTIFICATION quiet hours** ("don't ping me after 22:00"), NOT the
owner's LIFE no-meeting blocks ("don't schedule meetings after 18:00"). Reusing
it for slot ranking would silently mis-rank.

## What Changes

- **Generalize free/busy into a windowed, multi-calendar provider method.** Add
  `get_free_busy(calendar_ids, start_at, end_at)` to the `CalendarProvider` ABC,
  returning merged busy windows. The Google implementation reuses the existing
  `/freeBusy` request/response plumbing and the already-granted `calendar` OAuth
  scope (no new scope). `find_conflicts` is refactored to call `get_free_busy`
  for the single-calendar candidate window so the `/freeBusy` parsing lives in
  one place. This is a **generalization of existing plumbing, not greenfield**.
- **Add a `calendar_find_free_slots` MCP tool.** Given a duration, a search
  window, and optional natural-language-derived constraints ("mornings only",
  "avoid Fridays"), it queries `get_free_busy` across the relevant calendars and
  returns ranked open slots. This makes the calendar module register **17 MCP
  tools total** (was "16 MCP tools total").
- **Add `POST /api/calendar/workspace/find-time`.** The workspace endpoint
  behind the "Find time" panel: takes a duration + constraints, returns ranked
  open slots for the grid overlay → select → prefilled create flow.
- **Introduce owner scheduling-availability preferences, distinct from
  notification quiet hours.** A new owner-scoped preference (earliest/latest
  meeting time, working days, residence timezone, no-meeting blocks) is added to
  the `time-aware-delivery` capability as a **separate** concept from the
  per-butler notification `delivery_preferences`. Both the new free-slot finder
  and the existing `_build_suggested_slots` consume it so neither proposes
  6am / Sunday slots.

## Capabilities

### New Capabilities

_None — this extends existing capabilities._

### Modified Capabilities

- `module-calendar`: free/busy is generalized from the conflict-only single
  window into a windowed multi-calendar `get_free_busy`; a new
  `calendar_find_free_slots` tool ranks open slots; the registered-tool count
  moves from 16 to 17; slot suggestion consumes owner scheduling preferences.
- `connector-google-calendar`: documents that the connector's already-granted
  `calendar` scope authorizes free/busy queries, so no scope change is required
  for the availability finder.
- `time-aware-delivery`: adds owner scheduling-availability preferences as a
  distinct, owner-scoped concept separate from per-butler notification quiet
  hours.

## Impact

- **Calendar module (`src/butlers/modules/calendar.py`):**
  - New `get_free_busy` abstract method on `CalendarProvider`; Google impl reuses
    the `/freeBusy` plumbing currently inside `find_conflicts`.
  - `find_conflicts` refactored to delegate to `get_free_busy`.
  - New `calendar_find_free_slots` MCP tool.
  - `_build_suggested_slots` and the new finder take owner scheduling-preference
    constraints.
- **Calendar workspace API (`src/butlers/api/routers/calendar_workspace.py`):**
  new `POST /api/calendar/workspace/find-time` route + request/response models in
  `src/butlers/api/models/calendar_workspace.py`.
- **Time-aware delivery:** new owner-scoped scheduling-preferences storage and
  MCP tools; no change to the existing per-butler `delivery_preferences`
  notification quiet-hours behavior.
- **Spec (`openspec/specs/module-calendar/spec.md`):** "Calendar Event CRUD
  Tools" tool-count literal and a new free-slot-finder requirement;
  `connector-google-calendar` free/busy scope note; `time-aware-delivery`
  scheduling-preferences requirement.
- **No change** to the dual-lane sync model, recurrence scope (still
  series-scoped in v1), or butler-event routing.

## Out of Scope

- Proactive conflict / overcommitment radar (`bu-q8o90x`) — a separate forward
  scan that consumes this finder; not built here.
- Drag-to-reschedule and other Tier-1 workspace interactions.
- An LLM that auto-books a slot — `calendar_find_free_slots` only proposes; the
  human (or a downstream create call) commits.
- Cross-butler free/busy aggregation — free/busy stays within the calendar
  module's own connected accounts.
