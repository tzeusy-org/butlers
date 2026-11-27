## 1. Spec

- [ ] 1.1 This change reviewed before runtime code.

## 2. Slices

- [ ] 2.1 N3b `public.read_calendar_event_changes` definer (own migration, grants manifest) and `GET /api/calendar/workspace/changes`, with `tests/connectors -k calendar_changes`, real-PG definer test and route test.
- [ ] 2.2 N3c `use-calendar-changes` hook and the queue's `change` kind with before/after text.
- [ ] 2.3 Later (bu-5ii4gj.11.2): persisted dismiss of a change.
