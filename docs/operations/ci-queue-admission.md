# CI queue admission experiment

> **Closed and retired.** `bu-q1hzqn.8` was closed as not worthwhile at current
> demand, no ruleset change was ever applied, and `scripts/ci_queue_admission.py`
> (with its test) was deleted in `bu-99t5s3.2`. The commands below are kept as a
> historical record; recover the tooling from commit `d4dd952ed` if the trial is
> ever reopened.

`bu-q1hzqn.8` owns a reversible capacity trial after pipeline PR #4530 lands.
No setting changes with this source. The setup script still defaults to five
concurrent merge-group builds. Build admission is independent of the maximum
five entries merged together; [GitHub documents both controls](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue).
The merge queue remains the sole landing route, with full shard membership and
required `check`, `guards`, and `frontend` verdicts unchanged.

## Evidence and administrative limits

The complete repository-created cohort on 2026-10-15 from 03:30 through 06:10 UTC
contained 27 runs and 399 job records. Overlapping positive, non-skipped closed
job intervals peaked at 22: twenty backend/Vitest children across five queue
builds and two short PR verdict jobs. A shard waited 1,243 seconds from its own
creation to start. This is observed job overlap and scheduling delay, excluding
earlier-created runs and other repositories, not a certified runner quota or
proof of physical utilization. At 06:07 UTC the live snapshot was quiet: one
running job and none queued.

Complete GitHub queue timelines gave these first-enqueue-to-merge observations:

| PR | First enqueue to merge | Final protected run to required contexts |
| --- | ---: | ---: |
| 4531 | 6,471 s | 1,428 s |
| 4534 | 4,176 s | 2,089 s |
| 4535 | 4,146 s | 1,376 s |
| 4537 | 1,035 s | 995 s |

