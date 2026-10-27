## Why

Parking an approval action is currently durable, but owner notification is a
best-effort post-commit side effect. A process crash, quiet-hours hold, or
ambiguous provider result can therefore leave a still-pending action without a
recoverable, truthful notification record. Approval is a safety boundary: a
notification worker may recover attention, but it must never decide, execute,
or otherwise mutate the parked domain action.

## What Changes

- Add a schema-local, one-to-one durable approval-delivery intent root created
  in the same transaction as every new pending action, with a stable logical
  action key, resolved RFC 0021 admission result, fenced presentation
  generations, and no provider credential or raw provider error persisted.
- Add a deterministic notification-only worker with leased, fenced claims,
  bounded at-least-once retries/backoff, stale-claim recovery, and a
  pre-provider durable handoff marker.
- Preserve the canonical dashboard defer verb by atomically scheduling the
  next presentation generation for `now + hours`; the logical action key stays
  stable, while provider idempotency is scoped to a deterministic presentation
  key and a worker can never reschedule it.
- Make burst digest delivery cohort-owned so a terminal fourth action cannot
  strand fifth-or-later collapsed actions; the digest uses the cohort's own
  recovery subject rather than the fourth action's key.
- Extend the notification boundary to classify a trusted presentation handoff
  as confirmed, definitively safe to retry, or ambiguous. Bind every
  non-secret key to the transport-authenticated issuer/owning schema/mode and
  quarantine an uncertain post-start handoff instead of sending speculatively
  again.
- Atomically cancel outstanding action presentations when an action is decided
  or expires, while preserving the immutable action decision semantics. The
  worker changes presentation and attempt records only; it never approves,
  rejects, expires, executes, or mutates a parked action.
- Preserve RFC 0021's ordinary one-notification-per-action rule, explicit
  authenticated-defer re-presentation exception, quiet-hours exact-release,
  control-plane budget exemption, and burst-digest/collapse semantics without
  reusing or changing the generic deferred-notification queue.
- Add safe state/reason vocabularies, retention rules, API/dashboard truth,
  and stuck/ambiguous observability. Recovery records are excluded from generic
  notification history, retry, escalation, acknowledgement, and stored-envelope
  reconstruction. Recovery-mode delivery also bypasses outbound
  `switchboard.message_inbox` persistence, so it cannot enter generic
  conversation or LLM history. Existing push-emission rows remain legacy
  evidence; there is no historical-intent backfill or replay.

## Capabilities

### New Capabilities

- `approval-delivery-intent-recovery`: The schema-local durable intent,
  fenced notification-only recovery worker, provider-handoff classification,
  cancellation, retention, and rollout contract.

### Modified Capabilities

- `module-approvals`: Every pending-action admission atomically creates its
  delivery intent and every terminal action transition fences or cancels it.
- `core-notify`: `notify.v1` carries a trusted recovery presentation shape,
  derives issuer/schema identity from the authenticated transport, returns a
  normalized safe handoff classification, and fences generic controls/history.
- `butler-messenger`: The actual provider boundary durably reconciles a
  trusted approval presentation tuple; it does not resurrect the retired,
  unwired generic delivery-tracking tables.
- `dashboard-approvals`: Approval API and UI expose delivery recovery state
  and defer's scheduled presentation truth without leaking secrets, recipients,
  raw provider responses, or implying that an ambiguous handoff was never sent.

## Impact

This is an OpenSpec-only planning change. Future implementation affects the
Approvals module schema and parking/decision paths, daemon-owned background
worker lifecycle, notify/routing/Messenger delivery boundary, approval API and
dashboard, metrics, and integration/contract tests. It makes no source
implementation, migration execution, live replay, owner message, approval
decision, entity/fact mutation, deployment, or runtime change.
