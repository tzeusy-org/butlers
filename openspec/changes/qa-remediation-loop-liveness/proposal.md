## Why

QA reported healthy through 81 days of zero pull requests and repeated breaker trips. QA cannot
investigate itself (RFC 0015 D3) and cannot read Beads (runtime-to-tracker deny), so the health of
its remediation loop must come from deterministic conditions recorded by the independent control
plane. Each manual breaker reset buys five more attempts; when the cause is a missing capability,
those attempts fail identically. The owner approved the direction on `bu-l34qcx`; thresholds are
the recorded writer defaults on `bu-ejow5p.8`.

## What changes

- The control-plane controller records four QA remediation-loop conditions in
  `public.infra_conditions` with fixed identities: capability below target (with its cause),
  breaker open longer than 6 hours, proposal unadopted for 7 days, and no merged fix in 14 days
  despite at least 3 investigations that reached publication.
- Each check is present, absent or unmeasurable. An unreadable table or an empty opportunity set is
  unmeasurable and never opens or resolves a condition.
- `GET /api/qa/summary` returns `degraded` instead of `healthy` while any condition is active, and
  `unknown` when the conditions cannot be read; it lists the active conditions.
- `POST /api/qa/circuit-breaker/reset` requires `cause_ref` (`<cause_code>:<reference>`), records it
  on `public.breaker_resets`, and refuses a reset that does not name a still-dominant structural
  cause or reuses a reference whose claim was already disproved.
- An engineering-side watchdog (`scripts/qa_prerequisite_watchdog.py`) reports stalled or unowned
  QA prerequisite beads from a `bd export`. It runs outside the runtime.

## Not decided here

Paging these conditions through the owner-attention outbox: the fixed producer's allowlist lives in
the bootstrap-owned `scripts/init-db.sql`, so extending it is a separate owner-run step tracked as a
follow-up. Conditions appear on the dashboard summary until then.

Tracking: `bu-ejow5p.8`.
