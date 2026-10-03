## 1. Classification, state, and routing

- [x] 1.1 Split `usage_limit` out of the rate-limit bucket with an optional parsed reset.
- [x] 1.2 Add `core_257`: `provider_allowance_states` and `model_catalog.allowance_account`.
- [x] 1.3 Exclude every entry on an exhausted account in every resolver; add
  `excluded_allowance` to the resolution receipt.
- [x] 1.4 Write `allowance_exhausted` attempts, upsert the state, clear on success.

## 2. Scheduler deferral

- [x] 2.1 Defer prompt-mode cron and deadline dispatch to the earliest reset
  (`skipped_allowance`).
- [ ] 2.2 Reserve capacity so background work cannot drain an account below N owner turns.

## 3. Surfaces

- [ ] 3.1 `GET /api/settings/models/allowance`, Models-tab and System-verdict countdown.

## 4. Unknown reset

- [ ] 4.1 Canary probe at the default horizon when the reset time is unknown.
