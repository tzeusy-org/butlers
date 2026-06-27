## Why

Recurring-event mutations are series-only today. `calendar_update_event` and
`calendar_delete_event` accept `recurrence_scope: Literal["series"] = "series"`
(`src/butlers/modules/calendar.py` ~:3075 / ~:3414), and the
`module-calendar` spec normatively pins butler-workspace deletion as
"(series-scoped in v1)". So a user can never say "skip just this Tuesday's
standup" or "move every meeting from next week onward to 4pm" — the only choices
are edit the whole series or nothing. This is the most common recurring-calendar
operation and its absence forces clumsy workarounds (delete the series, recreate
two series).

The projection layer is already prepared for occurrence-level state:
`calendar_event_instances` carries an `is_exception` boolean
(`src/butlers/modules/calendar.py` ~:6219, ~:6240), and the provider stores
recurrence as a `recurrence` array (RRULE entries) that can also hold EXDATE /
RDATE entries (`~:917`, `~:1036`). The pieces exist; only the tool surface and
the `recurrence_scope` literal are missing.

## What Changes

- **`recurrence_scope` widens from `"series"` to `this | following | series`.**
  `calendar_update_event` and `calendar_delete_event` accept the new literal.
  `series` keeps today's whole-series behavior. `this` affects only the single
  named occurrence. `following` affects the named occurrence and every later one
  (split the series at that boundary).
- **Two new MCP tools are added** for the explicit occurrence-targeted path:
  `calendar_update_event_instance` and `calendar_delete_event_instance`. Each
  takes the base recurring `event_id` plus the occurrence start (`instance_start_at`),
  writes the provider's EXDATE/RDATE recurrence entry for the detached
  occurrence, and marks the matching `calendar_event_instances` row
  `is_exception = true`. This brings the **registered tool count from 16 to 18**.
- **The series-scoped-in-v1 statement is retired.** The butler-event delete
  scenario that today says deletion is "(series-scoped in v1)" is updated to
  describe scope-aware deletion.
- **Impact preview surfaces the scope.** A scope-aware mutation reports how many
  occurrences it will touch (one for `this`, the count from the boundary onward
  for `following`, the whole series for `series`) so the high-impact approval
  gate and the caller can reason about blast radius before the write.

## Capabilities

### New Capabilities

_None — this widens the recurrence handling of existing capabilities._

### Modified Capabilities

- `module-calendar`: `recurrence_scope` widens to `this | following | series`
  on `calendar_update_event` / `calendar_delete_event`; two occurrence-targeted
  tools (`calendar_update_event_instance`, `calendar_delete_event_instance`) are
  added (tool count 16 → 18); butler-event deletion is no longer series-only;
  scope-aware mutations expose an occurrence-count impact preview.

## Impact

- **Calendar module (`src/butlers/modules/calendar.py`):**
  - Widen the `recurrence_scope` literal on `calendar_update_event` (~:3075) and
    `calendar_delete_event` (~:3414) to `Literal["this", "following", "series"]`.
  - Add `calendar_update_event_instance` and `calendar_delete_event_instance`
    tools that resolve the occurrence, write provider EXDATE/RDATE recurrence
    entries, and set `is_exception = true` on the projected instance row.
  - Add an occurrence-count impact-preview helper feeding the existing
    `_gate_high_impact_mutation` path.
- **Spec (`openspec/specs/module-calendar/spec.md`):** the "Calendar Event CRUD
  Tools" requirement (tool count + update/delete scenarios) and the "Butler
  Event Management Tools" requirement (the "series-scoped in v1" delete
  scenario) are modified; a new occurrence-scoped requirement is added.
- **No DB schema change** — `calendar_event_instances.is_exception` already
  exists. **No frontend change** in scope.

## Out of Scope

- Detaching an occurrence into a fully independent standalone event with its own
  RRULE ("split into a separate series") beyond the `following`-boundary split.
- Per-instance attendee management (`calendar_add_attendees` /
  `calendar_remove_attendees` remain series/event-scoped).
- Backfilling `is_exception` for occurrences exception-ed directly in Google
  outside the butler (still reconciled by the normal sync projection path).
- Recurrence-scope handling for the butler-workspace tools
  (`calendar_update_butler_event` / `calendar_toggle_butler_event`) beyond the
  delete scope wording.
