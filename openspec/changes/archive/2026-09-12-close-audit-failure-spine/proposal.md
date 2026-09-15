## Why

The Issues feed groups only audit rows explicitly marked `result = "error"`, but several credential, approval, and model failure writers leave that outcome unset. This makes real operational failures invisible and makes the privileged audit view dominated by low-consequence noise.

## What Changes

- Require the affected credential, approval, and model audit writers to record an explicit success or error outcome, with failure writers recording `result="error"` and safe diagnostic context.
- Repair legacy `action="failed"` audit rows by filling only their missing error outcome.
- Define the privileged audit view as a consequence-oriented allowlist while retaining the full-noise opt-out.
- Add owner-timezone `from_date` and `to_date` calendar-day filters to the audit API and UI, using the same shared bound resolver as session filtering; retain `since` as an ISO timestamp filter.

## Capabilities

### New Capabilities

<!-- None. -->

### Modified Capabilities

- `dashboard-audit-log`: Record meaningful mutation outcomes, support owner-timezone audit day bounds, and make the privileged filter consequence-oriented.

## Impact

- Affects `public.audit_log` outcome provenance through an idempotent core migration.
- Affects the audit API, its shared time-bound utility, credential/model/approval writers, and the Audit Log page filters.
- Affects audit-derived issue grouping because repaired and future failure rows become eligible as `result="error"` evidence.
