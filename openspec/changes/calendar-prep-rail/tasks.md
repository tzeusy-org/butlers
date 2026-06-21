## 1. Prep view + grants migration

- [x] 1.1 Add a core Alembic migration (`core_142_v_prep_contributions.py`, next in chain after `core_141`) creating `calendar.v_prep_contributions` as a UNION ALL view over the contributing specialists' `state` tables, each filtered to `key LIKE 'calendar/prep/%'` and annotated with a hardcoded `butler` string literal — mirroring `core_140_v_overlay_contributions.py`.
- [x] 1.2 Ensure the calendar reader role (`butler_calendar_rw`) exists best-effort and grant SELECT on each contributing specialist's `state` table to that role.
- [x] 1.3 Reuse the optional-schema guard (`_state_table_exists` via `to_regclass`); emit a NULL-returning stub UNION term for any specialist whose `state` table is absent at migration time.
- [x] 1.4 `downgrade()` drops the view and revokes the SELECT grants (reversible, auditable).

## 2. Relationship prep contribution job

- [x] 2.1 Implement `calendar_prep_contribution` deterministic (zero-LLM) job in `src/butlers/jobs/calendar_prep.py`: for each entity-linked event in the lookahead window, resolve attendees (name + Dunbar-tier override letter-mark), durable relationship notes, and last-met from the most recent prior co-attended event; write one envelope per event under `calendar/prep/<event_id>`.
- [x] 2.2 Prune prep envelopes for events that scrolled out of the window (per-event keys, idempotent re-runs).
- [x] 2.3 Register `calendar_prep_contribution` in the existing `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` under `relationship` — no parallel registry.
- [x] 2.4 Add a `calendar_prep_contribution` schedule entry to `roster/relationship/butler.toml` with `dispatch_mode="job"`.
- [x] 2.5 Unit tests: populated envelope shape; honest empty-state (unresolved attendee); empty-when-no-events; prune of stale keys; zero-LLM handler signature.

## 3. Meeting-prep rail read endpoint

- [x] 3.1 Add `query_calendar_prep` + `CalendarPrepRow` to `calendar_workspace_v1.py`, reading `calendar.v_prep_contributions` for a single `calendar/prep/<event_id>` key through one deterministic reader pool (fail-open to `[]`).
- [x] 3.2 Add `CalendarPrepNote` / `CalendarPrepAttendee` / `CalendarPrepResponse` Pydantic models.
- [x] 3.3 Add `GET /api/calendar/workspace/prep/{event_id}` projecting the cached view, merging attendees across contributing butlers by `entity_id`, validating each envelope's `butler` against the view's source column, with an honest empty-state and no LLM / no direct sibling-schema read.
- [x] 3.4 Tests: populated read; honest empty-state; fail-open on missing view; no-direct-read + no-LLM guarantees; cross-butler merge; butler-mismatch guard.

## 4. Validation

- [x] 4.1 `openspec validate calendar-prep-rail --strict` green.
- [x] 4.2 Backend lint/format + targeted pytest green.
