# Playwright installer failure matrix

Original correction scope: bu-29wkwj.7 / PR #4917. Recovery scope: bu-29wkwj.14. The first hosted timeout
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
| Slow primary, healthy alternate | Only confirmed cleaned deadline selects official archive for the existing second attempt; first descendants gone before second starts | Deps-recover uses the real controller/supervisor and generated private configuration; source-local control, hosted benefit unproven |
| Ordinary failure or cancellation | Clean failure retains default route; direct signal/EOF and parent/total cancellation never retry | Deps-exhaust, deps-supervisor-cancel/eof, deps-total-expiry plus original cancellation/EOF controls |
| Recovery preparation rejected or cancelled | No late install launch; partial/private overlay removed only after confirmed cleanup | Deps-recovery-rejected and deps-prep-cancel with owned overlay-path observation |
| Private sources and effective configuration | Exact noble/deb822/endpoints/trust allowlist; private copied sources, unchanged third-party bytes/state; later/binary overrides refuse | Existing owning node executes input-policy and synthetic-reader controls; actual Ubuntu24 reader/sandbox validation remains unperformed locally |
| Controlled Ubuntu24 config/source reader | Real reader admits private sources/state, refuses later normal-fragment and binary overrides; actual source parser enumerates private targets without fetching; actual `_apt` reads only owned scratch and cannot read private configuration | CI-only branch of the same owning node; wired, not executed locally. No host configuration writes or package installation |
| Actual root-owned apt behavior | Hosted runner proves real sudo/apt execution and cleanup | Pending fresh exact-head hosted frontend-e2e; synthetic controls do not establish this |

The current tests exercise real groups with synthetic install/browser commands
and a sudo boundary shim. A changed cap is provisional and source-derived:
healthy current p95 calibration and long-tail measurements remain unearned.

Local focused command:
`uv run --no-sync pytest tests/contracts/test_ci_test_targets.py::test_ci_workflow_shards_full_lanes_without_coverage_or_privacy_drift -q --tb=short -n 0`
passed all fourteen original lifecycle modes on the original correction tree. The recovery
change extends this same node to twenty lifecycle modes and input/configuration negatives;
new exact-head receipts belong to its PR. The reservation
observer failed on the prior implementation with "owned group identity released
before signal". Owning contract, inventory and static gate results are reported
with the pushed correction; they do not substitute for the hosted row above.

Protocol: only reserved supervisor outcomes 80/81/82/83 establish cleaned
success/failure/deadline/cancellation. Deadline 82 requires actual elapsed expiry;
cancellation 83 is explicitly non-retryable, even after confirmed reaping. Unexpected setup/interpreter/sudo exits, including 0,
fail closed. An abrupt supervisor death is deliberately represented as
unproven, even when its descendant remains live in the injected crash control;
the test harness alone cleans those synthetic survivors after the assertion.


Recovery does not increase the 720s outer budget, two 150/160s dependency
attempts, ten-second dependency backoff, two-second cleanup reserves, three
110/120s browser attempts/two backoffs or 20-minute job cap. Mandatory OS
packages and locked browser launch still run on warm/cold cache paths. It does
not prove the cause of the observed Azure download slowdown or guarantee an
alternate is healthy. The two named failed runs (40002609785 and 40002526033)
show fast indexes, slow archives and no browser launch; the unchanged healthy
comparison (39999166300 attempt2) fetched 25.9MB at reported 17.5MB/s and ran
174 browser tests. Exact reserved outcomes were not printed in those failures.

The recovery child runs the actual APT reader before the unchanged locked CLI,
inside the same owned group and remaining attempt time. Local tests replace
that reader and the privileged/install boundary explicitly; no local host apt/sudo
is run. In Ubuntu24 GitHub Actions the same existing node additionally runs the
actual reader against private source/config/state/cache fixtures, checks real
later-fragment/binary override refusal and enumerates targets with actual
`apt-get --print-uris update` (no fetching/installing). Its exact hosted receipt
is still required; wiring is not a pass. The same CI-only control requires
noninteractive sudo to read only owned scratch as actual `_apt`, with a denied
600-mode mirror negative and restored positive companion; private copied
configuration must remain unreadable. Missing sudo/identity access fails by
name. No local sudo is invoked. Real signed OS dependency installation, locked
Chromium launch and e2e execution remain separate terminal hosted proof. Synthetic success alone cannot close those rows or establish p95.
