## 1. Calendar overcommitment producer (`calendar:overcommitment`)

Spec-first: nothing below is authorized until the owner adopts this change. One follow-up bead
per group.

- [x] 1.1 Verify the `[Unknown]` identity seam: whether `calendar_event_instances.id` is stable
  across a provider resync of the same instance (read `src/butlers/modules/calendar` sync and
  upsert code, and a resync test), and whether a dedup-store outage (`load_dedup_rules` and
  `load_keep_separate_keys` fail open to the default strategy while `available` stays true) can
  change which copy survives a cluster and so the `instance_id` feeding `overlap_pair_id`. If
  unstable, replace the overlap identity with a content-free
  stable key before implementation and amend the requirement.
  Resolved (bu-vcjgo8): unstable. One schema's instance id survives its own resync, but each
  calendar butler's schema holds a copy of the same provider event and the `origin_ref` dedup
  keeps the most recently synced copy, so the surviving instance id rotates between schemas.
  The pair id is now `overlap_pair_id` over provider identities (the `origin_ref` cluster key),
  and a dedup-store read failure is reported on the scan and skips the tick. Requirement
  amended.
- [x] 1.2 Verify the `[Unknown]` freshness seam: whether a complete scan over stale
  `source_freshness` rows can miss a provider-side cancellation, and decide whether the loop must
  also require fresh sources before `snapshot_complete=true`.
  Resolved (bu-vcjgo8): no. Stale rows can keep a cancelled conflict observed or miss a new one,
  but cannot resolve a live one, and `calendar_sync_deadman` already raises sync death.
- [x] 1.3 Add a supervised loop modeled on `run_calendar_sync_deadman_loop`
  (`src/butlers/jobs/calendar_sync_deadman.py`, started in `src/butlers/api/app.py`) that calls
  `query_calendar_conflicts` over `[owner-local midnight, + N days)`, builds title-free
  observations, and reconciles `calendar:overcommitment` through the Switchboard pool with
  `snapshot_complete = scan.available`. At adoption, amend REQ-infrastructure-reliability-001
  (`openspec/specs/infrastructure-reliability/spec.md`), which enumerates the supervised
  dashboard loops consumed by the RFC 0007 `supervisors` readiness projection, to add the new
  loop.
- [x] 1.4 Tests: open (overlap and overloaded day), `back_to_back` ignored, auto-resolve,
  `available=false` never resolves, reconciliation failure keeps the loop alive, privacy
  (no title, attendee or location in summary or metadata), concurrent scans idempotent.
  Nearest existing test: `tests/jobs/test_calendar_sync_deadman.py` (pattern) and
  `tests/integration/test_calendar_sync_deadman_roundtrip.py` (real Postgres).
  Tests delta: +9 ~0 -0 expected (revisit at implementation); measured +23 ~1 -0 collected
  (`tests/jobs/test_calendar_overcommitment.py` 10 functions, 3 parametrized; 2 in
  `tests/integration/test_calendar_overcommitment_roundtrip.py`; the lifespan inventory test
  extended). Shipped by bu-vcjgo8 as `src/butlers/jobs/calendar_overcommitment.py`.

## 2. Home device threshold producer (`home:device-threshold`)

- [x] 2.1 Verify the `[Unknown]` staleness seam: how `ha_entity_snapshot.last_updated`,
  `is_steady_state_unknown` and `_require_ha_source_healthy` interact for a battery sensor that
  stops reporting. Decide whether a stale reading counts as unmeasured.
  Resolved (bu-6im1ew): a stale numeric reading counts as measured. The snapshot's
  `last_updated` is HA's own, which moves only when the state or attributes change, so an old
  timestamp on a steady battery level is normal and the snapshot cannot tell it from a silent
  sensor. `is_steady_state_unknown` only gates offline classification of stateless domains, and
  `_require_ha_source_healthy` gates the whole run. A sensor HA has lost reads `unavailable` or
  `unknown` and is carried forward `confirm_only`; a non-finite reading is treated the same.
- [x] 2.2 In `run_device_health_check` (`src/butlers/jobs/home.py`), build observations from the
  existing battery and offline classification and reconcile `home:device-threshold` with a
  best-effort helper that logs and swallows, mirroring `_reconcile_owner_conditions` in
  `roster/finance/jobs/finance_jobs.py`. Do not reconcile when the HA source is unmeasurable or when the job returns early on an empty
  `ha_entity_snapshot`. Qualify `critical` and `warning` only (not `info`), and carry the active
  battery condition forward with `confirm_only` for a battery entity whose state is not numeric.
- [x] 2.3 Tests: open, resolve on recovery (including an `info`-level reading), `info` never
  opens, unavailable battery carried forward, empty snapshot does not reconcile, offline
  escalation keeps one episode, `HASourceUnmeasurableError` leaves conditions active,
  reconciliation failure does not break the job, privacy, no extra attributes.
  Nearest existing test: `tests/jobs/test_home.py` (`run_device_health_check`) with the owner
  condition assertions of `roster/finance/tests/test_jobs.py`
  (`test_insight_scan_overdue_bill_opens_owner_condition`).
  Tests delta: +8 ~0 -0 expected; measured +8 ~0 -0 collected (7 functions, one
  parametrized x2, in `tests/jobs/test_home_device_threshold.py`). Shipped by bu-6im1ew.

## 3. Health baseline deviation producer (removed)

Dropped on review: `health:baseline-deviation` already exists and `add-personal-baselines` governs it.
The group number is left vacant so groups 4 and 5 stay stable.

## 4. Finance budget threshold producer (`finance:budget-threshold`)

- [x] 4.1 In `run_insight_scan` (`roster/finance/jobs/finance_jobs.py`), after the existing
  budget section, build observations from `budget_status()` items with status `warning` or
  `exceeded` and reconcile `finance:budget-threshold` through `_reconcile_owner_conditions`.
  Wrap the currently unwrapped `budget_status()` call so a raise skips only this source.
  `budget_status()` has no degraded return shape: it raises, returns items, or returns
  `{"items": [], "count": 0}` with no active budgets, and the empty result is a complete
  snapshot that resolves. The early exits in sections 1 to 3 skip the reconcile on those runs;
  that latency is accepted and no change is made.
- [x] 4.2 Tests: open on warn, warning to exceeded keeps one episode, resolve on new period or
  lower total, failing `budget_status()` never resolves, reconciliation failure does not break
  the scan.
  Nearest existing test: `roster/finance/tests/test_jobs.py`
  (`test_insight_scan_overdue_bill_opens_owner_condition` pattern).
  Tests delta: +5 ~0 -0 expected; measured +6 ~0 -0 collected (4 functions, one parametrized x3).
  Shipped by bu-5rjeup.

## 5. Cross-cutting spec hygiene

- [ ] 5.1 Run `openspec validate add-owner-condition-producers --strict`, `make check-guards`
  and `make check-spec-overwrites`. The delta has ADDED blocks only, so no overwrite entry is
  expected.
- [ ] 5.2 Owner adoption: decide the three adoption questions in `design.md` (job-time versus
  write-time crossing detection in D5, owner-resolve-holds-on-unchanged-calendar in D4, and the
  settings surface in D2) and confirm the two adoption notes (the `calendar:` source prefix
  deviating from `{origin_butler}:{category}` in D1, and the REQ-infrastructure-reliability-001
  loop-list amendment in D2) before any follow-up bead is filed.
