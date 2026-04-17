## Context

Reminders currently exist as a separate concept from calendar events, stored in three places:

1. **`reminders` table** (in the relationship butler's schema) — the primary store, with `contact_id`, `message`, `reminder_type`, `cron`, `due_at`, `dismissed`.
2. **SPO `facts` table** — some reminders duplicated as facts with `predicate = 'reminder'` for the memory/relationship graph.
3. **`calendar_event_instances` projection** — reminders projected into the calendar workspace via `SOURCE_KIND_INTERNAL_REMINDERS` for dashboard display.

This triple-storage creates confusion: reminders are not first-class calendar events, yet they appear in the calendar. The projection pipeline (`_project_reminders_source`, `_create_reminder_event`) adds indirection between what the user sees and where data lives.

Calendar events already have the right data model: `starts_at`, `ends_at`, `recurrence_rule`, `status`, `metadata`. Reminders are a strict subset of this model — a reminder is a calendar event that happens to be scoped to a person and has notification semantics.

**Constraint**: The calendar module is a shared module available to any butler. Reminders are currently owned by the relationship butler. Moving them to the calendar module makes reminders available to all butlers with calendar enabled (health butler can create medication reminders, finance butler can create payment reminders, etc.).

## Goals / Non-Goals

**Goals:**

- Single storage location for reminders: `calendar_events` table
- Entity association on calendar events via multi-entity junction table
- Authorship tracking: which butler and session created each event
- Title/body structure on all calendar events (not just metadata JSONB)
- `tick(source_butler)` scoping so each butler only processes its own events
- Standard RRULE recurrence for reminders (replacing relativedelta hack)
- Reminder MCP tools available to any butler with calendar module

**Non-Goals:**

- Changing how scheduled tasks work (they remain in `scheduled_tasks` table)
- Adding contact_id to calendar events (entity_id via junction only)
- Multi-butler reminder aggregation (each butler manages its own)
- Calendar event sharing between butlers
- Push notifications to external providers (existing notify() pipeline handles this)

## Decisions

### D1: Reminders stored in `calendar_events` with `source_kind` discrimination

**Choice:** Reminders are rows in `calendar_events` belonging to a `calendar_sources` entry with `source_kind = "internal_reminders"` and `lane = "butler"`.

**Why not a new table?** The whole point is consolidation. A new `calendar_reminders` table would recreate the problem.

**Why not extend `scheduled_tasks`?** Scheduled tasks have dispatch semantics (prompt/job execution). Reminders are notification-only events. Mixing them would require every `tick()` to distinguish "should I dispatch this?" from "should I notify about this?"

### D2: Multi-entity junction table `calendar_event_entities`

**Choice:** `calendar_event_entities(event_id, entity_id)` junction with composite PK and FKs to `calendar_events(id)` and `public.entities(id)`.

**Why entity_id not contact_id?** Entities are the identity anchor (RFC 0004). A reminder "Mom's birthday" should link to the Mom entity regardless of whether she has a contact record. Calendar events like "Meeting with Sarah and Tom" link to two entities.

**Why junction not single FK?** Multi-entity support. A meeting involves multiple people. A single FK would require picking one.

### D3: `source_butler` and `source_session_id` on `calendar_events`

**Choice:** Two new columns: `source_butler TEXT NOT NULL` (butler name), `source_session_id TEXT` (nullable, session that created it).

**Why on `calendar_events` not `calendar_sources`?** A single `calendar_sources` row (e.g., the internal_reminders source) may serve events from multiple butlers. The butler attribution belongs on the event, not the source.

**Why `source_session_id` nullable?** Provider-synced events (Google Calendar) have no session. Butler-created events do.

### D4: `title` and `body` columns on `calendar_events`

**Choice:** `title TEXT NOT NULL` (<30 words by convention, not constraint) and `body TEXT` (nullable).

**Why not keep using `metadata JSONB`?** Title and body are universal across all event types. Extracting them from JSONB into proper columns enables indexing, simpler queries, and enforces that every event has a human-readable name.

**Why no character constraint on title?** The <30 word limit is a UX guideline, not a data invariant. Enforcement in MCP tool input validation, not DB constraint.

### D5: `tick(source_butler)` scoping

**Choice:** The calendar module's `tick()` method accepts a required `source_butler` argument. It only evaluates due events where `calendar_events.source_butler` matches.

**Why not evaluate all events?** Multiple butlers can have the calendar module. Without scoping, the relationship butler's tick would fire notifications for the finance butler's payment reminders. Each butler owns its events.

**Why not a global "reminder checker" cron?** That would require a designated butler to own all reminder evaluation, violating the principle that each butler manages its own domain. The `source_butler` scoping is simpler and more correct.

### D6: RRULE replaces relativedelta

**Choice:** Recurring reminders use standard RRULE strings (`RRULE:FREQ=YEARLY`, `RRULE:FREQ=MONTHLY`). The `calendar_events.recurrence_rule` column already supports this.

**Why not keep the old types?** The `recurring_yearly`/`recurring_monthly` enum and `relativedelta` advancement logic is a custom reinvention of RRULE. Calendar events already have RRULE support. Using one system is strictly better.

### D7: Dismissal as instance cancellation

**Choice:** Dismissing a one-time reminder sets `status = 'cancelled'`. Dismissing a recurring reminder cancels the current instance in `calendar_event_instances` (marking it cancelled) while the next instance remains active.

**Why not delete?** Cancellation preserves history. The user can see dismissed reminders in a filtered view.

## Risks / Trade-offs

**[Risk] Data migration complexity** — Reminders must be faithfully migrated from the `reminders` table and facts table into `calendar_events`. Entity linkage must be resolved from `contact_id → contacts.entity_id`.
→ **Mitigation:** Write a reversible Alembic migration. Validate row counts before/after. Keep `reminders` table as backup (renamed `_reminders_backup`) for one release cycle.

**[Risk] Relationship butler manifesto references reminders** — Lines 13, 33, 61 of MANIFESTO.md explicitly mention reminders as a capability.
→ **Mitigation:** Amend manifesto. Reframe from "the butler sets reminders" to "the butler uses the calendar to track important dates and follow-ups." The semantic intent is unchanged; the tool surface shifts.

**[Risk] Breaking change for existing reminder tools** — Any butler skills or scheduled tasks that call `reminder_create`/`reminder_list`/`reminder_dismiss` will break.
→ **Mitigation:** The new calendar module tools use the same names (or aliases). Update all skill references in roster/.

**[Risk] `title` column migration for existing events** — Existing `calendar_events` rows have no `title` column. Adding `NOT NULL` requires a default or backfill.
→ **Mitigation:** Backfill from `metadata->>'title'` or `metadata->>'display_title'` where present. Use `'(untitled)'` as fallback default. Migration sets the default, then removes it.

## Migration Plan

1. **Core migration** (new Alembic version):
   - Add `source_butler TEXT NOT NULL DEFAULT 'unknown'`, `source_session_id TEXT`, `title TEXT NOT NULL DEFAULT '(untitled)'`, `body TEXT` to `calendar_events`
   - Backfill `title` from existing metadata where possible, then drop default
   - Backfill `source_butler` from source metadata where possible, then drop default
   - Create `calendar_event_entities` junction table
   - Create index on `calendar_events(source_butler)`

2. **Reminder data migration** (same or subsequent migration):
   - For each row in `reminders`: INSERT into `calendar_events` with appropriate source, timing, recurrence
   - For each migrated reminder with `contact_id`: resolve `contacts.entity_id`, INSERT into `calendar_event_entities`
   - Delete facts with `predicate = 'reminder'`
   - Rename `reminders` table to `_reminders_backup`

3. **Code changes** (post-migration):
   - Add reminder tools to calendar module
   - Update tick() to accept and filter by source_butler
   - Remove `roster/relationship/tools/reminders.py`
   - Update relationship butler MANIFESTO.md and CLAUDE.md
   - Simplify calendar workspace projection (remove `_project_reminders_source` indirection)

4. **Cleanup** (after one release cycle):
   - Drop `_reminders_backup` table
   - Remove any backward-compat code

## Open Questions

None — all design decisions were resolved in discussion with the project owner.
