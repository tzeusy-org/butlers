## Context

See `proposal.md` for the approved problem and scope. Doctrine/spec baseline:
`9ffee3f92de8145dfdf88f4dfe50b44a73371432`.

[Observed] `approvals/previews.py` serves both dashboard safe previews and
notification-card decisions. Neither merge tool name is registered. Adding a
normal renderer alone would therefore unlock both generic dashboard approval
and notification buttons without structural review.

[Observed] `MergeCompareDialog` loads Relationship's structural comparison but
commits through the standalone merge mutation. Using that mutation from a
parked request would leave its approval unresolved. The shared approval executor
already dispatches the original handler, including Relationship's legacy
`entity_merge` alias, and the single merge authority writes merge history and
fleet-rebind receipts. Its target update does not overwrite `entity_type`.

## Goals / Non-Goals

**Goal:** compose these existing paths into one authenticated owner review of
the original pending operation, with no model invocation.

**Design boundary:** retain the merge algorithm and existing approval status
machine. Non-goals are listed in `proposal.md`; this adds no schema, domain
capability, standing rule, or new authentication factor.

## Decisions

### D1: A typed merge-review envelope keeps older clients safe

Extend the approval-detail response with optional `entity_merge_review`:

```text
entity_merge_review: {
  source_entity_id: UUID,
  target_entity_id: UUID,
  displayed_call: { tool_name: string, args_digest: SHA256 }
} | null
```

For a valid, pending Relationship merge, return that context and keep generic
`call_preview = null`. An older dashboard consequently remains disabled instead
of ignoring a new review flag and exposing its ordinary Approve button. Other
tools keep their existing `call_preview` contract. Missing, malformed, identical
IDs or unhandled executable arguments never earn merge-review eligibility.

Both merge names resolve to the same review behavior, but `displayed_call` keeps
the original stored tool name and hashes the original stored arguments. Do not
rename a legacy row to manufacture eligibility. The arguments remain only the
existing executable merge command; review data is response metadata.

Rejected: registering a normal shared preview and adding only a browser flag.
Old clients and notification builders would treat its existence as permission
for direct approval.

### D2: Reuse comparison content, not the standalone mutation

The new approval review uses the existing owner-authenticated
`POST /api/relationship/entities/compare`, with the source and target taken
exclusively from the typed approval context. Extract reusable comparison content
from `MergeCompareDialog`; preserve its standalone survivor picker and mutation
for existing entity-page callers.

In approval mode, label the columns **Absorb source** and **Keep target** and
show names, types and UUIDs. Show aliases, existing per-entity facts and provenance,
shared identifiers and divergent single-valued facts using the current compare
contract. Explain tombstoning, target-type preservation, existing conflict rules,
and asynchronous fleet reference rebinding without promising all receipts are
already complete. A name match is a candidate, not proof of identical identity.
No survivor selector exists in this mode.

Key comparison loading and rendered eligibility to
`(action_id, tool_name, args_digest, source_entity_id, target_entity_id)`. Clear
eligibility before changing the selection or hiding/loading/erroring the review,
and ignore late responses for a different key. A comparison for the inverse pair
or an earlier stored call cannot unlock this operation.

The dossier and shared summary-review dialog use this same review content.
Keyboard approval and delayed triage enter the same final confirmation; neither
can execute directly from an ID-only rail item. The confirmation names both IDs
and types as well as names, so identical display names remain distinguishable.

### D3: Keep the existing approval wire binding and executor

After comparison, final confirmation submits the exact `displayed_call` that
accompanied the rendered merge review to the existing approval endpoint. No
`edits` are allowed for this merge decision: the source/target direction cannot
change after review. Both flat and legacy routes validate the stored tool/argument
digest under the pending-action lock before decision, audit or dispatch. They
recognize the dedicated merge-review binding without treating a generic raw-args
dump as a safe preview.

The client does not assert the actor and does not submit a new merge command.
Dispatch uses the existing owning daemon and shared executor; the merge service
remains the sole authority. Owner authentication is unchanged. Showing a view
is a UI requirement, not a new authentication factor or an assertion that the
server can prove a person read it.

On success, the original approval records execution and the service writes its
merge review and rebind cohort. Refresh the approval dossier/list, affected
entities and merge-review data. Represent pending/failed rebind receipts honestly.
No separate standalone merge followed by best-effort approval bookkeeping.

### D4: Separate notification eligibility from dashboard review

The dashboard-specific merge resolver does not register these tools for ordinary
notification-card decisions. Merge notifications offer the dashboard deep link
as **Review merge**. Existing delivery keys, callback signatures, presentation
digests, quiet hours and digest collapse stay intact. The authenticated callback
decision path refuses approval for these merge tools even if an old or manually
constructed callback is supplied; it directs the owner to review instead.

No generalized capability-policy rewrite is needed. Keep the policy scoped to
`memory_entity_merge` and Relationship's legacy `entity_merge`.

### D5: Preserve lifecycle and failure distinctions

```text
pending -> loading comparison -> reviewable -> final confirmation
                                             -> approved -> executed
pending -> rejected / deferred / expired              -> execution failed
```

Loading/error/cancel/selection changes perform no decision. Deny uses the existing
approval rejection path, not Relationship's separate dismissal mutation; Defer
retains its delivery-timer semantics. Existing rejected/abandoned pair retention
continues to prevent curation from silently recreating the owner's decision.

A changed stored call fails before approval. If an entity is unavailable during
comparison, keep approval disabled with retry/context. If it becomes unavailable
after review, the merge authority revalidates under its locks and fails without
substituting an entity. Preserve the existing execution-failure outcome:
`approved`, null execution result and an execution-failed event, with explicit
operator recovery. Do not report success or automatically retry.

The comparison shows current context, not a promise to freeze every fact until
dispatch. Existing merge-time transaction/conflict rules continue to govern fact
changes; this feature adds no long-lived entity locks or fact-version protocol.

## Risks / Trade-offs

- **Incomplete entry-point sweep:** enumerate dossier, summary dialogs, keyboard
  triage and both HTTP decision routes in verification; all share the same rendered
  review key and original-call snapshot.
- **Cross-type confusion with identical names:** explicitly show UUIDs, roles in
  the comparison when available, the type of each record, and resulting target
  type. No type-based rejection or data reclassification is introduced.
- **Stale or repeated confirmation:** existing action locking/currentness checks
  remain authoritative; clear stale browser eligibility and disable repeat
  submissions while a confirmation is pending.
- **Two commits in existing execution lifecycle:** approval execution and merge
  storage do not become a distributed transaction. Retain the executor's current
  recovery semantics and add no automatic replay or exactly-once claim beyond the
  existing guarantees.
- **Existing comparison payload size:** the current queries return all qualifying
  active facts without pagination; scrolling the dialog does not bound the
  response. Reuse the existing queries, exclusions and presentation, and exercise
  a large response during browser verification. This repair adds no second dataset,
  model costs or arbitrary truncation credited as a complete preview. Pagination
  redesign is separate scope.

## Migration Plan

No database migration or historical-row rewrite is planned. Implement backend
typed review support and notification refusal before enabling the new frontend
controls in the same release. An older frontend sees null generic preview and
remains blocked; a newer frontend against an older backend also remains blocked
because the typed review context is absent. Retained valid pending rows become
reviewable on read. Historical approved rows keep explicit Retry/Abandon policy.

Rollback removes the new review controls/support and returns affected pending
requests to disabled approval. It does not reverse committed entity merges,
delete audit/receipt history, or replay any action.
