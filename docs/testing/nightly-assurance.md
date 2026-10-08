# Nightly assurance

Nightly is supplementary assurance. Both future-date offsets, the full selected
`tests/ roster/` population, the schema/migration species and all four exact-image
kernel proofs remain required. The merge queue still runs the complete required
gate; nightly does not quarantine or replace any of its checks.
This change claims no wall-clock gain.

## Evidence and authority

Each invocation retains a versioned external exit receipt, a selected safe-node
manifest and phase outcomes, sanitized JUnit and timings. Raw JUnit stays in a
runner temporary directory and is removed after the existing sanitizer runs.
Parameter values, assertion bodies, captured stdout and provider diagnostics do
not enter retained evidence. A function-local case number is bound to that
invocation's selected manifest; it is not a published parameter identity.

The CI assessor is provisional because its own workflow has not yet completed.
It publishes a fixed, versioned GitHub issue marker as **evidence transport**.
An issue body, caller actor string or missing artifact cannot authorize a Beads
command. The trusted host independently reads the completed workflow, final
attempt, actual jobs and exact named artifacts. Missing/cancelled/incomplete
evidence stays UNKNOWN, with any reached failures retained. Verified terminal
workflow failure still qualifies as red even if the artifact producer died.

Two adjacent scheduled UTC dates with verified failure authorize escalation.
A rerun replaces its own night's verdict; it is not another night. Changed sets
on the second date still form two reds, but key their explicit failing set.
Manual branch controls have a separate canary scope. Only complete green resets
the incident; unknown/cancelled/missing dates never fabricate recovery.

## Existing host coordinator step

The host can obtain a finite read-only plan with its existing authenticated `gh`
and `bd` identity:

```bash
uv run --no-sync python scripts/reconcile_nightly_incidents.py \
  --receipt .tmp/nightly-reconciliation-plan.json
```

This does not install a daemon or write Beads. The existing coordinator cycle
owns apply. It passes its already-open lifecycle-lock descriptor to the bounded
child with `subprocess.run(..., pass_fds=(lock_fd,))`, supplying:

```text
--coordinator-apply --coordinator-lock-fd <inherited-fd>
--coordinator-owner <existing-coordinator-assignee>
--repo-root <canonical-checkout>
--export <host-export-directory>/incidents.json
--receipt <ignored-current-reconciliation-receipt>
```

The descriptor must name the canonical checkout's existing regular
`.beads.gate.lock`. The flags and FD are a serialization check, not authentication
or isolation from another same-identity host process. The owner and host remain
trusted under the existing doctrine. No credentials enter CI, the export or a
model/container session. A coordinator already holding the lock passes that
same open-file description; it does not ask the child to open a competing lock.

The reconciler searches the globally unique `gh-issue:<number>` before creating
and independently reads the incident back. A lost create ACK therefore recovers
the existing row. File-cluster Beads use deterministic source-path references;
the incident's node-to-owner map retains prior unresolved work. Recovery closes
the escalation incident and transport issue only. It never closes cluster work
or overwrites an active claim. A recurrence reopens the same incident with a new
episode. Pagination and request ceilings are explicit; exhausting either writes
an unavailable receipt and does not refresh the export as an all-clear.

The transport marker retains the red failure-set key when its issue closes.
Recovery has a separate green run/attempt/head binding, independently verified
against terminal evidence and ordered after that red. Copied, stale or forged
recovery is unavailable. The same failure set later reopens that same issue and
external reference, then the same Bead with a new episode; a changed set has its
own issue. Software conformance covers this full transport and disposable CLI
path, while actual host adoption and recovery still need their own readback.

The always assessor can publish before its workflow is terminal. Such a marker's
verdict and failure digest are provisional hints. The host requires the exact
same repository/workflow/run/attempt/head/event/ref/UTC-date envelope, fetches
genuine completed evidence, and explicitly promotes the issue to that terminal
failure-set binding with independent issue readback before an applied incident
command. A forged completed verdict or stale identity cannot use this exception.
When another provisional issue converges on an established failure set, the
stable incident issue is retained and the other becomes a closed terminal-bound
evidence alias. Its canonical issue and genuine matching key are independently
checked; the alias never authorizes another Bead. Changed terminal sets remain
separate, and genuine terminal-green triage closes without inventing red work.

