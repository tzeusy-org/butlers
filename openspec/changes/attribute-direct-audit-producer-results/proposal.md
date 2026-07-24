## Why

Several current direct audit producers write rows with a null `result`, so the
operator history records that an event happened without saying whether the
producer observed, delivered, detected, or escalated it. The breaker-open
notification is one concrete case: a confirmed owner delivery is not
attributed as delivered in its audit row.

## What Changes

- Require every current direct production `audit_router.append` producer to
  pass an explicit, producer-meaningful `result`.
- Record `result="delivered"` when the model-breaker open notification is
  confirmed delivered, while preserving its existing delivery and debounce
  behavior.
- Add focused source-level completeness coverage and producer regressions so a
  future direct writer cannot silently omit its result.
- Preserve the optional generic `audit_router.append` argument for compatible
  callers outside this direct-producer sweep.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-audit-log`: direct audit producers must preserve a meaningful
  producer outcome while generic router compatibility remains optional.

## Impact

This affects the direct append boundaries in core attention and background-job
producers, focused Python tests, and the `dashboard-audit-log` delta. It does
not add a migration, rewrite historical rows, alter notification/retry
behavior, normalize unrelated result vocabulary, or make `result` globally
mandatory at the audit router API.
