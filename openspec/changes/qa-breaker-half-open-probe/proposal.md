## Why

A tripped QA breaker waits for an owner reset, so a transient outage (a provider authentication
error, for example) halts remediation until someone notices, and nothing in QA's own records says
why it stopped or when it will try again (bu-ejow5p.3, slice S3 of Breaker v2).

## What changes

- The breaker heals itself. Once it has been open for 15 minutes past the newest failure, one
  finding is admitted as a half-open probe, serialized across dispatchers by an advisory lock held
  across the claim. A success breaks the failure streak and closes the breaker; each failed probe
  doubles the wait, up to a 2 hour cap (about 12 probes a day).
- The state is derived from the launched-execution rows dispatch and the dashboard already share:
  no new table, and a restart re-derives it.
- `GET /api/qa/circuit-breaker` and the `/summary` breaker name the dominant cause (`mixed` with
  counts when the failures differ), the trip time and the next probe time. Each counted attempt
  carries its cause class and the first line of its own error text.
- Patrol reads carry `halt_reason`.

## Deferred

- The briefing and `/qa` clauses, links and the reset dialog's evidence and forecast (S3b, frontend).
