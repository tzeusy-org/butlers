## Why

Runtime fail-closed SET ROLE was delivered by bu-dss7he.2, but a shared connecting identity still owns objects, dashboard pools have no role/reach boundary, bootstrap re-widens public grants, and runtime startup still runs migrations. A complete authority model must remove those ownership escape paths while retaining DND, protected outbox/restore, current consumers and adopted SQL exceptions.

## What Changes

- **BREAKING**: separate migrator, runtime and dashboard credential profiles. Only the CLI migrator upgrades; serving startup verifies migrations read-only.
- Generate roster role identities and one exact object/operation manifest; deny unknown objects, converge effective ACLs and validate every reachable role and owner.
- Install purpose-owned final-state definers through Alembic, preserve canonical DND and restricted cross-schema exceptions, and replace obsolete admin/version mechanisms only with executing security survivors.
- Replace bootstrap-body text drift with truthful current catalog diagnostics. Keep owner-auth and the held runtime-probe key plane separate.
- Define operator-only login/secret/ownership transfer and explicit rollback, with full fresh/existing/concurrent/real-role proof requirements.

## Capabilities

### New Capabilities

None: the authority model extends existing database, daemon and dashboard contracts.

### Modified Capabilities

- `database-security`: exact grant/ownership/reach/definer/manifest and cutover model.
- `core-daemon`: read-only required-migration admission before serving.
- `dashboard-api`: constrained manager pools and current catalog facts.

## Impact

This draft is grounded at public `0cf196b56833602894f56bb0caad87d09ee374b1`. It is the complete normative/operator source increment for released bu-dss7he.6, not delivery of its runtime rebuild. The 3705 full implementation allocation and original AC/slice matrix remain controlling; no small delivered .2 slice substitutes for completion. Runtime files, migrations, slots, secrets, live databases, deployment activation and feature tests are unchanged here.

## Authority and exclusions

The recorded 2026-10-20 owner release governs this original engineering scope, including supersession of the public-write audit posture. Ready3743 independently found the corrected full12 contract conforming; ROOT3745 recorded its exact within-release authority and authorized canonical/RFC0006/vision/active-DND application and archive on this same branch. The resulting application still needs DIFFERENT conformance. This is governing source preparation, not a runtime rebuild or a separate preliminary merge prerequisite. No generic extra owner gate is invented for ordinary role/manifest engineering. Live login/secret and privileged ownership transfer remain owner acts. Runtime-probe key-plane rehome remains Held until the task actually completes. No per-butler process split, owner-auth login change, Google deployment consolidation, retired Decision Desk service, new domain/capability grant or .11 migration-baseline substitution is included.
