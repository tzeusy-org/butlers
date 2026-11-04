## Context

`token_limits` is an owner-set budget on one catalog entry; a provider allowance is the
provider's own cap on an account that several catalog entries (and runtimes) may share.
They are different failures with different recoveries, so they get different state.

RFC 0038 replaces the spawner's reasoning sessions with goals. The collision disposition for
this change reads: allowance gates obligation deferral and invocation selection within a
goal, and fallback follows D14. The decisions below keep the landed pre-switch behavior and
name the goal-runtime seam that takes over each part at the switch.

## Decisions

- **Account key**: `COALESCE(model_catalog.allowance_account, model_catalog.runtime_type)`.
  No backfill is needed and a shared plan across runtimes is expressible per entry.
- **Reset honesty**: only an epoch, an ISO-8601 instant with an offset, or "in N
  hours/minutes" is parsed. A bare clock time ("try again at 12:25 PM") carries no
  timezone, so it is not guessed; the state records `default_window` (one hour). A
  parsed instant outside (now, now + 8 days] is discarded.
- **Merge rule**: concurrent rejections keep `GREATEST(reset_at)`; a default-window guess
  never shortens a parsed reset; repeats only refresh `last_rejection_attempt_id`.
- **Exclusion lifts by itself** when `reset_at` passes (the CTE compares to `now()`), and
  early on a successful attempt on that account. `unknown` is never treated as
  exhausted by routing and never rendered as available by readers.
- **Breaker**: the attempt outcome is `allowance_exhausted`; the breaker counts only
  `runtime_failure` and `success`, so the rejection neither trips nor resets it.
- **Scheduler**: deferral applies to prompt-mode work only (job-mode work uses no model).
  The check fails open: a lookup error never freezes a healthy schedule.
- **Atomicity**: the attempt row and the state upsert are two statements; the upsert is
  idempotent and best-effort, so a lost upsert costs at most one more rejected probe.

## Goal-runtime decisions

- **Invocation selection inside a goal (D16, D14).** The goal runner resolves each
  invocation's model with the same account exclusion: a lead, a child, the one D14 relaunch
  and a `goal.escalate` lead. Allowance only chooses which model runs. It never admits a new
  goal, changes origin, ceiling, grants or exposure, or adds to or resets the D10 budget.
- **Fallback follows D14.** A trusted `usage_limit` is a retryable class. The runner relaunches
  the failed invocation at most once, on a same-tier model whose account is not exhausted,
  and only when the broker recorded no capability call made with the failed invocation's
  token. The relaunch counts against the invocation budget and a relaunched lead advances the
  epoch. With no unexhausted same-tier candidate, or when the relaunch also fails, a child ends
  `failed` and a lead ends the goal `failed`. Nothing waits inside a goal for a reset: that would
  hold a lease and budget for an unbounded time, and D14 offers no such path.
- **Obligation deferral before admission (D5).** The existing daemon check
  (`allowance_deferral` in `model_routing.py`, called by the cron and deadline passes in
  `scheduler.py`) stays the seam. After the switch it runs before the daemon submits the
  obligation admission, over the candidates the obligation's lead could resolve to. A deferred
  occurrence submits nothing, so admission writes no decision, goal row or budget reservation,
  and the later dispatch is that occurrence's only admission. [decision] The daemon seam was
  chosen over a new admission refusal reason because it reuses landed code, needs no new
  admission vocabulary, and keeps admission free of model-catalog reads.
- **Owner goals are not deferred.** Only scheduled prompt work defers. An owner goal falls
  through to another account by invocation selection, as an owner turn does today.
- **Writer after the switch.** The goal runner writes the `allowance_exhausted` attempt row,
  with `invocation_id` and `goal_id` (D17), and the allowance upsert and clear. The spawner keeps
  them only for model use outside goals that still runs through it.
- **Three ledgers stay apart.** Allowance is the provider's cap; `token_limits` is the owner's
  per-entry quota; the D10 budget is the goal's own limit. None writes another, and no allowance
  failure is a breaker signal.
- **Fail-open deferral is not a budget check.** D10 makes every budget check fail closed.
  Allowance is provider availability, not a budget or an authority check, so a lookup error
  still admits normally; the invocation then meets the resolver exclusion or a rejection.

## Boundaries

| Boundary | Behavior | Owner |
| --- | --- | --- |
| Failure | Upsert errors never raise out of failover. Deferral lookup errors fail open. No unexhausted candidate ends the invocation per D14. | `model_routing.py`, `spawner.py`; goal runner `bu-u5s4w8` |
| Concurrency | Rejections merge with `GREATEST(reset_at)`. Deferral is an optimistic update on the observed `next_run_at`. Admissions deduplicate per occurrence. A success that started before a rejection can clear it (Risks). | `model_routing.py`, `scheduler.py`; admission `goal_admission.py` |
| Replay | A repeated rejection only refreshes the row. A resubmitted admission returns the existing goal. A relaunch never replays work: a broker-recorded call suppresses it. | `model_routing.py`; `goal_admission.py`; goal runner `bu-u5s4w8` |
| Budget and authority | Allowance never writes quota, budget or breaker rows, and never changes grants, ceiling or exposure. Relaunches count against the invocation budget. | goal runner `bu-u5s4w8`; spend lanes `bu-jv1moa.1` |
| Rollback | Before the switch the landed spawner and scheduler path is the behavior. The cutover rollback restores it unchanged, because the table, column and classifier are shared. | RFC 0038 Rollback |

