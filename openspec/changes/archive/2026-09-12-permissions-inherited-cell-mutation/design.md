## Context

`GET /api/permissions` intentionally builds a dense matrix: a missing database
row is an inherited system default, while an existing row is an operator-set
override. The dashboard already has a reason modal and the mutation route
already uses an UPSERT, but the matrix disables inherited buttons. As a result,
the owner cannot create the first explicit revoke or grant. The same writable
route accepts arbitrary path vocabulary even though the matrix itself exposes
only `ENFORCED_PERMISSIONS` and registered butlers.

This is a permissions control-plane change. It must preserve the required
reason and audit transaction, remain keyboard-operable, and avoid inventing a
second permissions data model or a migration.

## Goals / Non-Goals

**Goals:**

- Let an operator use the existing modal and UPSERT for an inherited default.
- Keep inherited cells visibly dim until a first submitted override
  optimistically changes them to explicit foreground state.
- Reject unknown permissions and unregistered butlers before any permissions or
  audit write.
- Cover the browser behavior, API guards, and OpenSpec contract with focused
  regressions.

**Non-Goals:**

- Changing defaults, the enforced-permission set, butler registration, or the
  permissions table schema.
- Adding a new settings layout, permission role model, or deletion workflow.
- Making a failed executed action retryable or changing approval semantics.

## Decisions

### D1 — An inherited cell is an editable default, not a disabled control

The matrix will retain its `inherited` label and dim styling but remove the
disabled state. Its native button remains keyboard-operable and opens the
existing reason-required modal. On confirmation, local matrix state immediately
becomes an explicit cell (`inherited: false`) with the requested grant value;
a failed PUT restores the prior inherited cell and keeps the modal usable.

- Rejected: a separate "make explicit" interaction. It adds another control
  without improving the audited grant/revoke flow already present.
- Rejected: keeping inherited cells disabled. It contradicts the current
  capability contract's first-explicit-mutation scenario.

### D2 — Validate mutation vocabulary at the authoritative boundary

The PUT route checks `perm` against `ENFORCED_PERMISSIONS` and checks `butler`
against the live `butler_registry` before opening its UPSERT/audit write.
Unknown values return the existing validation-style 422 envelope with stable
errors `permission_not_enforced` or `butler_not_registered`; no row, audit
entry, or webhook dispatch occurs.

- Rejected: relying on the frontend's matrix rows. Direct clients can call the
  public API and must not create decorative state.
- Rejected: a new database constraint/migration. The registry and enforced
  tuple are the established live authorities, and this slice only prevents new
  invalid writes.

## Risks / Trade-offs

- **[Optimistic state diverges after a failed request]** → retain the exact
  prior cell and restore it on failure before showing the mutation error.
- **[Registry lookup is unavailable]** → do not write; the existing pool error
  path remains explicit instead of falling back to arbitrary values.
- **[Dim inherited cells look unavailable]** → preserve the inherited label but
  add normal interactive hover/focus affordances and test keyboard-native button
  semantics.

## Migration Plan

No migration or backfill is required. Existing inherited cells become editable
on frontend deployment; their first mutation uses the existing UPSERT and audit
transaction. Rollback returns the UI to its prior dim inherited state while
retaining the valid explicit rows already written, which the current matrix
already understands.

## Open Questions

None. The existing `ENFORCED_PERMISSIONS`, live registry, reason modal, and
UPSERT establish the needed authorities and write path.
