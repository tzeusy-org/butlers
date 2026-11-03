## Why

Parked entity merges reach an approval dossier that cannot approve them: neither
`memory_entity_merge` nor legacy `entity_merge` has an exact-call preview. The
owner needs to compare the entities, understand which record survives, and resolve
the original request without reconstructing the operation elsewhere.

## What Changes

- Give pending entity-merge approvals a structural review using the existing
  Relationship comparison, with names, types, IDs, shared evidence, divergences,
  and the consequences of absorbing the source into the target.
- Keep the stored source-to-target direction fixed throughout approval. Changing
  the survivor requires a separate explicit operation.
- Allow person/organization merges in either direction. The surviving target
  retains its entity type; different types are information to review, not a veto.
- Confirm through the original approval's exact-call binding and shared executor,
  retaining approval events, merge-review history and durable fleet-rebind receipts.
- Keep merge notifications review-only, even when the dashboard gains a safe
  preview. Telegram opens the review instead of offering direct merge approval.
- Make loading, failure, stale-call and terminal states actionable; preserve Deny,
  Defer, explicit execution recovery, and keyboard/screen-reader access.
- Make retained pending rows reviewable without recreating them or rewriting their
  stored tool name or arguments. Do not replay historical approved actions.

## Capabilities

### New Capabilities

None. This connects existing approvals and entity-comparison capabilities.

### Modified Capabilities

- `dashboard-approvals`: structural review, fixed direction, final confirmation,
  loading/error behavior and pending lifecycle controls for parked merges.
- `module-approvals`: dashboard-only merge review eligibility, original-call
  binding, notification policy, legacy compatibility and execution outcomes.
- `entity-identity`: explicit preservation of the selected target's entity type,
  including cross-type merges already supported by the merge authority.

## Impact

- **Frontend:** `ApprovalsPage`, the shared approval review dialog used by summary
  surfaces, approval hooks/types, and reusable comparison content extracted from
  `MergeCompareDialog`. Existing standalone merge review remains supported.
- **API:** approval detail/preview models and both flat/legacy approval decision
  routes. Comparison stays with the existing owner-authenticated Relationship API.
- **Runtime:** dashboard preview eligibility, notification-card policy and the
  existing Relationship-owned merge handler/executor seam. No second merge engine.
- **Boundary classification:** this changes the review-to-approval HTTP contract
  and channel eligibility at an approval security boundary, so `design.md` is
  required. It adds no domain-to-domain access, grants, authentication factors,
  shared-schema change or external dependency.
- **Rollout:** deploy backend support and frontend review together; an older
  frontend must remain unable to approve a merge without its complete review.
  No bulk data repair or automatic dispatch is part of deployment.

## Out of Scope

- Changing duplicate-candidate matching, filtering candidates by entity type, or
  replacing the existing default survivor-selection heuristic.
- Choosing a different survivor within a parked approval, bulk merges, changing
  the target's type, or introducing a new merge/conflict-resolution algorithm.
- Model-generated comparisons, verdicts, summaries or similarity scores.
- Inline Telegram merge approval, approval-policy relaxation, standing-rule
  creation, new owner authentication or step-up requirements.
- Executing the reported live request, replaying approved legacy rows, repairing
  unknown-origin attribution, or reconciling the entire historical merge backlog.

## Owner Approval

The owner approved the repair proposal on 2026-11-03 and requested the
`th-projects` feature-request workflow. The owner explicitly confirmed that
person/organization merges are acceptable when the target's type survives.
This changeset formalizes that approved scope; application implementation is a
downstream handoff, not part of this specification PR.
