## Why

The `module-calendar` requirement "Dual-Lane Ownership and Authoritativeness" contradicts
itself. Its scenario "Butler-generated events in provider sync projection" says provider sync
persists every event, butler-generated ones included, while "Butler overwrites external edits to
butler-owned events" says provider sync skips an edited butler-generated event through a
butler-generated filter. The code has no such filter: `_project_provider_changes` persists every
provider event and records `butler_generated`/`butler_name` in metadata, and
`_push_internal_events_to_provider` overwrites the Google copy of a pushed scheduled task or
internal reminder with local state on the next push.

## What Changes

- Rewrite the scenario "Butler overwrites external edits to butler-owned events" so provider sync
  persists the edited copy with its butler-generated metadata, the outbound push overwrites the
  Google event with local state, and the projection converges on the following sync. The outcome
  and the BECAUSE are unchanged: the butler database stays authoritative for butler-owned events.
- No other text in the requirement changes. No behavior changes.

## Impact

- Spec: `openspec/specs/module-calendar/spec.md` (one scenario).
- Code: comment-only correction in `src/butlers/modules/calendar.py` (`_project_provider_changes`).
- Tests: `tests/modules/test_module_calendar.py` proves both halves of the corrected scenario.
