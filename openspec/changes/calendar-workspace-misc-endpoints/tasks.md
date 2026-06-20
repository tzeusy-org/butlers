# Tasks — calendar-workspace-misc-endpoints

Three small backend endpoints. No DB migration, no new MCP tool. Preview reuses
the existing recurrence engine; dismiss/snooze reuse the existing
`reminder_dismiss` tool and butler-event update path.

## 1. Single-entry lookup (bu-p20s01)

- [ ] 1.1 Add `GET /api/calendar/workspace/entries/{entry_id}` resolving
  `entry_id` against the indexed `calendar_event_instances.id` and returning the
  `UnifiedCalendarEntry`-shaped record (incl. `source_butler`/`source_session_id`
  provenance) in the standard `ApiResponse` envelope
- [ ] 1.2 Return HTTP 404 (`status: not_found`, `entry: null`) for an unknown
  `entry_id`; fan-out lookup is scoped to `butlers_with_module('calendar')`
- [ ] 1.3 Test: known id returns the entry; unknown id returns 404 not-found

## 2. Recurrence dry-run preview (bu-15srd1)

- [ ] 2.1 Add `POST /api/calendar/workspace/butler-events/preview` taking a draft
  (`rrule` or `cron`, optional `until_at`, `timezone`) and returning the projected
  occurrence datetimes over the existing 90-day window with the existing
  "+N more" capping sentinel; persist nothing
- [ ] 2.2 Reuse the existing `dateutil`/`croniter` expansion helper in a
  read-only/dry-run mode; surface lossy conversions in a `notes` field
- [ ] 2.3 Invalid `rrule`/`cron` fails fast with a 422 carrying the parse error
  (no partial/silent result)
- [ ] 2.4 Test: a weekly RRULE returns the expected next dates + cap sentinel; an
  invalid expression returns 422 with diagnostic detail; nothing is written

## 3. Snooze / dismiss actions (bu-ul4dgm)

- [ ] 3.1 Extend the workspace butler-events mutation handler with `action`
  values `dismiss` and `snooze`
- [ ] 3.2 `dismiss` dispatches the existing `reminder_dismiss` MCP tool; `snooze`
  updates the reminder/butler-event `due_at` via the existing update path
- [ ] 3.3 Unknown target id returns HTTP 404; the soft-mutation envelope
  (`status`/`persisted`) is preserved
- [ ] 3.4 Test: dismiss marks the reminder dismissed; snooze moves `due_at`;
  unknown id returns 404
