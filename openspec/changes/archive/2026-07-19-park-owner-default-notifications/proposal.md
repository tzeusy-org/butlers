## Why

Routine owner-default notifications suppressed by approvals-policy quiet hours
or an active DND/sleeping context currently lose their content. The result is
visible only as a transient tool status and a suppression ledger row, even
though the existing per-butler deferred-notification substrate can preserve and
deliver the same full `notify.v1` envelope safely.

## What Changes

- Replace eligible implicit-owner `notify()` policy/context drops with durable
  deferred envelopes in the originating butler schema.
- Compute a deterministic policy wake anchor without changing the current
  quiet-window predicate, and use the latest active suppressing-context expiry
  as the context wake anchor.
- Link the chosen deferral to `public.attention_ledger`; make a failed durable
  write retryable without falling through to immediate delivery.
- Reconcile stale suppression wording in the completed-but-unarchived
  context-bus producer change so a later sync cannot restore drop semantics.
- Update the applicable notify, delivery, scheduler, spawner, approvals, and
  context contracts, plus RFC 0011 and outbound-flow topology documentation.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `core-notify`: owner-default quiet-hours and context handling becomes durable
  deferral with preserved envelopes and explicit failure semantics.
- `time-aware-delivery`: existing deferred-notification storage also carries
  owner-default policy/context holds with a caller-provided delivery anchor.
- `core-scheduler`: the existing flush path is the sole delivery engine for
  these stored envelopes and preserves its retry/coalescing behavior.
- `core-spawner`: direct owner-default `deferred` outcomes remain delivered for
  interactive delivery accounting; retired suppression statuses are no longer
  the expected policy/context outcome.
- `dashboard-approvals`: the policy documentation no longer describes routine
  owner-default pages as silently dropped.
- `context-bus`: suppressing DND/sleeping signals supply a TTL-bounded wake
  anchor that respects all concurrently active suppressors.

## Impact

- Code: `core_tools/_notifications.py`, `core/approvals_policy.py`, and
  `core/attention_ledger.py`; existing temporal delivery and scheduler code are
  reused rather than redesigned.
- Contracts: `notify.v1` content remains unchanged; only the eligible result
  behavior changes from `suppressed_*` to the existing `deferred` result shape.
- Storage: no migration, dependency, cross-schema write, or new delivery path.
- Documentation: RFC 0011, core specs, and outbound-flow topology are updated
  in this change; approval-request push behavior remains compatible.
