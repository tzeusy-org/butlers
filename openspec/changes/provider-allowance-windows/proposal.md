## Why

A provider plan usage-limit ("You've hit your usage limit") is a property of the whole
provider account, but the runtime treats it as a per-model fault. Each catalog entry
has to fail five times before the circuit breaker excludes it, failover can pick a
sibling entry on the same dead account, an exhausted account is re-probed by live
owner turns every breaker cooldown, and background sweeps keep spending the allowance
the owner's next message needs.

RFC 0038 now governs the target runtime. After the switch, prompt-mode schedules submit
obligation admissions (D5), the goal runner selects each invocation's model by role and
tier (D16), a retryable runtime failure gets at most one relaunch on the same tier's
fallback model (D14), and every goal carries a reserved budget (D10). This amendment
applies the `re-scope` row for this change in the
[generalist collision dispositions](../generalist-lead-focused-delegation/collision-dispositions.md)
before either change archives. It changes no RFC 0038 decision.

## What Changes

- The failover classifier gives usage-limit rejections their own failure class
  (`usage_limit`), separate from transient rate limits, carrying a reset instant only
  when the provider message states one unambiguously. After the switch this class is the
  runtime failure taxonomy D14 reads.
- A new `public.provider_allowance_states` table records, per provider account key,
  whether the account is `available`, `exhausted`, or `unknown`, when an exhaustion
  lifts, and whether that reset was parsed or assumed (`default_window`).
- `public.model_catalog.allowance_account` (nullable) names the account an entry draws
  from; NULL means the entry's `runtime_type`.
- Every resolver excludes every entry on an exhausted account until its reset, so an
  owner turn fails over to a different account within one attempt. After the switch the
  exclusion gates invocation selection inside a goal: the model for a lead, a child, a
  D14 relaunch or an escalation. It never admits a new goal or resets the goal's budget.
- The spawner (after the switch, the goal runner for goal invocations) writes the
  rejection as outcome `allowance_exhausted` (ignored by the breaker) and clears the state
  on a successful attempt on that account.
- The scheduler defers prompt-mode cron and deadline dispatches whose every candidate
  model sits on an exhausted account, recording `skipped_allowance` and running them
  after the reset instead of failing over to a costlier tier. After the switch the
  deferral happens before the obligation admission is submitted, so a deferred occurrence
  has no goal and no budget reservation.
- Account allowance stays distinct from owner token quota and from goal budgets, and a
  usage-limit is never a breaker signal.

## Moved out of this change

- The owner-turn reserve for background work (former task 2.2, `bu-q7vx1q.52`) is
  superseded by the owner reserve in the monthly spend lanes at goal admission
  (`bu-jv1moa.1`).
- The probe at the default horizon when the reset is unknown (task 4.1) stays here,
  re-targeted onto the goal runtime by `bu-q7vx1q.53` as a D18 model function.

## Impact

Specs: model-failover, core-spawner, core-scheduler, catalog-token-limits,
database-security, dashboard-model-settings. Code before the switch:
`failover_classifier.py`, `model_routing.py`, `spawner.py`, `scheduler.py`, migration
`core_257`, and the allowance endpoint and Models-tab strip. After the switch the goal
runner (`bu-u5s4w8`) and the daemon's obligation submission take the spawner and
scheduler seams named in `design.md`. Owner token quota semantics are unchanged.

## Re-scope and delivery boundary

Every requirement name and scenario stays; each spawner, scheduler and resolver requirement
now also states its goal-runtime successor, and the six requirements on the four shared
capabilities gain trace lines. The goal runner, admission seam and probe are delivered by their own beads; this
documentation amendment ships no code, tests, archive or runtime activation. Tests: +0 ~0 -0.
