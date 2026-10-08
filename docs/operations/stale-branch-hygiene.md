# Stale branch and cache hygiene

Old branch heads retain their old Actions YAML. A current `push: branches: [main]`
filter does not protect a re-push of an old head with unfiltered push triggers.
Do not bulk-push, mass-rebase or rewrite those heads as a cleanup shortcut.

`bu-ly3lv5.3` supplies source preparation. A source PR, an archived spec or a unit
fixture does not authorize live deletion or satisfy the under-100, cache-hit,
actual PR-body repair, seven-day or fourteen-day outcomes. This move claims **no
wall-clock gain**. The historical storm and queue durations are context only.

## Operator boundary and preparation

Run `uv run --no-sync python scripts/ci_branch_hygiene.py --help` from the actual
repository checkout. The fixed provider is `tzeusy-org/butlers`; the tool refuses
an unrelated Git origin. Commands use argument arrays, capture provider errors
privately and print categorical outcomes. `inventory` and `plan` do not mutate
remote refs, caches or settings. `plan` writes independent recovery bundles.
`apply`, `restore` and `restore-setting` are dry-run unless `--execute` is explicit.

Inventory compares completely paginated branches with actual remote heads,
reads all PR states, local attached/detached worktree identities and nonclosed
canonical beads, and enumerates every cache against its declared count. These
are separate source observations. A closed PR name or a commit's backdated date
alone never proves current-head safety or current custody.

Root supplies a fresh custody JSON record, outside checkouts to be retired:

```json
{
  "repository": "tzeusy-org/butlers",
  "captured_at": "<actual UTC collection time>",
  "complete": true,
  "held_refs": ["<every additional live/foreign/uncertain ref>"],
  "held_shas": ["<detached or otherwise held object identities>"],
  "terminal_worktrees": [],
  "ref_activity": {}
}
```

This is root's actual foreign-custody attestation, supplemented by fresh Git and
canonical reads; a JSON boolean is not an owner signature or an isolation
boundary. Missing/expired coverage produces exclusions. A terminal path is added
only after its actual owner has released it and all evidence is retained outside
it. Clean state alone does not release a worker. For an unmerged abandoned head,
`ref_activity[ref]` must additionally bind the exact `sha`, `last_activity_at`,
`complete` coverage and its actual `source_receipt`; the youngest PR, ref-activity
or commit date must be at least fourteen days old. Missing ref-activity evidence
is UNKNOWN. Every abandonment still needs explicit approval of that exact head.

Only `agent/`, `qa/`, `fix/` and `codex/` refs can be candidates. Main, protected,
open PR, held, attached/detached live worker, recent and unresolved history refs
survive. Actual reachability from the bound main head is useful independent
history evidence; squash-merged exact PR heads additionally need their exact
recorded merge identity and independent recovery. Changed heads do not inherit
a prior PR's approval. Under 100 remote heads remains mandatory; a retained-set
lower bound over 99 is an incomplete outcome, not permission to delete exclusions.

## Recovery and approval precede mutation

Prepare an immutable inventory and plan, using a recovery store independent of
every checkout and its common Git directory:

```bash
uv run --no-sync python scripts/ci_branch_hygiene.py inventory \
  --custody <fresh-root-custody.json> --output <new-inventory.json>
uv run --no-sync python scripts/ci_branch_hygiene.py plan \
  --custody <fresh-root-custody.json> --store <independent-recovery-directory> \
  --output <new-plan.json>
```

Each bundle is fetched into independent storage, verified by a genuine isolated
bare clone and fsck, and bound by digest and exact head. There are no object
alternates. PR refs are discovery aids, not durable recovery. Plan output is
append-only and records every exclusion. Root fully reviews the source, recovery
and exact list, then obtains the author's explicit approval **of that list and
plan digest**. Engineering preparation does not need another generic permission.

The external approval record carries `digest`, `action`, `approval_reference`
and, for unmerged heads, `approved_abandonments`. It is an audit reference to the
real author approval, not a new signer, host service or self-asserted privilege.
`action` is independently `apply`, `restore` or `restore-setting`; a deletion
approval is not remote-restoration approval. Same-identity trusted operators can
rewrite files: this procedure does not defend against a compromised trusted host.

## Serialized apply, partial results and restoration

The exact lock is `<resolved git common directory>/ci-branch-custody.lock`.
For this checkout family that is
`/home/orca/orca/projects/butlers/.git/ci-branch-custody.lock`; worktree-local
`.git` files resolve to that same directory. It covers native healing/QA branch
and worktree creation, native removal/reaping, operator apply, and coordinator
claim, dispatch, reassignment, terminal custody release and affected worktree
preparation/removal. A coordinator must acquire it **before** changing canonical
ownership or publishing a dispatch, then re-read actual custody and worktrees,
perform the bounded claim/dispatch/reassignment and ordinary worktree operation,
publish its custody state, and release it. Terminal release similarly follows
confirmed worker stop/recovery and precedes any permission to retire that tree.

```bash
flock -n /home/orca/orca/projects/butlers/.git/ci-branch-custody.lock <coordinator-operation>
```

