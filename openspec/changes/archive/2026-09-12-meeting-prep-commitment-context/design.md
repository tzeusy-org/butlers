## Context

The calendar meeting-prep rail precomputes per-event attendee context via the
relationship butler's `calendar_prep_contribution` deterministic job. The prep
envelope already carries notes, Dunbar tier, last-met, and message context per
attendee. Commitment-class `owner_conditions` (RFC 0026) provide the missing
domain: what the owner owes or is owed by each attendee.

This change adds a `commitments` field to the prep envelope by extending the
existing job — no new job, migration, or cross-schema view.

## Goals / Non-Goals

**Goals:**

- Surface active commitment-class owner conditions per attendee in the prep
  envelope, ordered by escalation severity.
- Maintain the prep rail's deterministic, zero-LLM, fail-open contract.
- Render rule-separated commitment rows in the frontend prep rail component.

**Non-Goals:**

- Querying `owner_conditions` at request time (all data precomputed).
- Creating or resolving commitments from the prep rail.
- Broader "Moment Prep" triggers beyond calendar events.
- New migrations or schema changes.

## Decisions

### Extend the existing job, not a new contributor

The relationship prep job already reads `public.entities` (which owns
`owner_conditions` via `public` schema). Adding a per-attendee query to the
same job avoids: a second state-store write per event, merge complexity at read
time, and a second scheduled job entry. The commitment query is a single SQL
statement filtered by `metadata->>'class' = 'commitment'` and
`metadata->>'counterparty_entity_id'`, with a cap and an ORDER BY escalation.

### Commitment fields are denormalized into the envelope

The prep envelope carries `kind`, `direction`, `summary`, `deadline`,
`escalation_level`, and `fingerprint` per commitment — enough for the frontend
to render a row without a follow-up API call. `fingerprint` is included so a
future iteration could link to a commitment detail view.

### Fail-open on commitment query failure

If the `owner_conditions` query fails (table not yet migrated, connection error),
the job writes the envelope with `commitments: []` per attendee and logs a
warning. The existing prep context (notes, tier, last-met, messages) is
unaffected. This follows the established pattern for message-context fail-open.

### Cap per attendee, escalation-first ordering

`MAX_COMMITMENTS_PER_ATTENDEE = 10` prevents envelope bloat for high-commitment
contacts. Ordering by `escalation_level DESC` surfaces the most urgent items
first — the prep rail is a glance surface, not a comprehensive list.

## Risks / Trade-offs

- **Stale commitment data.** The prep job runs on a schedule (not per-request),
  so a commitment resolved between job runs still appears in the envelope until
  the next run. Acceptable: the prep rail is already eventually-consistent for
  notes and last-met.
- **Large envelope for high-commitment contacts.** Capped at 10 commitments per
  attendee; the vast majority of contacts will have 0–2.
- **Dependency on commitment-lifecycle tasks 3–4.** The `list_entity_commitments`
  helper is not strictly required — the job can query `owner_conditions` directly
  with the metadata filter. But using the helper when available ensures the
  confidence threshold and metadata validation are applied consistently.

## Verification

- Prep job writes commitments for attendees with active commitment-class
  conditions; empty list otherwise.
- API response model includes `commitments` array per attendee.
- Backward compatibility: pre-commitment envelopes without a `commitments` field
  normalize to `[]` in the API response.
- Fail-open: commitment query failure does not affect existing prep context.
- Frontend: rule-separated commitment rows render with kind icon, direction
  indicator, summary, deadline when present, and the established `L0` through
  `L3` escalation label; `L2` and `L3` rows are visually emphasized.