## Runtime export and topology

The host atomically replaces a bounded regular `incidents.json`. Its allowlisted
fields contain version, repository/workflow, as-of time, incident and episode
IDs, allowed status, issue/run/attempt/SHA/date and evidence digest. They contain
no test messages, sender/provider data, credentials or arbitrary URLs. The runtime
reads with `O_NOFOLLOW | O_NONBLOCK`, verifies a regular file and enforces bounds
and freshness. A missing, stale or malformed source is UNAVAILABLE, not zero.

Compose exposes **only the dedicated export directory**, read-only, at
`/run/butlers-nightly`. A directory mount observes atomic inode replacement.
The directory contains no `.beads` configuration or credentials. Create it as
part of the trusted host deployment; never make a missing mount's placeholder
an evidence claim. `0400` or a same-UID mode check does not establish isolation.
The exact-image empty-root mount plan excludes this path; its actual kernel
isolation remains a separate four-test obligation.

`BUTLERS_NIGHTLY_INCIDENT_EXPORT` selects the absolute fixed consumer file,
default `/run/butlers-nightly/incidents.json`. `butlers up` uses the Compose mount.
An independent `butlers run switchboard`, separate API/connector layout or
Kubernetes deployment must explicitly mount the same dedicated read-only
directory and set this process environment. It must not copy host tracker
credentials or add an egress exception. Source wiring does not prove these
deployments or the coordinator cycle have adopted it.

The registered hourly Switchboard job uses the existing quiet-hours/context,
owner recipient and Messenger delivery primitives, at normal priority. Under
real `butler_switchboard_rw`, a transaction lock and existing `public.audit_log`
commit `attempting` **before** transport. Ambiguous acknowledgement remains
uncertain and never automatically sends again. A confirmed outcome persists
independently. A savepoint lets failed ledger persistence retain that known
outcome; only the ledger write retries. The real `notify` ledger row and its
marker binding commit together, then a separate acquisition verifies both.
No new role, grant, table or global exactly-once notify guarantee is introduced.

## Clock and kernel proof limits

The wrapper explicitly uses `FAKETIME_DONT_FAKE_MONOTONIC=1` and measures the
actual installed library's package version, hash, controller/worker wall and
monotonic observations. Progressing start-at resets when an independent child
execs; the receipt records that behavior rather than assuming one shared origin. PostgreSQL
clocks are independent of the pytest process; DB-authoritative fixtures seed
relative to the actual DB clock. A source clock correction is not SQL proof.

A clock-preflight launcher failure retains only fixed error indicators and a
bounded diagnostic of the same program through the wrapper's own Python
interpreter, under the same preload environment. Raw child bytes and exception
arguments are discarded. A positive direct-interpreter observation never
rescues a failed `uv` launch: the original preflight remains non-green with zero
test cases. Phrase indicators distinguish observations, not underlying causes.
Worker/service/container recovery causes need real endpoint and phase evidence.

Folded hour/minute jobs run only after the original same-run species genuinely
succeed. Skipped or unknown folded jobs cannot count as a fully green night.
The progressing UTC starts are 15:30 (23:30 Singapore) and 23:59:40. The named
QA boundary subphase must additionally prove the relevant assertion crossed its
milestone; starting a whole suite near midnight alone is insufficient. Its
separate serial two-flag phase atomically changes the supported timestamp file
between the resumed tick and the original zero-repeat assertion. In-place file
truncation can make the library parse an empty clock, so this control never
rewrites that file in place. Its actual minute crossing, unchanged monotonic
clock, selected manifest and command remain separate phase evidence.

Exact-image diagnostics distinguish nonempty output from EOF and retain only
closed process/operation/error labels. A label is not per-case attribution or
causal syscall proof. Preserve real unprivileged identities, actual gate release,
pidfd termination, detached descendants, peer isolation and signer/environment
denials when diagnosing a failure. No shell fallback, privilege widening, global
sysctl or generic security-profile relaxation is authorized by this feature. The exact-image
job uses the standard Ubuntu 22.04 runner after the same source's complete four
original kernel tests passed there without skips. The matched Ubuntu-latest
species failed UID-map launch. Kernel/OS and independently built image identities
remain explicit; this environment correction does not isolate AppArmor as the
cause, adopt a dumpability counterfactual, or prove deployment or scheduled nights.