Acquire this exclusion first; do not hold a canonical mutation/dispatch lock and
then wait for it. Acquisition is nonblocking and refuses immediately when busy,
with no sleep/retry loop. Failed acquisition performs no canonical operation,
producer creation or retirement. Release is in `finally` on success and failure.
Do not nest it or acquire it around a native operation that already acquires it
internally. Initial QA read-only fetch may precede acquisition; branch/worktree
creation is fenced. Native busy custody refuses creation or defers best-effort
cleanup. Each native Git command has a 60-second communication bound; timeout
returns the ordinary nonzero tuple/caller failure path. Timeout or cancellation
stops the actual process group and reaps the owned child before releasing the
custody lock, including cancellation during creation. This is ordinary process
lifecycle handling under the existing trusted-host premise, not kernel or
malicious-hook isolation. This is cooperation by the existing trusted actors, not isolation from
a compromised same-identity host or an uncooperative remote actor. Before every
mutation the operator refreshes provider/custody/worktree state. The remote
expected-SHA lease independently refuses a changed remote head; it does not make
PR-state checks and remote deletion one atomic GitHub transaction.

`apply --execute --input <plan> --approval <record> --custody <fresh-record>
--output <new-result>` retains a before-IO receipt, verifies bundle digest and
restore, refuses drift/new PR/claims, and removes only released clean terminal
worktrees before local and expected-SHA remote references. Failed removal blocks
branch retirement. A lost acknowledgment gets read-only absent/same/changed/
UNKNOWN reconciliation; UNKNOWN never causes a blind retry. Receipts survive
partial actions. Restarting the same plan into the same receipt directory refuses
overwrite: root reconciles the existing stages before creating a new attempt.

An absent ref is idempotent; a changed ref requires a new reviewed plan. Remote
restoration is create-only, separately approved, and refuses any replacement
ref. Verify isolated recovery first: remotely republishing the old YAML can
recreate the storm. Original object recovery, setting reversal and cache rebuilding
are different mechanisms.

## Settings and cache evidence

Setting application changes only `delete_branch_on_merge`; its before value,
readback and independent rollback are recorded. It does not prune existing refs.
Node-cache eviction binds exact ID/key/version/ref, excludes main/active/uncertain
refs and non-node caches, and positively checks the recorded main survivor.
Playwright and uv key/restore/producer semantics are unchanged. Cache deletion is
irreversible; performance recovers by ordinary rebuild, not a fictitious restore.

Read back usage below **10,000,000,000 decimal bytes**, main node-cache presence,
actual remote count under 100, and a normal main-scoped frontend cache-hit receipt.
The tool's `cache_hit: NOT_OBSERVED` cannot be ticked from key presence or synthetic
provider controls. Unexpected new entries or unavailable main evidence require
reconciliation, never a wider eviction list. Setting success is separate from
branch/cache success. The final complete remote/cache list is refreshed and
approved by root; source workers execute no live mutation.

## QA and session-link guard

New native QA investigations fetch main successfully, resolve its immutable
commit and retain `QA preparation` attempt/base-SHA plus creator receipts. Fetch
or commit-resolution failure refuses before checkout/spawn. Existing follow-up
heads and the foreign QA sealed-artifact/sandbox/publisher/lineage holds remain
unchanged. Main movement alone does not authorize rebasing an active PR or
resetting a worker. Native legacy cleanup policy is unchanged apart from shared
exclusion; the operator's recoverable retirements do not adopt that policy.

Current guards read validated live title/body for the fixed PR on execution.
Manual rerun sees a corrected body without an empty commit; it still tests the
original trigger-head commit range, not a newer source head. The live fetch is
admission at that point, not an atomic GitHub edit/merge lock. Mandatory API or
identity failures refuse without stale fallback. Public scanner mode reports
fixed categories/counts, never matched text; its private matcher/Finding/formatter
interfaces and exact terminal commit trailer exception remain. Comments remain
best effort. Read-only token/event/checkout policy and terminal fan-in are retained.

Root's actual body-edit criterion uses an already affected author-owned PR and
keeps the existing source head. Do not plant a forbidden public canary. Private
synthetic forbidden-body positives are causal software evidence, not an actual
GitHub edit or authentication receipt. If no affected live case exists, this
outcome remains NOT RUN.

## Observation and completion

The actual apply readback retains declared UTC `T0` and `plan_digest`. `observe
--input <actual-apply-readback> --output <new-observation>` reads **all** CI push
conclusions over separate half-open seven- and fourteen-day windows. It subdivides
capped queries, follows every page, reconciles counts and IDs, and positively
witnesses main through the same reader. Missing pages, unresolved caps, absent
positive or incomplete elapsed time are UNKNOWN. A non-main event of any conclusion
fails the actual window. Preserve evidence; no silent reset of T0.

Root owns this observation tail without keeping a source worker idle fourteen
days. Normal owning native adoption is source preparation; exact-head normal CI,
independent review, full protected merge_group, author-approved live actions,
actual body rerun and both elapsed windows are separately mandatory before whole
closure. No slice is selected for deferral and no gain is claimed.
