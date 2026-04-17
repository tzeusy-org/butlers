## Why

Reminders and calendar events are semantically the same concept — "something needs to happen at a certain time, possibly recurring." Currently reminders live in up to three separate stores (a `reminders` table, SPO facts, and calendar workspace projections), creating a confusing dual system where reminders are half-projected into the calendar but not first-class citizens. Unifying them eliminates storage duplication, simplifies the calendar workspace projection pipeline, and makes reminders visible and manageable from a single surface.

## What Changes

- **Reminders become calendar events.** Stored directly in `calendar_events` with `source_kind = "internal_reminders"`. No separate `reminders` table or facts-table entries.
- **Calendar events gain entity associations.** New `calendar_event_entities` junction table links events to entities (not contacts). Multi-entity support.
- **Calendar events gain authorship tracking.** New `source_butler` (TEXT NOT NULL) and `source_session_id` (TEXT nullable) columns on `calendar_events`.
- **Calendar events gain title/body structure.** New `title` (TEXT NOT NULL, <30 words) and `body` (TEXT nullable) columns replace metadata-stuffed event descriptions.
- **Recurrence uses RRULE.** Replaces the `relativedelta` yearly/monthly hack in reminders with standard RRULE strings.
- **`tick(source_butler)` scoping.** Each butler's tick only evaluates events it created, preventing duplicate notifications.
- **Reminder MCP tools move to the calendar module.** `reminder_create`, `reminder_list`, `reminder_dismiss` become calendar module tools available to any butler with calendar enabled.
- **`reminders` table dropped.** Data migrated to `calendar_events`, then table removed. **BREAKING**
- **Facts-table reminder entries deleted.** SPO facts with `predicate = 'reminder'` removed after migration. **BREAKING**
- **Relationship butler manifesto amended.** Removes tool-ownership language for reminders; reframes as calendar-provided capability.
- **Calendar workspace projection simplified.** Reminders no longer need special projection from a separate source — they are native calendar events.

## Capabilities

### New Capabilities

_None — this is a consolidation, not a new capability._

### Modified Capabilities

- `module-calendar`: Add reminder tools (create, list, dismiss), entity association via junction table, source_butler/source_session_id columns, title/body columns, tick(source_butler) scoping, default 15-minute duration for reminders, RRULE-based recurrence for reminders.
- `entity-identity`: Add `calendar_event_entities` junction table linking calendar events to entities.

## Impact

- **Database schema:** Core migration adds columns to `calendar_events`, creates `calendar_event_entities` junction, migrates data from `reminders` table, drops `reminders` table, deletes reminder facts.
- **Calendar module (`src/butlers/modules/calendar.py`):** Gains reminder CRUD tools, entity junction queries, source_butler filtering in tick(), title/body fields in event model.
- **Relationship butler tools (`roster/relationship/tools/reminders.py`):** Deleted entirely.
- **Relationship butler config:** MANIFESTO.md and CLAUDE.md updated to reflect calendar-provided reminders.
- **Calendar workspace API (`src/butlers/api/routers/calendar_workspace.py`):** Simplified — no more separate reminder projection pipeline.
- **Frontend calendar workspace:** Minor — reminders display as native events with a subtype badge rather than a projected overlay.
- **Existing reminder data:** Migrated. Existing reminders become calendar events. No data loss.
