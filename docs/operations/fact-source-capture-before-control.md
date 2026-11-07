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

Before fixture teardown the observer requires positive observations of all three distinct
capture Tasks, bound to the exact issuer, inboxes and runtime coroutine code by the same
narrow tracer. Task references stay in RAM; coroutine resumption deduplicates the same identity,
and missing/conflicting observations remain UNKNOWN. It passively waits at most five seconds
for these known tasks, then checks actual completion. It never cancels them, consumes their
results or scans unrelated tasks; a pending same-name foreign coroutine is irrelevant. This
fits inside the unchanged ten-second observation envelope alongside the existing five-second
pool acquisition limit. It also checks pool reacquisition, transaction release and `SELECT 1`,
zero partial durable and in-memory reports. Complete actual fixture/process teardown and an
unrelated sentinel must survive. Observation never replaces
or mocks the pool, migration, resolver, transaction or test body. After complete cleanup the
candidate runtime is restored exactly and the same node executes once in a fresh process.
It must pass setup/CALL/teardown. No source retry is allowed.

The old test remains **FAILED**. `WITNESS_COMPLETE` is a separate aggregate control verdict for
that observed RED, paired candidate GREEN and complete cleanup. Its schema stays distinct
from complete shard, smoke, coverage or release evidence. Existing candidate AFTER evidence
and the mandatory full protected source tree remain separate.

The separate AFTER-only `after-diagnostic.json` annex is nonqualifying. Its fixed category
and public symbolic operation describe the CALL failure plus bounded observed resolver error
events, including errors caught before `resolve_identity` returns `None`. Observed events do
not establish causality. Collection uses only exact candidate-hash/code identities and the
three bound capture Tasks/issuer; foreign code, tasks and issuers remain opaque. At most 24
unique observations and eight aggregate records are retained in RAM; overflow or unmapped
sites are explicitly truncated. Normal coroutine protocol events are excluded. No dynamic
class/module names, messages, arguments, `failure_class`, SQLSTATE, locals, identifiers or
raw tracebacks leave the private process. Missing binding or a preexisting tracer is unavailable.
Context statement labels cover both entry and exit; a transaction-context observation does
not imply a locked-row read failed, even when the context exits after a report write.

The producer reads at most 8192 bytes from a private regular nonsymlink file, validates exact
keys/types/enums/counts/binding and reconstructs the annex before retaining a failed AFTER
refusal. Invalid/missing input becomes closed UNKNOWN. Existing phase keys and acceptance
predicates are unchanged: an annex never repairs failed AFTER or qualifies a witness. It adds
no await, timer, query, cancellation, retry or timeout extension. Existing artifact upload
includes the annex; actual native failure diagnosis and full paired success remain distinct.

The producer invokes the existing owned `install_ci_uv.py` within its own confined domain,
with a private path-output sink. Ordinary workflow installer consumers remain unchanged by this control; installation is part
of the control rather than a new workflow installer step.
The executor-local `scripts/fact_source_capture_before_supervisor.py` owns each execution
in a dedicated Linux child-subreaper domain established before its sole workload exec.
It uses default SIGCHLD zombie retention, one waiter and validated Linux all-child waits
(`__WALL`); unsupported flags refuse before exec. It never calls `Popen.poll` or `wait`
as a competing reaper. The retired shared action remains absent.

The versioned process-settlement basis is `linux-subreaper-all-child-v2`. Live reads of
only the supervisor's own direct-child relation discover signal targets; they do not
prove that a census is complete or empty. A non-consuming all-child `waitid(WNOWAIT)`
proves each signal target is a direct/adopted child held unreaped by this sole waiter.
No consuming wait occurs between selection and signalling, so a numeric target cannot
be recycled into the unrelated sentinel. Zombie leaders remain held while their worker
threads live. Bounded TERM/KILL, adoption and reap waves run within the existing 20ms
grace and 1.5-second settlement deadline. Wait0 means children remain pending.

Only the exact root outcome followed by genuine all-child ECHILD and no held child,
hard failure, overflow or expired settlement bound qualifies process cleanup. The producer
requires exactly one ordered ready/outcome/diagnostic/settled sequence, zero refusal counts
and the versioned positive kernel proof. Normal uncancelled supervisor exit is additionally
required for a witness. Each accepted phase receipt
includes `process_cleanup_basis` and strict `process_cancelled`; missing, old or malformed
proof refuses. Physical process cleanup after cancellation can remain positively observed.
The v2 settlement independently exports cancellation; reserved exit3 preserves its visibility
if a signal arrives after the proof is built/written or main returns. The terminal handler
stays armed through CLI exit. TERM/INT termination during interpreter teardown is also
classified as cancellation, while cleanup remains provable only with the complete valid
kernel record sequence. Missing records still leave cleanup UNKNOWN.

The producer combines supervisor and local cancellation in `run_owned.cancelled` and its
closed observation. Unexpected supervisor cancellation also latches aggregate cancellation.
Installer and environment admission reject the flag; a phase records `process_cancelled`
and refuses a cancelled lifecycle even with physical cleanup, child0 and otherwise healthy
observations. Final summary sealing checks the aggregate latch. Cancellation or a
phase/aggregate deadline therefore cannot produce a witness. Parent or control-reader loss remains a fatal refusal.

This proof assumes a trusted closed rooted workload: no external daemon launch, external
ptrace/reparenting interference or ancestry escape. It is process settlement, not hostile-code
containment or Docker/database teardown. Only the unchanged genuine fixtures establish those
resource-cleanup facts. A discovery omission may delay signalling, but an unreaped live child
keeps all-child wait pending; if closure is not earned by the existing bound, cleanup refuses.
Encountered permission, malformed ownership/identity, hard I/O, unsupported wait and overflow
failures remain latched. No previous failed receipt is promoted or reused as a witness.

The aggregate 2480-second budget starts before checkout and includes the preserved 640-second UV installer and
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

The former branch-only recovery accepted two equal live foreign-task censuses after
known task retirement. Linux does not guarantee complete children lists under concurrent
exit, so this predicate and its recursive scanner are retired. The historical known-before
positive and absent-before refusal remain scoped mechanism evidence. Their executing
survivor is the kernel-domain control: real listed-thread retirement before the first legacy
probe, resistant detached descendants through a live intermediary, zombie-leader/live-worker
settlement, adoption/reaping, sentinel survival and genuine final all-child ECHILD. The
same positioned controller makes the old source fail the positive-settlement assertion.
Deliberately omitted direct discovery must remain pending and refuse at deadline; an inverse
wait0-as-ECHILD mutant must fail that assertion, followed by the restored refusal control.
These software/kernel controls do not establish the hosted incident cause or a PG witness.

The original control42350015004/1 remains FAILED/UNKNOWN: helper0, three latched scan refusals,
absent selected task before/after and settledfalse, despite observed final kernel closure.
The new protocol neither erases that refusal nor declares it a complete live census.

An optional `uv_install` field on an installer-stage refusal contains only bounded exit
integers, strict booleans or null and a six-field closed lifecycle projection from the
existing owned result. It distinguishes the installer-result predicates without publishing
installer output, commands, paths or environment. The refusal still reports UNKNOWN for
control, PostgreSQL CALL and cleanup; it never supplies a complete phase/witness. A healthy
installer proceeds under the existing unchanged predicates and deadlines. Missing or
malformed fields remain unknown. These future diagnostics cannot identify the failed
predicate in an older receipt which discarded the result.
