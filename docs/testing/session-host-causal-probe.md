# Session-host causal probe

This opt-in diagnostic has no workflow entrypoint and grants no runtime eligibility.
Run it only after explicit coordinator authorization on one fresh disposable Noble
GitHub-hosted ubuntu-24.04 VM with exclusive profile custody. `--execute`,
`--disposable-job`, GitHub-hosted/Linux/ubuntu24 identity, provider run ID
and attempt, clean checkout, exact native/helper pins and absent conflicting policy
are required. Ordinary local checks use fake host operations; they never load policy.

The coordinator owns original → derived → original policy arms, each with a random
nonce, fixed variant digest, and exclusive private intent/owned receipts. The original
and transformed members retain separate pins. It never uses profile replacement,
reuses the derived CLI receipt for an original arm, relaxes global userns restrictions,
or accepts generic map-root userns. Every successful arm removes the exact owned
policy, observes both names absent, and rechecks restrictions before another arm.

A transparent test-only pytest adapter runs the unchanged native cross-invocation
node. It calls the original owner, plan and subprocess methods with unchanged
arguments and results. It hashes the actual post-seal inherited image FDs using
`pread`, records typed fstat/seal metadata and normalized FD roles, and binds them
to the compiled image inventory. It records owner health/pending work, stage
emptiness, namespace death, direct-child reaping, closed FDs and settled tasks after
the test's finally blocks. Expected A failures therefore have their own settlement
receipt, even though the native test raises before its trailing success assertions.
No traceback parsing establishes native proof.

A arms require one exact closed shim-exec ENOENT failure with no setup/teardown
error or skip. B requires one pass, positive READY receipt and complete stderr drain;
stderr need not be empty. All arms must share source, compiled images and normalized
actual peer FD plan. Missing, duplicate, malformed, stale, over-cap or unknown evidence
refuses. Child stdout/stderr are drained concurrently through nonblocking pipes. Their aggregate
retained output is capped live at 2 MiB, with one extra detection byte; overflow immediately
refuses and discards the private output. only closed
enums, typed results, counts, hashes and fixed metadata are published. No stderr prefix,
arbitrary exception text, invocation contents or raw traceback is published.

The global work deadline is 210 seconds with 90 seconds reserved for cleanup. Commands
have explicit elapsed bounds including a reserved TERM/KILL/direct-reap window;
the native command is capped at 45 seconds without
changing its request/readiness deadlines. An uncertain native settlement does not
unload policy from a potentially live child: it quarantines the disposable runner,
stops remaining arms and reports NONPASS. The harness signals its own command group,
waits only a bounded time, and closes escaped inherited pipes. This settles the direct
command where proven; it does not establish settlement of native bwrap's separate
session. No raw command output or exception payload is published.

Execution remains blocked until an independently reviewed dedicated single-job launcher
pins the exact source and runs only on a fresh standard GitHub-hosted ubuntu-24.04 VM,
with a finite job timeout, no secrets, no unrelated workload and no continuation on probe
failure/quarantine/timeout. The explicit flag acknowledges that external execution
contract; environment flags alone do not prove it. No launcher is added here. Disposal
of that VM is the enforced external containment fallback for unobserved escaped native
children. VM disposal grants no arm-cleanup or causal credit; every accepted A/B/A arm
still needs its own full native settlement and cleanup receipt. Partial/unobserved policy loads likewise
never remove uncertain policy. An absent cleanup receipt never earns a green.

A completed original RED / derived GREEN / original RED sequence with all cleanup
receipts supports flag-dependent correction only on that exact hosted context.
It does not establish production policy authority. Independent combined-source
review and full required hosted CI remain separate requirements. This document and
offline controls are not evidence that the sequence has executed.

The success-only public artifact is `RUNNER_TEMP/butlers-session-host-causal/probe-report.json`.
A launcher reads that exact owned mode-0600 file without following symlinks, rejects
oversize/duplicate-key/nonfinite JSON, and calls the pure `closed_report` validator with
independently pinned source HEAD/tree, exact `_SOURCES` hash map and actual provider
run/attempt. The validator closes every report/identity/arm/native schema, requires three
unique nonces and all settlement/cleanup receipts, and repeats cross-arm actual-image/FD
plan comparisons. Refused reports are diagnostic NONPASS and are never success artifacts.
