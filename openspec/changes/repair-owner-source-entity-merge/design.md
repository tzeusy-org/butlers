## Context

The owner index includes all rows, including tombstones. Existing role union is
computed from the deterministically locked source and target, but target assignment
precedes source tombstoning. Canonical bu-2cpmi6 identifies this production conflict
separately from Template8's broader failures.

## Decisions

Move the existing source tombstone update before target assignment and remove only
owner using array_remove. Keep the union computed from the original locked rows.
Other source roles remain on its historical tombstone. No external acquisition can
observe the uncommitted ownerless interval: the same transaction protects roles,
facts, references, audit and receipt creation. Any later exception restores the
original committed source owner.

Preserve pair-lock ordering, locked guard, fact planning, temporal collision
refusal, role deduplication, target metadata precedence, references, receipts and
post-commit events. No migration, constraint, membership, grant or caller changes.

The existing owner merge node provisions current core, memory and Relationship
chains through the published create_migrated_test_db helper. Relationship is
schema-qualified; memory retains the helper's documented public schema default.
Bootstrap-created runtime identities are selected on every pool acquisition.
This is bounded owner-source proof, not a full fixture/topology conversion or
Template8 qualification.

## Verification and administration

The existing software guard node positions owner release before target assignment.
Its old-source RED is distinct from real PostgreSQL evidence. The existing PG owner
node neutralizes only early owner release to reach the actual singleton violation,
restores it, proves a late failure after roles and a planted contact reference move,
then reads back outside the transaction. It preserves all four original assertions,
source non-owner roles, target-owner and empty-role companions, and a General runtime
refusal.

Local Docker-backed SQL is unavailable in this dispatch. Genuine current hosted PG
execution and independent completion-scope review remain mandatory preparation.
Only then may ordinary validated disposable sync/archive apply this full preserving
delta; no incomplete-task override, skip-specs or validation bypass. Fresh final-head
normal, nonauthor/protected review and actual squash remain mandatory administrative
delivery after archive. Source preparation does not close the original issue.

## Rollback

Before commit, the normal transaction restores all effects. Code recovery restores
this branch's parent with unrelated worktrees and Template8's sealed checkpoint
untouched. The existing owner index and all applied migration bytes are preserved.
