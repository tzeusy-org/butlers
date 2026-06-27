## 1. Switchboard read grants migration

- [x] 1.1 Add a core Alembic migration (`core_143_email_butlers_switchboard_read_grants.py`, next in chain after `core_142`) granting `butler_messenger_rw` and `butler_travel_rw` USAGE on the `switchboard` schema and SELECT on its tables (+ default privileges), mirroring `core_077`.
- [x] 1.2 All statements best-effort (tolerate missing roles/tables/schema); `downgrade()` revokes the grants.

## 2. Email/message-context prep contribution job

- [x] 2.1 Implement `run_email_calendar_prep_contribution(pool, job_args, *, butler_name)` in `src/butlers/jobs/calendar_prep.py`: for each entity-linked event in the lookahead window, read recent inbound `email`-channel threads per attendee from `switchboard.message_inbox` (grouped by resolved sender entity), and write one envelope per event under `calendar/prep/<event_id>` populating each attendee's `message_context`.
- [x] 2.2 Add messenger/travel wrappers (`run_messenger_calendar_prep_contribution`, `run_travel_calendar_prep_contribution`).
- [x] 2.3 Write an envelope only when at least one attendee has message context (honest empty-state); cap threads per attendee; fail open when `switchboard.message_inbox` is unreadable; prune stale per-event keys.
- [x] 2.4 Register `calendar_prep_contribution` under `messenger` and `travel` in the existing `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` — no parallel registry.
- [x] 2.5 Add a `calendar_prep_contribution` schedule entry to `roster/messenger/butler.toml` and `roster/travel/butler.toml` with `dispatch_mode="job"`.
- [x] 2.6 Unit tests: populated message_context; event-with-no-threads skipped; thread cap + subject fallback; fail-open on unreadable inbox; stale-key prune; zero-LLM handler signatures.

## 3. Read endpoint surfaces merged message context

- [x] 3.1 No endpoint code change — the existing `GET /api/calendar/workspace/prep/{event_id}` entity-keyed merge unions the email envelope's `message_context` from `calendar.v_prep_contributions` (which already includes the messenger/travel `state` tables, core_142).
- [x] 3.2 Test: the read merges a messenger envelope's `message_context` into the relationship-sourced attendee by `entity_id` (existing `test_prep_rail_merges_message_context_across_butlers`).

## 4. Validation

- [x] 4.1 `openspec validate calendar-prep-message-context --strict` green.
- [x] 4.2 Backend lint/format + targeted pytest green.
