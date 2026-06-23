## Why

Every event a butler authors via `calendar_create_event` is branded
(`BUTLER:` title prefix + `extendedProperties.private.butler_generated=true`)
yet is written to the user's **primary** Google calendar — the current
documented behavior of the "Dual calendar ID resolution" requirement, which
deliberately prefers `_primary_calendar_id` for tool mutations. The result is
butler-authored clutter mixed into the user's personal calendar, and a
confusing ownership model: butler-owned events on the primary calendar get
silently reverted if the user edits them directly (the "Butler overwrites
external edits" scenario).

The dedicated "Butlers" Google subcalendar already exists and auto-upserts at
startup (`discover_or_create_calendar("Butlers")` → `_resolved_calendar_id`),
and internal scheduler/reminder events (`lane="butler"`) are already pushed to
it via `_push_internal_events_to_provider`. Only the user-lane CRUD path
(`calendar_create_event`, `create_user_event`) still targets primary.

The owner wants a clean rule: **butler-authored events live on the Butlers
subcalendar; the user's own events stay on their primary calendar and the
butler edits them in place.**

## What Changes

- **Default create target flips to the Butlers calendar.** Butler-authored
  creates (`calendar_create_event` with no explicit `calendar_id`, and the
  programmatic `create_user_event`) default to `_resolved_calendar_id` (the
  Butlers subcalendar) instead of `_primary_calendar_id`. An explicit
  `calendar_id` argument (already accepted) remains the opt-out for "put this
  on my primary calendar."
- **`create_user_event` becomes a first-class butler-authored write.** It is
  stamped with butler-generated metadata (`butler_generated=true`,
  `butler_name`) and `BUTLER:` title branding like `calendar_create_event`,
  and routes to the Butlers calendar. (Today it is unmarked and lands on
  primary.)
- **Update/delete resolve the event's HOME calendar.** `calendar_update_event`
  and `calendar_delete_event` called without an explicit `calendar_id` resolve
  the calendar the event actually lives on (via the projection:
  `calendar_events.origin_ref` → `calendar_sources.calendar_id` keyed by the
  provider event id), rather than blindly defaulting to primary. This is what
  lets the butler edit the user's own primary-calendar events in place while
  also correctly editing butler events now living on the Butlers calendar.
  Fallback order: explicit override → projection home calendar → bounded search
  across discovered calendars → primary.
- **Calendar-id roles are disentangled.** `_resolved_calendar_id` (the
  immutable, discovered/created Butlers calendar id, cred key
  `GOOGLE_CALENDAR_ID`) is separated from any user-chosen default-target
  override. `calendar_set_primary` no longer overwrites the Butlers calendar
  id; it sets the primary/default-target selection only.
- **Go-forward only.** Butler-branded events already on the user's primary
  calendar are left untouched. No bulk migration of live Google events.

## Capabilities

### New Capabilities

_None — this changes routing behavior of existing capabilities._

### Modified Capabilities

- `module-calendar`: Default create target for butler-authored events flips
  from primary to the dedicated Butlers calendar; `create_user_event` becomes a
  branded butler-authored write; update/delete resolve the event's home
  calendar instead of defaulting to primary; `calendar_set_primary` no longer
  clobbers `_resolved_calendar_id`.

## Impact

- **Calendar module (`src/butlers/modules/calendar.py`):**
  - `_resolve_calendar_id` default branch returns `_resolved_calendar_id`
    (Butlers calendar) for butler-authored creates.
  - New home-calendar resolver for update/delete keyed by provider event id.
  - `create_user_event` gains butler branding/metadata and Butlers-calendar
    routing.
  - `calendar_set_primary` stops mutating `_resolved_calendar_id`; introduces a
    distinct default-target field (e.g. `_default_target_calendar_id`).
- **Spec (`openspec/specs/module-calendar/spec.md`):** "Dual calendar ID
  resolution", "Create butler-generated event", "Set primary calendar", and the
  CRUD update/delete scenarios are modified.
- **Health/meal logging (`roster/health/modules/__init__.py`):** Meal events
  created via `create_user_event` move from primary to the Butlers calendar
  (behavioral change, no API change).
- **Existing data:** No migration. Pre-existing butler events on primary stay
  put. New behavior is go-forward only.
- **No frontend, no DB schema change.** Pure provider-routing + in-memory
  state change.

## Out of Scope

- Bulk migration / cleanup of butler-branded events already on the primary
  calendar (explicitly deferred; owner can clean up manually).
- The `calendar_create_butler_event` family (internal scheduler/reminder
  workspace events) — already routes to the Butlers calendar; unchanged.
- An "autonomous vs on-behalf-of-user" intent flag — rejected in favor of the
  simpler rule "all butler creates → Butlers calendar, explicit `calendar_id`
  override for primary."
- Changing the sync/authoritativeness (dual-lane) model.
