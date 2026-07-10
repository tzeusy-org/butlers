## Why

The calendar-workspace UX roadmap (epic `bu-l3k0zg`) introduces three small
dashboard endpoints that each add or extend an HTTP contract but are too small to
warrant their own change: an event detail side-panel needs a single-entry read,
a butler-event dialog needs a non-persisting recurrence preview, and due
reminders/butler events need an in-grid snooze/dismiss. None of these exists
today: the workspace read API is range-only (no single-entry lookup), there is
no dry-run preview of the RRULE/cron expansion, and `reminder_dismiss` exists as
an MCP tool but has no workspace endpoint behind it.

These are grouped here so the contract is specified before implementation (the
project is spec-driven), without inflating three trivial endpoints into three
separate changes.

## What Changes

- **NEW `GET /api/calendar/workspace/entries/{entry_id}`** — single-entry lookup
  for the detail side-panel. `entry_id` maps to the indexed
  `calendar_event_instances.id`. Read-only, no migration. (Bead `bu-p20s01`; the
  inline-edit UI reuses the existing mutation envelope and is FE-only, not
  specified here.)
- **NEW `POST /api/calendar/workspace/butler-events/preview`** — dry-runs the
  existing `dateutil` RRULE / `croniter` expansion and returns the projected
  occurrence datetimes (mirroring the existing 90-day window + "+N more" capping)
  **without persisting anything**. Pure compute, no DB write, no LLM. (Bead
  `bu-15srd1`.)
- **EXTENDED butler-events mutation** — the workspace butler-events action set
  gains `dismiss` and `snooze`. `dismiss` wires the existing `reminder_dismiss`
  MCP tool; `snooze` is a reminder/butler-event update with a new `due_at` via
  the existing update path. No new table, no new MCP tool. (Bead `bu-ul4dgm`.)
