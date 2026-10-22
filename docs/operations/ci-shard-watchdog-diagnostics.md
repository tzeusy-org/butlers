# CI shard watchdog diagnostics

The separately named manual **CI Shard Watchdog Diagnostic** workflow captures
incomplete observations for a released investigation. Every checkpoint and local
reader result has `complete=false`. Neither a diagnostic artifact nor a successful
reader exit proves complete execution, successful tests, duration ordering, smoke,
release, coverage publication or a required CI gate.

The old PR4484 run38208268833 attempt1 was cancelled. Its unit-1 physical checkout,
internal cause and 3418 outcomes remain **UNKNOWN**. A future execution of the
frozen candidate cannot establish that old checkout or its missing outcomes.

## Release and fixed profile

ROOT first qualifies independent source/native review, exact-head normal CI,
protected merge-group and confirmed squash. ROOT then releases a harmless
finalized-artifact retention control and authenticates its readback after
cancellation. Only after that control may ROOT release one original-profile
investigation. These provider actions are future work; local software controls
and a mocked SDK response do not establish provider persistence.

Dispatch accepts only `original-pr4484-unit1` and a reviewed full capture commit.
Subject HEAD is `f574f7283daa99fe1666d0b7acec368b1f4ac096`, tree
`7a158bf1a98541110a8f904340d7def5737e4c5d`, with ordered parents d25a50b and c4d9fc8.
Capture HEAD/tree, observer/action bodies, all bundle members and lock are bound
separately from the actual subject and provider trigger HEAD.

The 35-minute inventory job runs the original fresh collector, partition and
unchanged budgets. The distinct collection observer runs in that same invocation,
records old/new opaque identities and joins the original collector's lane output;
it never classifies markers. Exact old200-file/3418-case unit-1 correspondence is
mandatory. The 28-minute subject job independently recollects through the original
runner before executing its original UV command. Both provisional job caps are
listed in `scripts/ci-job-timeouts.json`; there is no healthy calibration claim.

Both jobs use real Python3.12.15 environments and the original finite frozen
editable repair. The capture checkout supplies the current owned uv installer:
its unchanged five attempts,120-second attempt envelope and10-second backoffs
preserve the original retry profile while its process-group cleanup also applies
to these separately named diagnostic steps. The unit-1 entry retains three workers, loadfile, lexical collection,
maxfail1, coverage0, default tracer selection, item300 watchdog, original UV640
nominal reserve, fixture boundaries and finalizers. With coverage disabled the
actual tracer can be null. Original callbacks and all normal CI consumers remain
unchanged. Missing request-observation resource inputs remain UNKNOWN; this path
provides no REQ-testing-053 exemption.

## Observation and publication

Exclusive controller registration and xdist workerinput authorization fence
nested pytest processes. Item hooks enqueue opaque start/phase/departure events;
a bounded writer publishes atomic cumulative process states. Queue capacity1024,
counters, threads256, frames64/thread, registered descendants256, checkpoint16MiB
and eight immutable slots bound resource use. Cooperative stack sampling records
only approved tracked static file/blob/line references. It cannot recover every
native/GIL-stuck stack. Independent numeric descendant sampling can remain
available while cooperative sampling is unavailable. Process and stack elapsed
values share the existing literal nonnegative bounded-integer contract. Invalid
observer state becomes an UNKNOWN gap before sealing; a sealed malformed value
is refused by the strict reader before upload admission.

Slots are scheduled at action elapsed0/120/300/600/900/1200/1440/1560 seconds.
Action elapsed is not provider job elapsed. Unknown setup offsets or a missing
late upload remain UNKNOWN. Names are
`ci-watchdog-unit-1-<run>-<attempt>-<nonce-prefix>-<sequence>`; they never match normal
`ci-*-test-evidence` carriers. Each contains only a sealed snapshot and manifest.
No overwrite, append, delete, retry under an ambiguous name or raw JUnit/log upload
is allowed. Local checkpoint retention is bounded by eight16MiB slots plus live
and staging state (160MiB envelope).

The Node20 action bundles exact public `@actions/artifact`2.3.2 from the committed
integrity-bearing lock. No runtime npm installation occurs. Build with `npm ci
--ignore-scripts`, `npm run build`, then `npm run check-bundle`; all generated
bundle members and third-party notices are checked byte-for-byte.