## Delivery observations

Implementation, normal native adoption and the protected exact-head merge gate
precede the required **three adjacent fully green real scheduled UTC nights**.
Those nights, actual injected-red incident creation/update, host adoption and a
real ledger decision remain mandatory original outcomes. Software subprocesses,
planted PG conformance, manual canaries and retries do not supply those night or
deployment observations. The worker hands off without waiting three days; the
coordinator retains the observation obligations and closes only on real proof.

For a root-authorized branch conformance episode, the same trusted host may use
`--conformance-run <actual-terminal-workflow-dispatch-id>` with a **separate**
disposable export path. It still independently fetches the same nightly
workflow, actual agent branch/head/attempt and artifact census before any apply,
and still requires the inherited coordinator lock. It cannot write the default
production export, change the scheduled streak, or count as a scheduled night.
Readonly remains the default. Actual host invocation and real tracker/ledger
readbacks are required evidence; describing this command does not enact them.

The optional manual `minute-old-cron-control` input is restricted to the
isolated folded-minute barrier. It materializes the complete current scheduler
test module under an ignored historical filename, changing exactly PR4310's
QA cron literal. Its function executes against the unchanged current helpers,
fixtures, parameter cases and assertions. The receipt binds both full source
hashes. Ordinary and scheduled runs do not neutralize it, and a controlled red
never counts toward the required scheduled streak. Source materialization alone
is not PostgreSQL or minute-boundary evidence.

The Finance reconciliation repair compares database-owned feed timestamps with
an actual database clock read. Its existing real-PostgreSQL settlement control
also advances only the caller clock and retains the binding assertion. An
actually stale feed remains unverifiable. Delivery-retention presentation and
elapsed-allowance fixtures similarly plant dates in the database clock used by
their admission predicates; the attested liveness fixture plants its observation
in the runtime clock used by the expected-signal evaluator. Every other dated
failure remains individually unclassified until its own positioned evidence is
available; these repairs do not establish that all nightly failures share a cause.

The two filtered-partition pruner controls describe literal June 2026 partitions.
Their test-only clock is fixed to that declared month instead of silently aging
with the runner date. Every original keep/drop assertion remains, and the same
January partition has a positive eligibility companion a year later. Real
libfaketime software reproduction was red at +120 days before this fixture
repair and green at ordinary/+45/+120 days after it; it is not SQL proof.

Reconciliation reads current canonical assignment/status before every incident
mutation and applies the installed CLI's owner/status guards. Independent readback
preserves the complete existing contract, unrelated metadata and relation edges.
The CLI's close command has no such guards: automatic recovery therefore uses a
guarded closed-status update only for an unpinned ordinary owned incident with
no dependency/dependent edges. Other recovery states remain explicitly unavailable
for coordinator adjudication. Active foreign file-cluster ownership is retained
and classified without mutation; closed own clusters are guarded-reopened on
recurrence, while closed foreign or unknown-owner coverage is never called active.

A green exact-image assessment requires the exact four original selected safe
nodes, each with a nonskipped PASSED outcome. Successful job/exit flags cannot
substitute four skips, missing nodes, duplicates or unrelated replacements.
Existing legitimate named skips in other whole-corpus species remain permitted.

All existing timeout-register rows remain unchanged. Three added job rows are
explicitly provisional: folded-clock90 minutes, assessor10 minutes and manual
kernel diagnostic30 minutes. The folded bound covers the full3600-second
population watchdog and its30-second abort grace, up to two original300-second
QA cases and abort grace, UV640 plus setup/artifact reserve. These finite new-job
bounds are command envelopes, not actual healthy p95 or wall-clock-gain evidence.
The original75-minute faketime-matrix bound remains unchanged. Its enforcing
3600-second ABRT/30-second KILL and300-second thread argv now come from the
actual wrapper; the owning contract executes that producer and checks the same
scope, marker, worker and timeout invariants rather than searching copied prose.
