## 1. Exact-text review

- [ ] 1.1 Independently review the text in design.md against Rules 2, 4, 5 and 6,
  the current module/lifecycle seams, RFC 0038 and the retained schedule
  contracts. Validate this proposal with strict OpenSpec and repository guards.
- [ ] 1.2 Obtain the owner's approval of the exact Rule 2 and architecture
  passages and the proposed acceptance. Record the decision against
  bu-i4khaz's existing boundary.

## 2. Implementation acceptance after adoption

- [ ] 2.1 Hand bu-dss7he.10 the accepted citations and reviewed
  [handler allocation](job-inventory.md). Apply the adoption sweep. Keep
  bu-qozovd.5 responsible for Chronicler adapter aggregation.
- [ ] 2.2 Extend `tests/modules/memory/test_maintenance_scheduling.py` and
  `tests/core/test_core_scheduler.py` at the startup and synchronization
  seams. Exercise duplicate and unknown names, disabled module handlers,
  wrong-pool refusal, repeat startup and a real scheduled occurrence.
- [ ] 2.3 Extend the existing Health, Finance, Relationship and Chronicler
  `tests/daemon/test_*_job_registration.py` callers. Preserve the lease and
  retry controls, the owner-disabled row and TOML recovery controls, and the
  dashboard lifespan supervision checks. Confirm the scheduler claims and
  passes through the original arguments on the owning pool.
- [ ] 2.4 Extend `test_real_daemon_attaches_postcommit_owner_only_to_projection_jobs`
  with an eligible trusted handler, a same-name replacement and an
  ineligible job. Verify the trusted success alone invokes the continuation
  after the job commits. Preserve the real scheduler-tail test in
  `tests/chronicler/test_day_close_continuation.py`.

This proposal adds no executable tests. Test delta +0 ~0 -0.
