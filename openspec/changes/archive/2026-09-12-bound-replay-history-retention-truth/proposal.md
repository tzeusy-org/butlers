## Why

The archived 2026-05-19 ingestion-console delta carried a requirement titled
"90-day replay history retention". No 90-day window has ever existed in shipped
code. PR #3807 (bu-9965w) correctly DROPPED that requirement rather than re-ADD
a false one, and rejected a `## RENAMED` block that would have laundered the
content change past the guard.

Dropping it was right, and it left two things unresolved.

**First, the false number is still live in `openspec/specs/`.** The archived
delta did not invent "90 days"; it inherited it. The shipped baseline
`connector-filtered-events` still says, at its Retention policy scenario, that
partitions "older than 90 days" may be dropped. The pruner that actually exists
defaults to **12 months**
(`src/butlers/jobs/retention.py:151`, `_FILTERED_EVENTS_DEFAULT_KEEP_MONTHS = 12`).
So the spec and the code disagree about the only retention number this
capability has, and the archived fiction was a copy of a live one.

**Second, the scenario describes a sweep that does not run.** It reads as though
a scheduled maintenance task prunes partitions. Three independent gates each
prevent that today:

- no roster `butler.toml` schedules `filtered_events_partition_prune` at all, so
  the job is never invoked;
- `enabled` defaults to `False` (`src/butlers/jobs/retention.py:157`), and the
  pruner returns before touching the database when it is not set;
- `dry_run` defaults to `True` (`src/butlers/jobs/retention.py:158`), so even an
  enabled invocation counts candidates instead of dropping them.

The honest description of `connectors.filtered_events` today is: **nothing is
ever deleted.** Storage grows without bound.

Replay history itself is not in doubt and does not need a new contract. It is
served from `public.audit_log`
(`src/butlers/core/ingestion_events.py:2045-2053`), which already carries an
explicit, accurate, shipped requirement: "The audit log SHALL be retained
indefinitely. No retention job, no expiry, no deletes."
(`openspec/specs/dashboard-audit-log/spec.md`, Audit Log Retention). A
repo-wide static guard already forbids `DELETE FROM audit_log`
(`tests/api/test_audit_log.py`). Restating that guarantee inside a second
capability would create two owners for one fact, which is how the original
divergence started.

What is genuinely unwritten is the **relationship** between the two: a
replay-history entry is retained forever, while the event payload it refers to
sits in a partitioned table that a future sweep could drop. Those two ages can
diverge, and no requirement says so.

## What Changes

- Correct the `connector-filtered-events` Retention policy scenario to state the
  shipped truth: the pruner exists, its default window is 12 months, and it
  ships disabled, dry-run, and unscheduled, so no partition is dropped today.
  Every other clause of the requirement is carried forward unchanged.
- Add a requirement stating that replay-history lineage and the event payload it
  describes have **different** retention lifetimes, that lineage is the durable
  one, and that no component may assume the two age out together.
- Add a requirement that enabling deletion is an owner decision: turning on a
  sweep requires a recorded retention window, not merely a config flag.
- Pin all of it with tests that fail if a pruner becomes scheduled, if either
  safety default flips, if replay history is moved onto the prunable table, or
  if any sweep reaches the audit log.

This change deletes no data, adds no pruner, and does not enable an existing
one. It makes the written contract match the code.

## Open Owner Decision (deliberately unresolved)

**Choosing a number of days after which real owner data is deleted is not an
engineering-judgment call, because the loss is irreversible.** This change
therefore does not pick one. It documents that no deletion happens and pins that
property. The decision below is left for the owner.

**Question:** should `connectors.filtered_events` keep growing forever, or
should a bounded window be enabled?

The table holds one row per message a connector observed and deliberately did
not forward. Filtered rows carry a bounded preview with the raw payload redacted
(the Filtered-Content Privacy Tier requirement); errored rows retain the full
payload for diagnosis and replay.

Option A -- **Keep forever** (today's behaviour; no change).
Cost: unbounded storage growth on the highest-volume ingestion table, and
indefinite retention of errored-row payloads that were never forwarded.
Benefit: no irreversible loss; replay stays possible for any event, at any age;
zero new risk.

Option B -- **Enable the existing pruner at its 12-month default.**
Cost: events older than 12 months become unreplayable, and their payloads are
gone permanently. Partition DROP is not recoverable without a restore.
Benefit: bounded growth using the window already written into the code, so the
spec, the code, and the behaviour agree with no new number invented.

Option C -- **Enable at a shorter window (for example 90 days, the number the
archived delta claimed).**
Cost: the largest irreversible loss of the three, and the shortest replay
horizon. It would make the archived claim true retroactively, which is a poor
reason to choose a number.
Benefit: the tightest privacy posture and the smallest table.

Option D -- **Split the window by status:** prune `filtered` rows on a short
window, keep `error` rows longer.
Cost: partition-level DROP cannot express this, so it would need row-level
deletes and a new mechanism -- materially more work than the other options.
Benefit: matches the actual privacy asymmetry already in the spec, where
filtered content is minimal-retention by policy and errored payloads are
deliberately kept for diagnosis.

Resolving this requires an owner decision recorded as a retention window. Until
one exists, Option A is the shipped behaviour and this change describes it
accurately.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `connector-filtered-events` -- the Retention policy scenario is corrected from
  a 90-day claim to the shipped 12-month disabled default, and the capability
  gains an explicit replay-lineage divergence contract and an owner-consent gate
  on enabling deletion.
