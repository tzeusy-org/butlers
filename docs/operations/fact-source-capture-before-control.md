# Old-runtime fact-capture PostgreSQL witness

The separately named `Fact Source Capture BEFORE Control` workflow is a regression-evidence
control. It does not change ordinary CI fan-in and does not replace the full protected gate.
Only a separately reviewed and ROOT-admitted PR-triggered execution supplies evidence. There
is no manual dispatch, arbitrary command, selector, revision input or deployed database.

The fixed workload is `9b46e8e7342411cf3e8f2b75c139d077509233b0`. In a disposable checkout,
only `src/butlers/core/fact_authority.py` is replaced with its immutable
`37d3917c38080a0e43aa73d8f114b715c2377568` blob. The complete mode/blob manifest, physical
overlay tree, runtime hash, lock, interpreter, controller source, run/attempt and fresh nonce
remain bound. Its real venv imports that disposable checkout's source.

The existing `test_capacity_one_registered_owner_capture_and_concurrent_sources` node runs
unchanged, with its actual migrated PostgreSQL roles, capacity-one pool and registered
Relationship MCP fixtures. The original 15-second capture and 300-second item limits remain.
Only a genuine in-CALL old-code nested-acquire `TimeoutError` qualifies as the expected RED.
Setup failure, missing CALL, skip, another exception, healthy BEFORE, timeout or incomplete
cleanup remains refused/UNKNOWN. A registered endpoint does not prove the blocked old code
reached a network lookup. The observer records only actual known reachability.

The call observer retains only the exact immutable test frame's issuer and three distinct
synthetic UUID inbox references in RAM, on success as well as failure. It reads report data
only with a bound query for those three IDs; it never enumerates other inboxes or reads their
reports. Its narrow temporary tracer is restored on every outcome and leaves any preexisting
tracer intact. Missing references, incomplete rows or an unavailable observation remain UNKNOWN;
identifiers and frame locals never enter receipts.

Before fixture teardown the observer checks cancelled capture settlement, pool reacquisition,
transaction release and `SELECT 1`, zero partial durable and in-memory reports. Complete actual
fixture/process teardown and an unrelated sentinel must survive. Observation never replaces
or mocks the pool, migration, resolver, transaction or test body. After complete cleanup the
candidate runtime is restored exactly and the same node executes once in a fresh process.
It must pass setup/CALL/teardown. No source retry is allowed.

The old test remains **FAILED**. `WITNESS_COMPLETE` is a separate aggregate control verdict for
that observed RED, paired candidate GREEN and complete cleanup. Its schema stays distinct
from complete shard, smoke, coverage or release evidence. Existing candidate AFTER evidence
and the mandatory full protected source tree remain separate.

The producer invokes the existing owned `install_ci_uv.py` within its own confined domain,
with a private path-output sink. Ordinary workflow installer consumers remain unchanged by this control; installation is part
of the control rather than a new workflow installer step.
The executor-local `scripts/fact_source_capture_before_supervisor.py` owns every execution
domain. It derives from the supervisor in PR5082
(`2e970c17a1708014f78394f14f184a483e9d9c3c`), with local task observations and the
bounded stable-rescan acceptance described below. That existing rescan changes which scans
can be accepted; the complete local supervisor requires review with this control. It is not
previously approved public substrate. The retired shared action is not restored. The aggregate 2480-second
budget starts before checkout and includes the preserved 640-second UV installer and
640-second environment reserve, two 480-second node envelopes including cleanup, and 120
seconds each for setup and evidence. The 45-minute job is provisional, not measured p95.
Every phase receives remaining time and retains cleanup reserve; expiration/cancellation
cannot produce a witness. No local Docker/PG privilege retry is supported.

Phase success remains tentative until sentinel and private-workload cleanup finish.
Terminal publication then refuses any latched cancellation or expired aggregate budget.
The signal handler remains armed through summary writing, success announcement and CLI exit:
a terminal cancellation revokes this invocation's summary and interrupts success. A refusal
also removes any pending summary. Missing evidence or a non-successful provider run still
cannot qualify a witness, including interruption after the main function returns.

Only bounded closed JSON identity/phase/cleanup receipts are uploaded. Raw failure output stays
in protected runner-temporary files and is removed with the disposable checkout; it is never
an artifact. DSNs, credentials, payloads, traceback locals and arbitrary logs are excluded.
Missing/upload/cancelled/incomplete evidence remains UNKNOWN. Actual provider archive digest,
source readback and independent complete receipt verification are required after execution.