The uploader receives only documented SDK/runtime tracking keys; subject SDK keys
`ACTIONS_RUNTIME_TOKEN`, `ACTIONS_RESULTS_URL`, `ACTIONS_RUNTIME_URL` and
`ACTIONS_CACHE_URL` are removed. Their values stay process-only. Subject/test
secrets never enter the uploader. Uploader stdout/stderr are discarded; only fixed
status and finalized ID/digest/size pass its closed IPC channel. Upload concurrency
is one and the independent60-second envelope includes owned cleanup. Overlap,
error, missing result or cancellation cannot stall or select tests. SDK finalization
is the only local persistence claim; authentic provider readback remains separate.

Every owned launch first establishes a confined Linux child subreaper using the
standard Python interpreter. Its unprivileged per-process ownership boundary
adopts descendants even when an intermediate parent exits immediately or a child
creates a new session. Original child status and owned settlement are separate
closed records. Cleanup requires kernel child reaping, a positive settlement
record and supervisor exit; an empty polling result alone cannot attest it.
A disappearing task file closes a scan only on `ENOENT` with a known departed
process identity. Permission failures, malformed data, unknown or reused actors
and actual truncation remain incomplete. A subsequent same-start departure
cannot clear task-count or children-file overflow already observed during that
scan; kernel reaping still closes settlement.
One bounded terminal control record distinguishes a latched scan or population
refusal from the existing settlement deadline. Its fixed counters saturate at
65535 and report saturation explicitly; it contains no paths, arguments, process
names or exception text. The Node owner validates the exact schema and retains
only those closed values, control validity, capped byte/process counts and helper
exit state. The branch-owned numeric regression prints that closed diagnostic on
cleanup failure so its actual CI failure carrier can preserve the observed cause.
The local terminal transport record retains the subject diagnostic and at most
eight uploader diagnostics; sealed snapshot and manifest membership is unchanged.
Missing or malformed control remains incomplete. These observations neither
change cleanup acceptance or deadlines nor identify a historical failure's cause.
Cancellation enters bounded cleanup immediately. Supervisor ownership also
settles if the action process disappears or its control reader closes. Descriptor
health is checked independently on every bounded supervisor loop, even when a
resistant target emits no lifecycle record. Lost acknowledgments remain incomplete
although actual owned processes settle. SDK child output remains discarded and
its existing IPC socket carries only the allowlisted publication result.

## Strict downloaded-artifact admission

ROOT retrieves fixed same-repository/workflow/run/attempt artifacts with existing
read access. Retain authenticated artifact metadata, content/ZIP digests and
terminal provider state outside the repository. Under `inventory-dir`, provide
fresh inventory/assignment/binding/correspondence and a
`provider-authenticated.json` array. Each entry contains exactly artifact_id, name,
repository, workflow, run, attempt, sequence and snapshot_sha256, derived from
that authenticated provider retrieval. This local receipt is a custody input,
not a new provider authenticator. Unauthenticated metadata cannot earn persistence.

Place each two-file checkpoint in `snapshot-dir/slot-<sequence>` and run:

Run the reader from a capture repository whose Git store contains the externally
reviewed full commit supplied as `--capture-head`. The reader independently reads
that commit's tree and bundle/lock/observer/action bodies from Git objects and
compares every source pin. Downloaded seals and mutable worktree files cannot
provide those expected values. Authenticated artifact name/entry, slot directory,
sealed manifest and snapshot must agree on the same literal integer sequence0–7;
boolean sequences refuse admission.

```bash
uv run --no-sync python scripts/ci_watchdog_diagnostics.py validate \
  --snapshot-dir <authenticated-downloads> --inventory-dir <same-attempt-inventory> \
  --expected-profile original-pr4484-unit1 --capture-head <reviewed-capture-head> \
  --subject-head f574f7283daa99fe1666d0b7acec368b1f4ac096 \
  --subject-tree 7a158bf1a98541110a8f904340d7def5737e4c5d \
  --repository tzeusy-org/butlers --workflow 'CI Shard Watchdog Diagnostic' \
  --run <new-run> --attempt <new-attempt> --output <masked-report>
```