## Scenario trace

Every original scenario is retained with its name. Landed behavior is pinned by existing
tests; the goal-runtime scenarios are future seams with no test claimed here.

| Scenario | Requirement | Source owner | Existing test |
| --- | --- | --- | --- |
| Usage limit is not a transient rate limit | `REQ-model-failover-001` | `failover_classifier.py` | `tests/core/test_failover_classifier.py::TestRateLimitErrorsEligible::test_codex_usage_limit_is_eligible` |
| An ambiguous reset is not guessed | `REQ-model-failover-001` | `failover_classifier.py` | `::TestRateLimitErrorsEligible::test_codex_clock_time_reset_is_not_guessed` |
| Stated reset is parsed | `REQ-model-failover-001` | `failover_classifier.py` | `::TestRateLimitErrorsEligible::test_claude_usage_limit_epoch_reset_is_parsed` |
| Failover skips a sibling on the exhausted account | `REQ-model-failover-002` | `model_routing.py` | `tests/core/test_provider_allowance.py::TestAccountScopedExclusion::test_exhausted_account_excludes_every_entry_on_it` |
| Exclusion lifts at the reset | `REQ-model-failover-002` | `model_routing.py` | `::TestAccountScopedExclusion::test_exclusion_lifts_at_reset_at` |
| Unknown state never excludes | `REQ-model-failover-002` | `model_routing.py`; allowance endpoint | `::TestAccountScopedExclusion::test_unknown_state_does_not_exclude`; `tests/api/test_model_allowance_endpoint.py` |
| A goal relaunch selects a different account inside the same goal | `REQ-model-failover-002` | goal runner (`bu-u5s4w8`) | future seam |
| No unexhausted candidate ends the invocation without waiting | `REQ-model-failover-002` | goal runner (`bu-u5s4w8`) | future seam |
| Repeated usage-limit rejections do not open the breaker | `REQ-model-failover-003` | `model_routing.py` breaker | `tests/core/test_provider_allowance.py::TestAllowanceStateWriter::test_allowance_outcome_never_trips_the_breaker` |
| Usage limit fails over to a different account in one attempt | `REQ-core-spawner-019` | `spawner.py` | `tests/core/test_spawner_same_tier_failover.py::TestUsageLimitAllowanceExhaustion::test_usage_limit_exhausts_account_and_fails_over_in_one_attempt` |
| Ordinary failures leave allowance untouched | `REQ-core-spawner-019` | `spawner.py` | `::TestUsageLimitAllowanceExhaustion::test_ordinary_failure_does_not_touch_allowance` |
| A goal invocation's usage limit marks the account without touching the goal budget | `REQ-core-spawner-019` | goal runner (`bu-u5s4w8`) | future seam |
| Only exhausted accounts defer a due task | `REQ-core-scheduler-004` | `scheduler.py`, `model_routing.py` | `tests/core/test_core_scheduler.py::test_tick_defers_prompt_task_to_allowance_reset_without_dispatching`; `tests/core/test_provider_allowance.py::TestAllowanceDeferral::test_defers_to_earliest_reset_only_when_every_entry_is_blocked` |
| A free account prevents deferral | `REQ-core-scheduler-004` | `model_routing.py` | `::TestAllowanceDeferral::test_an_available_lower_tier_entry_prevents_deferral` |
| Lapsed exhaustion does not defer | `REQ-core-scheduler-004` | `scheduler.py` | `tests/core/test_core_scheduler.py::test_tick_dispatches_normally_once_allowance_reset_passed` |
| A deferred obligation occurrence admits nothing until the reset | `REQ-core-scheduler-004` | daemon obligation submission (`bu-jv1moa.1`) | future seam |
| Quota denial does not mark an account exhausted | `REQ-catalog-token-limits-003` | `model_routing.py` quota check | none named; gap reported |
| Goal budget exhaustion does not mark an account exhausted | `REQ-catalog-token-limits-003` | goal runner (`bu-u5s4w8`) | future seam |
| Runtime roles record exhaustion | Provider Allowance State Write Grants | migration `core_257` | `tests/migrations/test_core_257_provider_allowance_states_migration.py` (schema only; grants not asserted, gap reported) |
| Dashboard allowance scenarios | `REQ-dashboard-model-settings-005` | allowance endpoint and Models tab | `tests/api/test_model_allowance_endpoint.py`, `tests/api/test_model_allowance_real_pg.py` |

## Risks

A success on an exhausted account clears the state even if it started before the
rejection; the next rejection re-establishes it. The fix (clear only when the success
started after the latest rejection) is tracked by `bu-q7vx1q.54`, and the goal runner
inherits it through the shared clear function.