On refusal, the producer prints only a closed stage, error category and allowlisted reason.
After controller identity validation and creation of its own evidence directory, it also
writes `control-refusal.json`. This separate `fact-capture-control-refusal.v1` schema always
reports UNKNOWN, with no claim of PG CALL or cleanup. It cannot satisfy a phase or complete
witness reader. Failure before identity validation creates no directory or children; its
closed console diagnostic is the only observation. Exception messages, commands, paths,
environment values and tracebacks never enter these diagnostics. A failed artifact write
remains refused and does not overwrite or promote evidence.

An environment-preparation refusal additionally retains a closed `preparation` diagnostic.
Its lifecycle projection distinguishes the child exit/signal, supervisor exit, timeout,
cancellation, protocol refusal, and the existing supervisor's fixed refusal reason/counts.
These observations do not supply a missing cleanup verdict or prove any PostgreSQL call.
The unchanged, SHA256-pinned `ci_environment.py prepare` runs with its original argv, cwd,
environment, subprocess commands, exception handling and exit status. An observer labels only
compatibility/UV-version, lock-check, frozen-sync, own-source validation or metadata-write
events and allowlisted exception classes. It does not intercept subprocess execution or
publish stdout, stderr, exception messages, paths or environment values. The private helper
observation is validated against an exact schema before projection. A cancelled/terminated
helper may leave no such observation, which remains unknown; lifecycle refusal is never
promoted to a successful preparation or paired witness. The original preparation, aggregate
and process-cleanup deadlines and predicates are unchanged.

The optional lifecycle `first_scan_refusal` is either null or exactly four bounded integers:
`s` identifies supervisor/owned-descendant/settlement scan sites (1/2/3); `k` identifies
ENOENT/ESRCH/EACCES/EPERM/other-OS/value errors (1 through 6); `o` identifies task listing,
child-file open/context, read or PID conversion (1 through 4). `b` records only prior/current
identity presence, equality and departed-state bits; it contains no PID, TID, path or text.
A missing, duplicate or malformed diagnostic, zero/invalid refusal count, impossible bit
combination or accepted same-identity departed ENOENT does not export a tuple. This projection
never changes the existing refusal latch, cleanup completeness, return status or deadlines.
Synthetic tuple controls establish the software projection only. The historical two scan
refusals remain unlocalized: these new fields cannot retrospectively establish their cause,
prove that a future refusal will recur, or claim PG CALL, BEFORE or paired AFTER completion.

The preparation observer imports the fixed pinned `ci_environment` module through normal
Python resolution, then calls its unchanged `main()` with the original `SystemExit` behavior.
It refuses a preloaded module, missing/foreign source origin, or existing/symlinked active or
legacy bytecode cache before import; it does not evict modules or delete caches to proceed.
The imported file and spec origin are checked again. Healthy preparation and each existing
refusal keep their direct-helper exit, output, metadata and closed stage observations.

Optional `first_task_scan` contains only fixed phase/probe/classification categories and
booleans or null: whether the selected task was known before/after, whether its start identity
matched, and whether its observed state was departed. No PID, TID, path or start tick is exported.
A task entry observed absent is diagnostic only: it never clears a refusal latch or permits an
incomplete scan. The bounded synthetic thread-disappearance control proves one mechanism that
can produce a refused tuple, not the historical hosted cause or any PG BEFORE/AFTER witness.

A selected task's disappearance is never an empty successful scan. Only child-open ENOENT
for a previously known live task, positively absent afterward under the same live owner,
may attempt two complete fresh task/children censuses within the existing TERM_GRACE.
Both censuses must have identical task start identities and child sets; every observed
descendant is retained. Any denial, malformed data, reuse, overflow, deadline or instability
refuses and latches normally. No prior refusal is cleared. Final completion still requires
owned-descendant settlement, genuine kernel ECHILD and empty direct/adopted ownership.
This synthetic mechanism proof does not establish a PG CALL or historical hosted cause.

An optional `uv_install` field on an installer-stage refusal contains only bounded exit
integers, strict booleans or null and a six-field closed lifecycle projection from the
existing owned result. It distinguishes the installer-result predicates without publishing
installer output, commands, paths or environment. The refusal still reports UNKNOWN for
control, PostgreSQL CALL and cleanup; it never supplies a complete phase/witness. A healthy
installer proceeds under the existing unchanged predicates and deadlines. Missing or
malformed fields remain unknown. These future diagnostics cannot identify the failed
predicate in an older receipt which discarded the result.
