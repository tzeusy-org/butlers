## 1. Calendar overcommitment producer (`calendar:overcommitment`)

Spec-first: nothing below is authorized until the owner adopts this change. One follow-up bead
per group.

- [ ] 1.1 Verify the `[Unknown]` identity seam: whether `calendar_event_instances.id` is stable
  across a provider resync of the same instance (read `src/butlers/modules/calendar` sync and
  upsert code, and a resync test). If unstable, replace the overlap identity with a content-free
  stable key before implementation and amend the requirement.
- [ ] 1.2 Verify the `[Unknown]` freshness seam: whether a complete scan over stale
  `source_freshness` rows can miss a provider-side cancellation, and decide whether the loop must
  also require fresh sources before `snapshot_complete=true`.
- [ ] 1.3 Add a supervised loop modeled on `run_calendar_sync_deadman_loop`
  (`src/butlers/jobs/calendar_sync_deadman.py`, started in `src/butlers/api/app.py`) that calls
  `query_calendar_conflicts` over `[owner-local midnight, + N days)`, builds title-free
  observations, and reconciles `calendar:overcommitment` through the Switchboard pool with
  `snapshot_complete = scan.available`.
- [ ] 1.4 Tests: open (overlap and overloaded day), `back_to_back` ignored, auto-resolve,
  `available=false` never resolves, reconciliation failure keeps the loop alive, privacy
  (no title, attendee or location in summary or metadata), concurrent scans idempotent.
  Nearest existing test: `tests/jobs/test_calendar_sync_deadman.py` (pattern) and
  `tests/integration/test_calendar_sync_deadman_roundtrip.py` (real Postgres).
  Tests delta: +9 ~0 -0 expected (revisit at implementation).

## 2. Home device threshold producer (`home:device-threshold`)

- [ ] 2.1 Verify the `[Unknown]` staleness seam: how `ha_entity_snapshot.last_updated`,
  `is_steady_state_unknown` and `_require_ha_source_healthy` interact for a battery sensor that
  stops reporting. Decide whether a stale reading counts as unmeasured.
- [ ] 2.2 In `run_device_health_check` (`src/butlers/jobs/home.py`), build observations from the
  existing battery and offline classification and reconcile `home:device-threshold` with a
  best-effort helper that logs and swallows, mirroring `_reconcile_owner_conditions` in
  `roster/finance/jobs/finance_jobs.py`. Do not reconcile when the HA source is unmeasurable.
- [ ] 2.3 Tests: open, resolve on recovery, `HASourceUnmeasurableError` leaves conditions
  active, reconciliation failure does not break the job, privacy, no extra attributes.
  Nearest existing test: `tests/jobs/test_home.py` (`run_device_health_check`) with the owner
  condition assertions of `roster/finance/tests/test_jobs.py`
  (`test_insight_scan_overdue_bill_opens_owner_condition`).
  Tests delta: +5 ~0 -0 expected.

## 3. Health baseline deviation producer (`health:baseline-deviation`)

- [ ] 3.1 Verify the `[Unknown]` measurability seam: the exact shape of the not-measurable
  state in `_measurable` and how an unevaluated metric is represented, so the carry-forward
  scenario can be implemented with `confirm_only`.
- [ ] 3.2 In `run_baseline_watch` (`src/butlers/jobs/health_baselines.py`), after the episode
  advancement, reconcile `health:baseline-deviation` from the open episodes with label,
  direction and start date only. No reading, baseline or unit in any field.
- [ ] 3.3 Tests: open, resolve, unmeasurable metric carried forward, late sample absorbed by
  recompute, reconciliation failure does not break the job, privacy.
  Nearest existing test: `tests/jobs/test_health_baselines.py`.
  Tests delta: +6 ~0 -0 expected.

## 4. Finance budget threshold producer (`finance:budget-threshold`)

- [ ] 4.1 In `run_insight_scan` (`roster/finance/jobs/finance_jobs.py`), after the existing
  budget section, build observations from `budget_status()` items with status `warning` or
  `exceeded` and reconcile `finance:budget-threshold` through `_reconcile_owner_conditions`.
  Skip the source when `budget_status()` fails.
- [ ] 4.2 Tests: open on warn, warning to exceeded keeps one episode, resolve on new period or
  lower total, failing `budget_status()` never resolves, reconciliation failure does not break
  the scan.
  Nearest existing test: `roster/finance/tests/test_jobs.py`
  (`test_insight_scan_overdue_bill_opens_owner_condition` pattern).
  Tests delta: +5 ~0 -0 expected.

## 5. Cross-cutting spec hygiene

- [ ] 5.1 Run `openspec validate add-owner-condition-producers --strict`, `make check-guards`
  and `make check-spec-overwrites`. The delta has ADDED blocks only, so no overwrite entry is
  expected.
- [ ] 5.2 Owner adoption: decide the three adoption questions in `design.md` (job-time versus
  write-time crossing detection in D5, owner-resolve-holds-on-unchanged-calendar in D4, and the
  settings surface in D2) before any follow-up bead is filed.
