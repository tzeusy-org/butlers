## Why

Owner-attention suppression currently has three incompatible quiet-window
implementations and two configuration stores. In particular, the core path
treats the configured end hour as inclusive while the insight broker does not,
so a policy whose owner expects attention to resume at 08:00 can still defer
attention until 09:00.

The durable owner-default parking work now makes the policy boundary visible
across notifications, insights, approvals, and sleep context. The boundary and
its authority need to be unambiguous before more callers depend on it.

## What Changes

- Define `public.approvals_policy` as the single global **Owner Attention
  Policy** authority for quiet-window start, end, and IANA timezone.
- Make the policy an end-exclusive interval, `[quiet_start, quiet_end)`, with
  the exact configured end as the next delivery/context-expiry anchor.
- Add a guarded, rerun-safe core migration that preserves valid legacy insight
  quiet settings only when the canonical policy is incomplete, then removes the
  legacy quiet columns while keeping insight verbosity and budget settings.
- Route listed owner-attention readers through one timezone-aware shared
  predicate and anchor, retaining fail-open behavior for absent, incomplete, or
  invalid persisted policy data.
- Keep `GET/PUT /api/approvals/policy` and its payload shape stable while
  validating paired hours and IANA zones, and name the dashboard control for
  its owner-attention scope.
- Reconcile the relevant OpenSpec contracts, RFC amendments, topology, docs,
  and regression coverage.
- **BREAKING**: legacy `public.insight_settings.quiet_start`, `quiet_end`, and
  `quiet_timezone` are removed after guarded migration; runtime code no longer
  reads them.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `core-notify`: owner-default notification suppression uses one global,
  end-exclusive Owner Attention Policy and an exact delivery anchor.
- `dashboard-approvals`: the stable policy endpoint validates complete IANA
  policy pairs and the dashboard describes its owner-attention scope.
- `proactive-insight-engine`: routine insights read the canonical policy rather
  than broker-owned quiet fields, while urgent insights retain their bypass.
- `context-bus`: sleep context derives its expiry from the shared exact policy
  anchor.
- `time-aware-delivery`: durable owner-default holds preserve their stored
  policy-derived timestamp and are not re-gated on scheduler dispatch.
- `module-approvals`: approval-request pushes use the same exact policy end
  without changing pending-action expiry semantics.

## Impact

- Core policy helpers and direct readers in attention ledger, decision review,
  model-breaker, fleet-halt, notification, insight broker, and sleep-context
  code.
- The public policy API model/router and the Approvals dashboard copy.
- A core Alembic migration plus migration, unit, integration, API, frontend,
  and regression tests.
- RFCs 0006, 0007, 0009, 0011, 0019, and 0021; relevant topology and active
  OpenSpec delta documentation.
- This change deliberately does **not** add broker catch-up, wake/cron work,
  digest changes, secrets lifecycle changes, retention redesign, or policy
  re-gating of already durable scheduler rows. Per-butler
  `delivery_preferences` remains separate and unchanged.