Duplicate keys, nonliteral types, nonfinite/regressed counters/clocks, mixed source,
unknown members, symlinks/FIFOs, tampered seals and missing authentication refuse
admission. Complete expected membership remains present; missing item/phase and
finalizer boundaries remain UNKNOWN. Duplicate start/phase multiplicities remain
visible and cannot become apparently exactly-once observations.

## Harmless finalized-artifact retention control

The separate manual **CI Shard Watchdog Retention Control** workflow is source
for a future ROOT-released mechanism control. It never selects or prepares the
original workload. After independently reviewed source delivery, ROOT checks the
registered workflow and chooses its delivered/default-branch dispatch ref, with
`capture_head` equal to that full delivered SHA. Provider trigger SHA, workflow
SHA and actual checkout must all agree; a ref race refuses before upload. A raw
commit SHA is not assumed to be an accepted dispatch ref.

One managed uploader uses the same locked artifact SDK2.3.2 and cleanup boundary.
It finalizes exactly `manifest.json` and `marker.json`, together at most8KiB, in
`ci-watchdog-retention-control-<run>-<attempt>-<nonce-prefix>`. Both carry the
separate `ci-watchdog-retention-control.v1` schema, `complete=false` and
`mechanism_only=true`. The marker contains only the fixed control category and
opaque repository/workflow/run/attempt/source/tree/nonce bindings with exact
control/action/bundle/lock hashes. The manifest hashes the immutable marker.
Neither diagnostic nor normal execution admission accepts this carrier. No
original inventory, commands, arbitrary paths, user data or SDK output enter it.

The finalize step has a60-second envelope including owned cleanup. Only a
validated finalized ID, ZIP digest and archive size with complete owned cleanup
produce the closed terminal finalization record, including separate hashes and
sizes for both payloads. Upload failure, unavailable SDK, malformed or duplicate
IPC, cancellation and incomplete cleanup remain UNKNOWN/nonpass. There is no
retry, overwrite or deletion. A separately visible ordinary `sleep120` step runs
only after successful finalize; it has no cancellation immunity. The new whole
job has a provisional5-minute timeout, not a measured healthy calibration.

ROOT's separately authorized operator observes the successful finalize step and
authenticated, uniquely bound artifact metadata **while that ordinary wait is
active**, then issues ONE cancellation and waits finitely for actual cancelled
terminal state. Live partial job logs may be unavailable. Obtain the exact SDK
finalized record from terminal logs after cancellation, and match it against
authenticated same-repository/run/attempt artifact metadata, ZIP digest and
individual downloaded payload hashes/source bindings. ZIP and payload digests
are distinct. Suggested operator envelope is300 seconds, each call bounded by
`min(15s, remaining)`, polling at most every5 seconds. No live finalized metadata,
ended wait, unconfirmed cancellation, expired/missing/ambiguous artifact, wrong
source/digest/run or incomplete cleanup means UNKNOWN/refused; never cancel or
retry blindly. No operator action or provider persistence is earned by local
mock-SDK controls or normal/protected source CI. Only that exact hosted control
may precede ROOT's separately released unchanged original investigation; the
historical cause and original3418 outcomes remain UNKNOWN.

Rollback is a reviewed removal of only the new retention-control workflow and
action, separate control exports/generated derivative, new timeout row and this
section. Preserve the existing diagnostic workflow/action/profile, SDK lock,
normal CI fan-in and all original execution authority.

## Verification and rollback

Three Python owners exercise actual miniature three-worker privacy/registration,
failure/cancellation/restoration and fresh original collector correspondence. One
callback in a new reconciliation test file proves healthy legacy admission, diagnostic false/true
refusal, legacy missing-teardown refusal and restoration. Three bounded Node
controls exercise publication/IPC, raw SDK output privacy and real descendant
cleanup while an unrelated sentinel survives. These are named software controls,
not the3418-case workload, hosted timing or old-run cause proof.

Rollback is a reviewed PR removing only the new workflow/action/module/docs/native
addition and the two provisional timeout rows. Preserve every original source,
callback, native requirement/scenario, fixture, budget and required CI surface.
ROOT owns review, queue, squash, future transport/investigation release and parent
hard metrics. No baseline sync/archive or foreign active task flag is part of this
source release.
