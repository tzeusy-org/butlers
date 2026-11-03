## Amendment scope

Checked tasks are the landed pre-switch delivery and keep their history. RFC 0038 D5, D10,
D14 and D16 and the generalist collision disposition (`re-scope`) govern sections 4 and 5. The
owner-turn reserve (former task 2.2, `bu-q7vx1q.52`) moved to the spend-lane owner reserve in
`bu-jv1moa.1` and is no longer a task of this change.

## 1. Classification, state, and routing

- [x] 1.1 Split `usage_limit` out of the rate-limit bucket with an optional parsed reset.
- [x] 1.2 Add `core_257`: `provider_allowance_states` and `model_catalog.allowance_account`.
- [x] 1.3 Exclude every entry on an exhausted account in every resolver; add
  `excluded_allowance` to the resolution receipt.
- [x] 1.4 Write `allowance_exhausted` attempts, upsert the state, clear on success.

## 2. Scheduler deferral

- [x] 2.1 Defer prompt-mode cron and deadline dispatch to the earliest reset
  (`skipped_allowance`).

## 3. Surfaces

- [x] 3.1 `GET /api/settings/models/allowance`, Models-tab and System-verdict countdown.

## 4. Unknown reset

- [ ] 4.1 Probe an account at the default horizon when the reset time is unknown, as a D18
  model function in the goal runtime; the probe can only shorten the exclusion
  (`bu-q7vx1q.53`).

## 5. Goal-runtime seams (after the switch)

- [ ] 5.1 Apply the account exclusion to the goal runner's per-invocation model selection, the
  one D14 relaunch and escalation, and write `allowance_exhausted` attempts with
  `invocation_id` and `goal_id` plus the allowance upsert and clear, without touching the goal
  budget. Covers `REQ-model-failover-002`, `REQ-model-failover-003`,
  `REQ-core-spawner-019` and `REQ-catalog-token-limits-003` (`bu-u5s4w8`).
- [ ] 5.2 Run the allowance deferral before the daemon submits a prompt-mode obligation
  admission, so a deferred occurrence has no admission decision, goal row or budget
  reservation. Covers `REQ-core-scheduler-004` (`bu-jv1moa.1`).
