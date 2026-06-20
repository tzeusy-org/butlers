# Design — Route butler-authored events to the dedicated Butlers calendar

## Context

Two event systems coexist (see `module-calendar` spec):

- **User-lane CRUD** (`calendar_create_event` / `update` / `delete`): real
  Google events, branded `BUTLER:` + `butler_generated`, currently written to
  `_primary_calendar_id`.
- **Butler-lane workspace** (`calendar_create_butler_event` family): internal
  `scheduled_tasks` / `reminders`, pushed to `_resolved_calendar_id` (the
  Butlers calendar) by `_push_internal_events_to_provider`. **Already correct;
  not touched by this change.**

The Butlers calendar auto-upserts at startup
(`_resolve_startup_calendar_id` → `discover_or_create_calendar("Butlers")`),
so no new calendar-creation machinery is needed.

## Decisions

### D1 — Flip the create default, keep the override

`_resolve_calendar_id(None)` currently returns `_primary_calendar_id` when set.
Change the no-override branch to return `_resolved_calendar_id` (Butlers
calendar). The existing override path (caller passes `calendar_id`) is
unchanged and is the documented escape hatch for "put this on my primary."

Rejected alternative: an `on_behalf_of_user` intent flag that the LLM sets to
decide primary vs Butlers. Rejected because it pushes a routing decision onto
the model on every call (error-prone) when the owner wants a single
deterministic rule. The explicit `calendar_id` override covers the rare case.

### D2 — Separate "Butlers calendar id" from "default target"

Today `calendar_set_primary` overwrites `_resolved_calendar_id` AND reuses the
`GOOGLE_CALENDAR_ID` cred key, conflating the immutable Butlers-calendar id
with a user-chosen default. Split them:

- `_resolved_calendar_id` — immutable Butlers calendar id (cred key
  `GOOGLE_CALENDAR_ID`). Never mutated by `calendar_set_primary`.
- A distinct field (e.g. `_default_target_calendar_id` / the existing
  `_primary_calendar_id`) and a distinct cred key for the user-chosen default
  target.

With D1, butler-authored creates target `_resolved_calendar_id` regardless of
the user's default-target selection, so the two concerns no longer collide.

### D3 — Home-calendar resolver for update/delete

`calendar_update_event` / `calendar_delete_event` currently call
`_resolve_calendar_id(None)` → one calendar. Replace with a resolver keyed by
the provider event id:

1. **Explicit `calendar_id`** → use it (validated against discovered calendars).
2. **Projection lookup** → `SELECT cs.calendar_id FROM calendar_events ce JOIN
   calendar_sources cs ON ce.source_id = cs.id WHERE ce.origin_ref = $1` (the
   event's Google id). Use that calendar.
3. **Bounded search** → if not in projection, try `get_event` on the Butlers
   calendar then primary (and other discovered calendars) until found.
4. **Fallback** → primary calendar; surface not-found fail-open (consistent
   with `calendar_get_event`'s 404 contract) rather than raising.

This is the load-bearing piece for "edit my own primary events in place" AND
"edit butler events now living on the Butlers calendar."

### D4 — `create_user_event` becomes branded + Butlers-routed

Add `_ensure_butler_title` + `_build_butler_private_metadata` to the payload it
builds, and route via the new default (Butlers calendar). Keeps the permissions
gate it already enforces.

## Risks / Trade-offs

- **Behavioral change for existing users.** Meal logs and butler-created events
  stop appearing on the primary calendar. Mitigated: the Butlers calendar is a
  real, visible subcalendar the user already has; events remain visible, just
  grouped. Go-forward only (no backfill) avoids touching historical data.
- **Bounded search latency.** Step 3 of the resolver adds provider calls when
  the projection misses. Mitigated: projection hit is the common path; cap the
  search to the two known calendars first.
- **Sync/authoritativeness unaffected.** Butler-authored user-lane events are
  still projected via `_project_provider_changes`; this change only moves where
  they are written, not how they're synced/owned.

## Test Strategy

- Unit: `_resolve_calendar_id(None)` returns Butlers id; explicit override
  returns the override; `calendar_set_primary` leaves `_resolved_calendar_id`
  intact.
- Unit: home-calendar resolver — projection hit, override precedence, search
  fallback, not-found fail-open.
- Unit: `create_user_event` stamps butler metadata and targets Butlers id.
- Integration (fake provider): create → event lands on Butlers calendar id;
  update/delete by id target the resolved home calendar; user-lane synced event
  on primary is patched on primary.
- Regression: existing CRUD/sync tests updated for the new default target.
