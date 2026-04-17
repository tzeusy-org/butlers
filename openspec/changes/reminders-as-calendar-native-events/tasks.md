## 1. Database Schema Migration

- [ ] 1.1 Add `source_butler TEXT NOT NULL DEFAULT 'unknown'`, `source_session_id TEXT`, `title TEXT NOT NULL DEFAULT '(untitled)'`, `body TEXT` columns to `calendar_events` table in a new core Alembic migration
- [ ] 1.2 Backfill `title` from `metadata->>'title'` or `metadata->>'display_title'` for existing rows, then remove DEFAULT
- [ ] 1.3 Backfill `source_butler` from source metadata or source row context for existing rows, then remove DEFAULT
- [ ] 1.4 Create `calendar_event_entities` junction table with composite PK `(event_id, entity_id)`, FKs to `calendar_events(id)` and `public.entities(id)` both with `ON DELETE CASCADE`
- [ ] 1.5 Create index `idx_calendar_event_entities_entity` on `calendar_event_entities(entity_id)`
- [ ] 1.6 Create index on `calendar_events(source_butler)`

## 2. Reminder Data Migration

- [ ] 2.1 Migrate each `reminders` row into `calendar_events`: map `due_at` → `starts_at`, compute `ends_at` = `starts_at + 15min`, map `reminder_type` to RRULE (`recurring` + cron → RRULE, `one_time` → NULL), map `dismissed` → `status = 'cancelled'`/`'confirmed'`, set `source_butler = 'relationship'`
- [ ] 2.2 For each migrated reminder with `contact_id`: resolve `contacts.entity_id`, insert into `calendar_event_entities(event_id, entity_id)`
- [ ] 2.3 Ensure a `calendar_sources` row exists with `source_kind = 'internal_reminders'` and `lane = 'butler'` for the relationship butler schema
- [ ] 2.4 Delete facts with `predicate = 'reminder'` from the facts table
- [ ] 2.5 Rename `reminders` table to `_reminders_backup`
- [ ] 2.6 Write migration test: verify row counts before/after, verify entity linkage preserved, verify RRULE conversion correctness

## 3. Calendar Module — Reminder Tools

- [ ] 3.1 Implement `reminder_create` MCP tool on the calendar module: accepts `title`, `body`, `due_at`, `ends_at` (optional, default +15min), `recurrence` (one_time/yearly/monthly or RRULE string), `entity_ids` (list of UUIDs), `timezone`; creates `calendar_events` row + `calendar_event_entities` rows
- [ ] 3.2 Implement `reminder_list` MCP tool: accepts optional `entity_id`, `due_before`, `include_dismissed`; queries `calendar_events` with `source_kind = 'internal_reminders'` and `source_butler` filter, joins `calendar_event_entities` for entity data
- [ ] 3.3 Implement `reminder_dismiss` MCP tool: for one-time → set `status = 'cancelled'`; for recurring → cancel current instance in `calendar_event_instances`, keep series active
- [ ] 3.4 Register reminder tools in calendar module's `register_tools()` method
- [ ] 3.5 Write unit tests for `reminder_create`, `reminder_list`, `reminder_dismiss`

## 4. Calendar Module — Authorship and Title/Body

- [ ] 4.1 Update `calendar_create_event` to accept and store `title`, `body`, `source_butler`, `source_session_id`
- [ ] 4.2 Update `calendar_create_butler_event` to set `source_butler` and `source_session_id`
- [ ] 4.3 Update `calendar_update_event` and `calendar_update_butler_event` to support `title` and `body` fields
- [ ] 4.4 Update `CalendarEvent` model to include `title`, `body`, `source_butler`, `source_session_id` fields
- [ ] 4.5 Update Google event parser (`_google_event_to_calendar_event`) to map `summary` → `title` and `description` → `body`

## 5. Calendar Module — Entity Association

- [ ] 5.1 Update `calendar_create_event` and `calendar_create_butler_event` to accept optional `entity_ids` list and insert into `calendar_event_entities`
- [ ] 5.2 Update `calendar_get_event` to join `calendar_event_entities` and return associated entity IDs
- [ ] 5.3 Update `calendar_list_events` to optionally filter by `entity_id`
- [ ] 5.4 Update `calendar_update_event` to support adding/removing entity associations
- [ ] 5.5 Write unit tests for entity association CRUD

## 6. Calendar Module — Tick Scoping

- [ ] 6.1 Update `tick()` method signature to accept required `source_butler` parameter
- [ ] 6.2 Add due-reminder evaluation in `tick()`: query `calendar_events` where `source_kind = 'internal_reminders'` AND `source_butler = <param>` AND `status = 'confirmed'` AND `starts_at` within tick window, dispatch `notify()` for each
- [ ] 6.3 Update all butler daemon `tick()` callers to pass `source_butler` argument
- [ ] 6.4 Write unit test: tick only evaluates events matching source_butler

## 7. Projection Simplification

- [ ] 7.1 Remove `_project_reminders_source` method and `_create_reminder_event` helper from calendar module
- [ ] 7.2 Ensure reminder events (source_kind = "internal_reminders") are projected via the standard event projection pipeline alongside other calendar events
- [ ] 7.3 Update calendar workspace API to handle reminder events as native events (no special `source_type` mapping needed beyond what `source_kind` provides)
- [ ] 7.4 Verify frontend CalendarWorkspacePage correctly displays reminders as calendar events with appropriate type badge

## 8. Relationship Butler Cleanup

- [ ] 8.1 Delete `roster/relationship/tools/reminders.py`
- [ ] 8.2 Update relationship butler `CLAUDE.md`: remove `reminder_create/list` from tool list, add note that reminders are managed via calendar module tools
- [ ] 8.3 Update relationship butler `MANIFESTO.md`: reframe reminder references from tool-ownership to calendar-provided capability
- [ ] 8.4 Update any relationship butler skills that reference reminder tools to use calendar module `reminder_create`/`reminder_list`/`reminder_dismiss`
- [ ] 8.5 Update relationship butler `butler.toml` if any module config references reminders

## 9. Entity Merge Handling

- [ ] 9.1 Update `entity_merge()` in `src/butlers/modules/memory/tools/entities.py` to re-point `calendar_event_entities` rows from source entity to target entity, deduplicating on `(event_id, entity_id)`
- [ ] 9.2 Write unit test: entity merge correctly migrates calendar event associations

## 10. Integration Tests and Cleanup

- [ ] 10.1 Write integration test: full lifecycle — create reminder, list it, dismiss it, verify calendar event state
- [ ] 10.2 Write integration test: recurring reminder dismiss advances to next instance
- [ ] 10.3 Write integration test: tick(source_butler) only fires notifications for matching butler's reminders
- [ ] 10.4 Update existing calendar module tests that reference the old reminder projection pipeline
- [ ] 10.5 Run full test suite, fix any breakage from removed reminders table/tools
