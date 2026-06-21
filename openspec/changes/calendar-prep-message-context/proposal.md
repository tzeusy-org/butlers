## Why

The meeting-prep rail (`calendar-prep-rail`, bead `bu-jvpfv1` / PR #2657) shipped
the prep envelope, the cross-schema `calendar.v_prep_contributions` view, and the
`GET /api/calendar/workspace/prep/{event_id}` read — including a per-attendee
`message_context` slot that the read merges across butlers. But that change
explicitly left the message-context panel **unpopulated**: the email/message-owning
butlers (messenger, travel) did not yet WRITE prep contributions, so the slot was
always empty. This change is the `(b) message-context panel` split of `bu-jvpfv1`.

The doctrine constraint is the whole point: the calendar workspace fans out across
ALL calendar-owning butlers, most of which have no email module. So message/email
context MUST be sourced via the RFC-0010 deterministic contribution-job path —
precomputed by the butler that *owns* the email channel, into its own cached
`state` — NOT a direct cross-butler Gmail read at request time and NOT a per-open
LLM session (RFC-0020). Only messenger/travel co-own calendar + email.

## What Changes

### Modified capability: `calendar-overlay-aggregation`

- **Email/Message-Context Prep Contribution Job.** The email-owning butlers
  (messenger, travel) gain a deterministic (`dispatch_mode="job"`, zero-LLM)
  `calendar_prep_contribution` job that, for each entity-linked event in the
  rolling lookahead window, precomputes the recent email threads each attendee
  wrote and writes one prep envelope per event under `calendar/prep/<event_id>`
  into the butler's OWN `state` — populating the `message_context` slot the
  relationship-sourced envelope reserves. The recent-threads precompute reads the
  persisted inbound-message store (`switchboard.message_inbox`) during the
  scheduled job; it is keyed by the resolved sender entity so it joins the prep
  merge on the same `entity_id`. To preserve the prep rail's honest empty-state,
  an envelope is written only for events where at least one attendee has recent
  message context; events with none are skipped and any stale key pruned. The job
  is registered in the **existing** `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` under
  `messenger` and `travel` and scheduled via each `butler.toml` — no parallel
  scheduler.

### Modified capability: `dashboard-api`

- **Prep Rail Surfaces Merged Message Context.** `GET /api/calendar/workspace/prep/{event_id}`
  (unchanged code) now surfaces a populated `message_context` for an attendee
  when an email-owning butler has contributed one: the existing entity-keyed merge
  unions the relationship envelope (attendee + notes + last-met) with the
  email envelope (message context) read from the same `calendar.v_prep_contributions`
  view. Still no direct cross-butler read and no per-open LLM session.

## Impact

- **New Alembic migration** `core_143_email_butlers_switchboard_read_grants.py`
  (next in the core chain after `core_142`): grants `butler_messenger_rw` /
  `butler_travel_rw` SELECT/USAGE on the `switchboard` schema so the precompute
  job can read `switchboard.message_inbox`. Mirrors `core_077` (the relationship
  switchboard read grant); best-effort and reversible.
- **New job functions** in `src/butlers/jobs/calendar_prep.py`
  (`run_email_calendar_prep_contribution` + messenger/travel wrappers), registered
  under `messenger` and `travel` in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY`
  (`src/butlers/scheduled_jobs.py`).
- **Schedules** added to `roster/messenger/butler.toml` and
  `roster/travel/butler.toml` (`calendar_prep_contribution`, `dispatch_mode="job"`).
- **No change to the read endpoint code, the prep view, or provider writes**, and
  no LLM in the read or precompute path. The `calendar.v_prep_contributions` view
  already unions the `messenger` / `travel` `state` tables (core_142).

## Out of Scope

- **Outbound message context.** Only inbound threads (messages the attendee wrote)
  are surfaced; attributing outbound (owner-sent) messages to an attendee is a
  follow-up.
- **Non-email channels.** Telegram/WhatsApp thread context for an attendee is out
  of scope for this change (email is the calendar-attendee channel today).
- **FE prep-rail rendering of the message panel.** The frontend rail UI is a
  separate FE bead; this change populates the read contract it consumes.
- **Batched pre-rendered narrative.** Any prose remains deferred (RFC-0020 §Decision).