PR #4531 includes its watchdog removal and requeue; using its latest enqueue
alone would hide 3,865 seconds. These four mixed-rollout observations are not
p95 or a causal experiment. The org hosted-runner-limits API was unavailable
and does not establish standard-runner concurrency. The session identity has
push permission but no administrative access. GitHub consequently omits
`bypass_actors`; [the rules API](https://docs.github.com/en/rest/repos/rules#get-a-repository-ruleset)
only returns that field to ruleset writers. Its absence means UNKNOWN, never an
empty bypass list. No other credentials should be sought to fill this gap.

## Capture and review

The stdlib tool makes bounded API reads with a 240-second total deadline,
20-second request limits and 4 MiB responses. It defaults to a read-only plan:

```bash
python3 scripts/ci_queue_admission.py --output /tmp/ci-admission/plan.json
```

The command prints a plan digest. Exit 0 means a complete READY plan was
recorded; exit 2 means NOT_APPLYABLE, retaining the inspectable partial live
snapshot and a fixed refusal reason. Exit 3 means collection or validation
failed. Existing files are never overwritten. Keep these captures outside the
checkout: complete administrator snapshots can contain hidden bypass actor IDs.
Files are created mode 0600, without changing directory permissions. When the
administrator runs as `tze`, root's user-run handoff may grant `g+r` on the
specific non-secret plan/capture/receipt files for `orca` review. Do not weaken
the tool's private default or apply that grant to credential files. The shared
handoff directory must already permit reviewer traversal.

A READY plan requires administrator authority, a complete original ruleset,
exact current baseline five and mandatory protections, and PR #4530 actually
merged. It contains the full original response, writable rollback payload,
candidate payload and prerequisite merge identity. The candidate changes only
`max_entries_to_build` from five to one. Every unrelated writable field,
including all bypass actors and future recognized rule parameters, is retained.
Unknown top-level fields refuse pending review. The ordinary setup script is
not the trial apply command: it constructs defaults rather than preserving live
administrative state.

Review the exact JSON and digest. With the authorized administrator identity,
run these explicit operations from the reviewed source checkout:

```bash
python3 scripts/ci_queue_admission.py apply --plan /tmp/ci-admission/plan.json --digest REVIEWED_SHA256
# At the recorded trial deadline, or earlier on a stop condition:
python3 scripts/ci_queue_admission.py rollback --plan /tmp/ci-admission/plan.json --digest REVIEWED_SHA256
```

Apply rechecks authority, prerequisite, full original response and digest before
writing, then verifies the entire writable live payload. A plan expires for
apply after 24 hours; **expiry never prevents rollback**. Rollback requires the
live payload still match the candidate and restores the captured original;
foreign drift refuses rather than being overwritten. A failed post-write check
is UNKNOWN and requires inspecting live state, not blindly retrying. Retain
command exit receipts and the printed verification digest. Read/check/PUT is
not atomic: GitHub documents no conditional ruleset PUT. Serialize admin
changes during these operations and inspect any ambiguous transport failure.

Root owns the practical administrator handoff and names the operator responsible
for timed rollback before application. No daemon applies settings or performs
unattended recovery. Source tests use mocked APIs, never live mutations.

## Natural trial and complete lead time

After DAG, coverage and weights rollout stabilizes, collect one natural 24-hour
baseline with admission five. Record exact relevant source/runtime identities.
Then apply admission one for at most 24 hours and restore five, regardless of
sample count. Avoid simultaneous CI tuning, synthetic test PRs, queue jumping,
extra runner spend or cancellation of foreign jobs. One admitted full build
still executes the complete DAG; improved effective capacity is a hypothesis.

The operator runs this finite collector from the reviewed checkout in the
shared user-run handoff directory. `capture_dir` is an absolute path outside
all checkouts; record `arm_start` and the applied setting separately. The explicitly verified GNU
`/usr/bin/gnutimeout` bounds the entire collector and its child process group to 24 hours.
Each request has the tool's own shorter bounds. This host's default `timeout`
is uutils; the recipes require the separate GNU binary and refuse its absence
or a different implementation. They launch no workflows:

```bash
test -x /usr/bin/gnutimeout || exit 1
/usr/bin/gnutimeout --version | rg '^timeout \(GNU coreutils\)' || exit 1
export capture_dir=/home/orca/.tmp/ci-admission/arm-UNIQUE
mkdir -p "$capture_dir"
date -u +%Y-%m-%dT%H:%M:%SZ > "$capture_dir/arm-start.txt"
collector_status=0
/usr/bin/gnutimeout --kill-after=10s 24h bash -c '
  while :; do
    snapshot=$(date -u +%Y%m%dT%H%M%SZ)
    since=$(date -u -d "20 minutes ago" +%Y-%m-%dT%H:%M:%SZ)
    python3 scripts/ci_queue_admission.py capture --since "$since" \
      --output "$capture_dir/capture-$snapshot.json" \
      > "$capture_dir/capture-$snapshot.receipt" 2>&1
    status=$?
    printf "%s\n" "$status" > "$capture_dir/capture-$snapshot.exit"
    sleep 60
  done
' || collector_status=$?
printf "%s\n" "$collector_status" > "$capture_dir/collector.exit"
```

Exit124 from the outer timeout ends this observation window; it is not a
measurement PASS. The final interrupted snapshot and any nonzero/missing
capture receipt are UNKNOWN. Rolling twenty-minute windows avoid applying the
100-run ceiling to an entire busy day. They overlap deliberately: union by
run/attempt/PR/event identity, and retain the original arm boundary. A burst
over100 still refuses; use a shorter window and retain the gap as UNKNOWN.
There is no permanent service or unattended settings write.

After the window, retain terminal metadata for every observed run, including
runs whose execution outlived a short window. This bounded read-only recipe
refreshes the union; a timeout/error leaves that run UNKNOWN. Store its exit
receipt. It does not claim that a captured attempt passed:

```bash
test -x /usr/bin/gnutimeout || exit 1
/usr/bin/gnutimeout --version | rg '^timeout \(GNU coreutils\)' || exit 1
export refresh_dir=/home/orca/.tmp/ci-admission/refresh-UNIQUE
mkdir -p "$refresh_dir"
refresh_status=0
/usr/bin/gnutimeout --kill-after=10s 20m .venv/bin/python - "$capture_dir" "$refresh_dir" <<'PY' || refresh_status=$?
import json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, "scripts")
from ci_latency import identifier, population
root, output = Path(sys.argv[1]), Path(sys.argv[2])
ids = set()
for path in root.glob("capture-*.json"):
    for attempt in json.loads(path.read_text())["attempts"]:
        ids.add(identifier(attempt["id"]))
if len(ids) > 500:
    raise SystemExit("UNKNOWN: refresh exceeds500 runs; split and review")
def read(endpoint):
    return json.loads(subprocess.check_output(["gh", "api", endpoint], timeout=20))
for run_id in sorted(ids):
    run = read(f"repos/tzeusy-org/butlers/actions/runs/{run_id}")
    count = identifier(run["run_attempt"])
    if count > 5:
        raise SystemExit("UNKNOWN: attempt bound")
    attempts = []
    for number in range(1, count + 1):
        base = f"repos/tzeusy-org/butlers/actions/runs/{run_id}/attempts/{number}"
        attempt = read(base)
        jobs = population(read(base + "/jobs?per_page=100"), "jobs", limit=100, complete=True)
        if (attempt["id"] != run_id or attempt["run_attempt"] != number or
            attempt["head_sha"] != run["head_sha"] or any(
                j["run_id"] != run_id or j["run_attempt"] != number or
                j["head_sha"] != run["head_sha"] for j in jobs)):
            raise SystemExit("UNKNOWN: attempt identity")
        attempts.append({"run": attempt, "jobs": jobs})
    path = output / f"terminal-{run_id}.json"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
        json.dump(attempts, out)
PY
printf "%s\n" "$refresh_status" > "$refresh_dir/refresh.exit"
```

Metadata still in progress remains censored; no terminal verdict is invented.
The operator can repeat a refresh with a new output directory after completion.
The original snapshots/receipts remain immutable. Re-read complete relevant PR
queue timelines with a final capture, then reconcile all merges at arm boundaries.
Run the existing budget observer separately at arm start/end, using an actual
short enough since window for its100-run bound:


```bash
python3 scripts/ci_queue_admission.py capture --since 2026-10-15T06:00:00Z --output /tmp/ci-admission/capture-UNIQUE.json
python3 scripts/ci_latency.py --since 2026-10-15T06:00:00Z --output /tmp/ci-admission/latency-UNIQUE
```

Use the actual arm start timestamp, not this syntax example. Capture retains
live queue `enqueuedAt`, position/state and source/base commits; all bounded
CI-created run attempts, heads, outcomes and identifiers with complete exact-attempt
job identity/status/timestamps; complete relevant PR
enqueue/removal/merge timelines, removal reasons and final source/merge heads.
Merged-PR search includes entries whose workflow began before the window.
Queue timelines do not supply historical source heads at every enqueue. Bind
those heads from successive live captures; mark an unobserved transition UNKNOWN
rather than inferring a source change from a final head. First enqueue across
same-source requeues remains the primary clock; report latest enqueue separately.

Capture admits at most 100 runs, 100 relevant PRs, 100 events per PR and five
attempts per run, 100 jobs per attempt, in a window of at most 48 hours. Overflow, incomplete search,
GraphQL errors, identity mismatch or truncation refuses. Choose shorter windows
and union identities without dropping cross-window queue episodes. Missing
captures or absent completion are UNKNOWN. Retain unfinished entry ages and
arm-boundary censoring. Attempt `updated_at` is retained metadata, never a
completion timestamp. Raw job timestamps retain failure/retry cost and ordinary PR impact without
downloading logs. The [existing observer](ci-latency.md) owns run/job timing;
this tool does not redefine its success-only objective or impose a new gate.

Compare first enqueue to actual merge p50/p95, completed merges per wall hour
and per hour with nonempty queue, arrivals/backlog, failed/retried/cancelled and
superseded groups, ordinary PR latency, and total job minutes. Also retain enqueue
to run creation and the observer's run to last required context, job scheduling
and execution. Include all same-source removals/requeues; count a merge once.
Stratify backend/frontend/docs work and label source/runtime differences. Do not
credit moving wait before workflow creation as an improvement.

At least twenty complete natural successes per arm are required for a p95 target
claim. Smaller cohorts are INSUFFICIENT, with denominators and directional
throughput still reported. Restore at 24 hours instead of waiting indefinitely.
Proposed conservative early stop: revert if two consecutive two-hour periods
with persistent backlog have less than 80% of matched baseline merges/hour
while queue age rises, or ordinary PR p95 exceeds 120% with twenty samples.
Any protection/population/control mismatch stops immediately. These operational
thresholds are provisional stop rules, not significance tests. Review workload
mix and named failures before attributing harm. A future candidate two requires
a separate reviewed trial. No configuration wins from run latency alone; the
p95 below 420-second natural service objective remains unearned.
