# Playwright installer failure matrix

Active correction scope for bu-29wkwj.7 / PR #4917. The first hosted timeout
exposed surviving apt and retry overlap. The second review exposed incomplete
lifetime protection and release of the privileged group identity before its
last signal. This matrix is the acceptance checklist for the correction.

| Failure or boundary | Required result | Executing control and current evidence |
| --- | --- | --- |
| Healthy dependency and browser install | Clean dependencies before launching the locked browser | Cold/warm lifecycle modes: focused PASS |
| Dependency nonzero exit | Signal the still-reserved group, reap all owned children, then allow retry | Deps-exhaust with per-signal reserved-leader observation: focused PASS |
| Early successful leader exit | Keep leader waitable through final group signal, then reap | Cold/warm with per-signal reserved-leader observation: focused PASS |
| TERM-resistant dependency tree | TERM then KILL only its owned group; reap before retry | Deps-stall with TERM markers, reserved-leader observation and reaping: focused PASS |
| Denied privileged cleanup | Keep cleanup unproven and never start another attempt | Deps-cleanup-denied live-survivor/no-retry control: focused PASS |
| Setup exception after child creation | Cleanup the complete child lifetime; unexpected exit cannot authorize retry | Deps-setup-failure after live dependency readiness; cleanup and no retry: focused PASS |
| Abrupt supervisor failure / unproven exit | Never interpret generic zero/nonzero/signal exits as cleaned outcomes | Deps-crash and deps-unproven-zero: focused PASS; abrupt death cannot prove reaping |
| Parent cancellation and EOF | Supervisor kills and reaps its group; parent starts no retry | Deps-cancel and deps-eof with actual reaping: focused PASS |
| Reserved group identity | Observe leader without reaping; every privileged signal precedes leader reap | Every real signal observes a still-reserved leader in cold, deps-exhaust and deps-stall: focused PASS |
| Full setup and recovery envelope | Retain 720s installer reserve plus twice the named 228s before observation | Registry/workflow/contract aligned: ceil((456+720)/60)=20min; provisional source-derived cap |
| Actual root-owned apt behavior | Hosted runner proves real sudo/apt execution and cleanup | Pending fresh exact-head hosted frontend-e2e; synthetic controls do not establish this |

The current tests exercise real groups with synthetic install/browser commands
and a sudo boundary shim. A changed cap is provisional and source-derived:
healthy current p95 calibration and long-tail measurements remain unearned.

Local focused command:
`uv run --no-sync pytest tests/contracts/test_ci_test_targets.py::test_ci_workflow_shards_full_lanes_without_coverage_or_privacy_drift -q --tb=short -n 0`
passed all fourteen lifecycle modes on the correction tree. The reservation
observer failed on the prior implementation with "owned group identity released
before signal". Owning contract, inventory and static gate results are reported
with the pushed correction; they do not substitute for the hosted row above.

Protocol: only reserved supervisor outcomes 80/81/82 establish cleaned
success/failure/timeout. Unexpected setup/interpreter/sudo exits, including 0,
fail closed. An abrupt supervisor death is deliberately represented as
unproven, even when its descendant remains live in the injected crash control;
the test harness alone cleans those synthetic survivors after the assertion.
