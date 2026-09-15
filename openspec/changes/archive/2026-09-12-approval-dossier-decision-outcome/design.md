## Context

`pending_actions` keeps terminal status, decision provenance, and an optional
execution result. The append-only `approval_events` audit spine is the
authoritative source for an explicit rejection reason. The dashboard detail
mapper currently does not expose either outcome to the dossier, while the
module's established presentation redactor already protects execution errors.

This is a dashboard/API detail change only. It must retain the existing
approval-state machine and work against every registered approval pool,
including a legacy or degraded pool that cannot provide an audit-event row.

## Goals / Non-Goals

**Goals:**

- Return `denial_reason` from the newest immutable `action_rejected` event for
  the action, or `null` when no readable event supplies a reason.
- Return `execution_result` only after applying the existing approval-result
  redaction contract.
- Render the decision and safe execution outcome without removing
  `decided_by` or `decided_at`.
- Keep the dossier retry affordance exactly aligned with the existing safe
  dispatch state: `status == "approved" && execution_result == null`.

**Non-Goals:**

- A new `stalled` state, lane, population count, or recovery dashboard.
- Abandon semantics, a status transition, retry of an executed failure, or any
  executor change.
- A migration or duplicate stored rejection-reason column.
- Client-side redaction, raw error rendering, secret exposure, or a broad API
  refactor.

## Decisions

### D1 — Derive denial reason from the immutable audit event

For the one requested detail record, the API performs a bounded lookup of the
latest `action_rejected` event and reads its structured reason. It returns
`null` for legacy/no-event rows and treats unavailable event data as an
optional detail rather than failing the dossier response.

- Rejected: parsing the human-readable `decided_by` string. It is presentation
  provenance rather than a stable structured reason.
- Rejected: adding a `pending_actions.denial_reason` column. It duplicates the
  immutable audit source and requires migration/backfill work outside this
  slice.

### D2 — Redact execution output at the API boundary

The detail mapper passes the persisted execution result through the existing
approvals redaction helper before constructing the response model. The
frontend receives and renders only that redacted payload.

- Rejected: frontend-only filtering. Other API consumers would still receive
  raw execution errors and presentation logic would be duplicated.
- Rejected: a new redaction implementation. The module already defines the
  project-wide result-redaction contract.

### D3 — Use the raw detail-state predicate for Retry

The dossier renders its Retry control only when the response reports an
`approved` status and a null execution result. The existing retry endpoint
continues to own server-side validation and dispatch semantics.

- Rejected: retrying any failed-looking outcome. An executed failure is a
  terminal execution record and broadening replay is explicitly out of scope.

## Risks / Trade-offs

- **[A pool lacks readable audit events]** → The one optional provenance lookup
  fails closed to `denial_reason: null`; the main dossier remains available.
- **[Persisted execution errors contain sensitive text]** → The API reuses the
  established redactor before serializing the result, with API and UI
  regressions proving raw error text is absent.
- **[A UI predicate drifts from dispatch safety]** → Tests cover eligible,
  executed, and rejected examples; backend retry validation remains unchanged.

## Migration Plan

No schema migration or data backfill is required. Deploying the API and
frontend together enriches new and existing detail responses; older/legacy
records render null outcome fields safely. Rollback consists of reverting the
API fields and their optional dossier sections, without data cleanup.

## Open Questions

None. The existing audit-event and redaction contracts determine the behavior.
