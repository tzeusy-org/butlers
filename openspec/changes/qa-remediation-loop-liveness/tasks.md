## 1. Honest status and cause-bound reset

- [x] 1.1 `staffer_status` derives from active loop conditions (`degraded`, or `unknown` when unreadable)
- [x] 1.2 `breaker_resets.cause_code` / `cause_ref` (core_359); reset route requires and records `cause_ref`
- [x] 1.3 Dashboard reset dialog asks for the cause

## 2. Producer conditions

- [x] 2.1 Capability below target, with cause; breaker open too long
- [x] 2.2 Proposal unadopted; no verified fix (unmeasurable with zero opportunities)

## 3. Engineering-side watchdog

- [x] 3.1 `scripts/qa_prerequisite_watchdog.py` dry run against a `bd export`

## 4. Follow-ups (not in this change)

- [ ] 4.1 Page the conditions through the runtime-attention outbox (bootstrap-owned allowlist; owner-run)
- [ ] 4.2 Count verified recoveries, not only merged pull requests, once recovery evidence exists
