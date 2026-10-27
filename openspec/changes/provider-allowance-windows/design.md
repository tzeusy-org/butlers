## Context

`token_limits` is an owner-set budget on one catalog entry; a provider allowance is the
provider's own cap on an account that several catalog entries (and runtimes) may share.
They are different failures with different recoveries, so they get different state.

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

## Risks

A success on an exhausted account clears the state even if it started before the
rejection; the next rejection re-establishes it.
