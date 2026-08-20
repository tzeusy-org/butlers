## Why

The calendar meeting-prep rail (calendar-prep-rail, merged) surfaces per-attendee
relationship context before upcoming events: notes, Dunbar tier, last-met, and
recent message threads. But it has no awareness of commitments — the owner walks
into a meeting with Sam not knowing they promised to send a book or that Sam
said they'd share a referral.

The commitment-lifecycle change (in progress) builds
`list_entity_commitments(entity_id)` on `public.owner_conditions` filtered by
`metadata->>'class' = 'commitment'`. That query surface is the missing input.
RFC 0026 §Out of Scope explicitly anticipated this integration: "Moment Prep
integration (separate change — consumes commitment query surface)."

## What Changes

### Modified Capabilities

- `calendar-overlay-aggregation`: the relationship prep contribution job extends
  its per-attendee envelope with a `commitments` list — active commitment-class
  `owner_conditions` matched by `counterparty_entity_id`. The prep envelope
  schema gains this field; no new job, state key convention, or cross-schema
  view is needed because `public.owner_conditions` is already readable by the
  relationship role.
- `dashboard-api`: the meeting-prep rail endpoint surfaces the `commitments`
  array per attendee in its response model.

## In Scope

- Extend `PrepAttendee` envelope with `commitments` field
- Query `public.owner_conditions` for commitment-class conditions per attendee
  entity in the existing `calendar_prep_contribution` job
- Extend `CalendarPrep*` API response models with commitment data
- Surface commitment kind, direction, summary, deadline, and escalation level
- Frontend prep rail rendering of commitment chips per attendee

## Out of Scope

- Non-calendar-event prep triggers (e.g. composing a message, reconnection
  suggestions) — broader "Moment Prep" is a separate future change
- Commitment creation or resolution from the prep rail — this is read-only
- Modifying the cross-schema view or creating new migrations — `owner_conditions`
  is already in `public`
- Prose generation or LLM involvement in the prep path

## Impact

- `src/butlers/jobs/calendar_prep.py` (extended: commitment query + envelope)
- `src/butlers/api/models/calendar_workspace.py` (extended: commitment response
  model)
- `src/butlers/api/read_models/calendar_workspace_v1.py` (extended: pass
  commitment data through)
- `frontend/src/components/` calendar prep rail (extended: commitment rendering)
- Tests: prep job commitment coverage, API response shape, empty-state behavior

## Design

See `design.md` in this changeset.

## Dependencies

- `commitment-lifecycle` change (specifically tasks 3-4: commitment helper module
  and `list_entity_commitments` query surface). Task 1 (`resolve_condition`) is
  not a direct dependency but is in the prerequisite chain.
