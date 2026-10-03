## Why

A provider plan usage-limit ("You've hit your usage limit") is a property of the whole
provider account, but the runtime treats it as a per-model fault. Each catalog entry
has to fail five times before the circuit breaker excludes it, failover can pick a
sibling entry on the same dead account, an exhausted account is re-probed by live
owner turns every breaker cooldown, and background sweeps keep spending the allowance
the owner's next message needs.

## What Changes

- The failover classifier gives usage-limit rejections their own failure class
  (`usage_limit`), separate from transient rate limits, carrying a reset instant only
  when the provider message states one unambiguously.
- A new `public.provider_allowance_states` table records, per provider account key,
  whether the account is `available`, `exhausted`, or `unknown`, when an exhaustion
  lifts, and whether that reset was parsed or assumed (`default_window`).
- `public.model_catalog.allowance_account` (nullable) names the account an entry draws
  from; NULL means the entry's `runtime_type`.
- Every resolver excludes every entry on an exhausted account until its reset, so an
  owner turn fails over to a different account within one attempt.
- The spawner writes the rejection as outcome `allowance_exhausted` (ignored by the
  breaker) and clears the state on a successful attempt on that account.
- The scheduler defers prompt-mode cron and deadline dispatches whose every candidate
  model sits on an exhausted account, recording `skipped_allowance` and running them
  after the reset instead of failing over to a costlier tier.

## Deferred (tracked on the bead)

The Models-tab and System-verdict countdown with an attempt-row door, the content-blind
`GET /api/settings/models/allowance` endpoint, the owner-turn reserve for background
work, and the canary probe at the default horizon are separate slices.

## Impact

Specs: model-failover, core-spawner, core-scheduler, catalog-token-limits,
database-security. Code: `failover_classifier.py`, `model_routing.py`, `spawner.py`,
`scheduler.py`, migration `core_257`. Owner token quota semantics are unchanged.
