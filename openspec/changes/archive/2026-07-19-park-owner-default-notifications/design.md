## Context

`notify()` already builds a complete `notify.v1` envelope and already has a
durable per-butler deferral substrate. Its earlier `delivery_preferences` gate
uses that substrate correctly. A later, narrower owner-default gate instead
records `suppressed_quiet_hours` or `suppressed_context_bus` and discards the
content. The existing scheduler flushes stored envelopes through the standard
Switchboard→Messenger delivery plane, retains pending rows after transport
failure, and coalesces due rows without changing their durable provenance.

This change corrects only that later owner-default gate. It must preserve
approval-request pushes and the existing approval quiet-hours helper contract,
and it must not add a new delivery engine or cross-schema ownership path.

## Goals / Non-Goals

**Goals:**

- Preserve a full, resolved `notify.v1` envelope for each eligible policy or
  context hold in the originating butler schema.
- Give every chosen hold a deterministic UTC delivery anchor and a linked,
  durable attention-ledger decision.
- Preserve direct-target, high-priority, other-intent, delivery-preferences,
  approval-request, retry, and scheduler semantics outside the narrow gate.
- Ensure the active context-bus producer artifacts cannot later sync stale
  drop/suppression semantics back into the canonical contract.

**Non-Goals:**

- Changing quiet-window inclusivity, preferences/configuration, context
  producers, wake evidence, broker catch-up, morning composition, cron, schema,
  ACLs, dependencies, or third-party egress behavior.
- Re-gating a stored envelope during scheduler flush or changing the existing
  24-hour expiry/retry/coalescing policy.

## Decisions

### D1 — Use the existing originating-schema deferred envelope

The new branches call `insert_deferred_notification()` after the existing full
envelope is resolved. The row remains in the calling butler's schema, while the
ledger stores only the row id in `notification_ref`; it never duplicates message
content into `public`. This honors RFC 0006 isolation and makes the established
scheduler flush the single delivery engine.

Rejected: a public queue, a bespoke morning queue, or storing message text in
the ledger. Each would expand ownership or privacy surface without solving a
gap in the existing substrate.

### D2 — Compute anchors in deterministic helpers

`core.approvals_policy` gains a reusable policy quiet-hours delivery-anchor
helper. It retains `approval_push_deliver_at()` as a compatibility wrapper, so
approval-request pushes keep the exact current behavior. A malformed policy
timezone is treated as an unreadable policy and fails open; a valid policy
anchor is the first whole local hour after the existing inclusive quiet-window
end.

`core.attention_ledger` gains a structured suppressing-context result while
retaining `get_suppressing_context_signal()` as a string compatibility wrapper.
It preserves DND-before-sleeping as the ledger reason, but its `wake_at` is the
latest `expires_at` among every active DND/sleeping signal. A selected DND must
not accidentally wake delivery while another sleeping or DND assertion remains
active.

Rejected: changing endpoint inclusivity or producer wake calculation in this
slice; those are separately scoped behavior changes.

### D3 — Fail closed for a chosen hold, fail open for eligibility reads

Policy/context lookup failures retain the existing fail-open immediate path.
Once a branch has chosen quiet-hours/context parking, a deferred-row persistence
failure returns a retryable error and logs a best-effort `failed` ledger event;
it neither sends immediately nor returns a destructive suppressed result.
Conversely, a ledger-write failure after the queue write cannot discard the
queued row or change the `deferred` response.

### D4 — Do not re-evaluate the hold at flush time

The scheduled notifier invokes the stored envelope directly. `deliver_at` is
the durable decision made at admission; transport failure leaves the row pending
for the established retry behavior and a successful status transition remains
the replay guard. Adding a second policy/context read at flush would make an
already accepted envelope unpredictable and would duplicate scheduler policy.

### D5 — Reconcile active OpenSpec state explicitly

The completed `context-bus-producers` change is not archived in this work. Its
sleep-producer scenario is revised from a `suppressed_context_bus` result to a
durably deferred one so its later sync cannot restore stale behavior. This new
change carries the canonical delta that defines the complete admission and
delivery contract. The in-progress decision-loop change is not modified;
helper extraction is backward-compatible with its approval-push behavior.

## Risks / Trade-offs

- **Queue write failure could create silent content loss** → return an explicit
  retryable error, log safely without message text, and test that no transport
  call occurs.
- **Concurrent context writers could shorten a hold** → anchor on the maximum
  active suppressor expiry rather than only the highest-priority reason.
- **A helper refactor could alter approval pushes** → retain the public helper
  as a wrapper and run its existing behavior tests unchanged.
- **Existing deferred expiry remains a finite retention limit** → document that
  this change prevents intentional admission-time dropping, not every existing
  expiry outcome.

## Migration Plan

No database migration is required. Deploying the code changes only future
eligible notifications; previously suppressed content cannot be reconstructed.
Rollback restores the former direct suppression behavior for new calls while
already persisted rows continue through the unchanged scheduler. No data repair
or queue conversion is needed.

## Open Questions

None. Predicate/config consolidation, wake-evidence production, and composed
morning delivery are intentionally deferred to the parent slices.
