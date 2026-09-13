## Why

The System page currently reports several true but separate facts about backup
artifacts, backup runs, and restore drills, while the remaining recovery work is
split across two active OpenSpec changes and several Beads. The owner still has
no single, bounded answer to whether recovery was proven without confusing a
present file, a healthy gzip stream, or a historical drill with a recoverable
deployment.

## What Changes

- Define one composed recovery-proof contract above the existing backup-run,
  artifact-health, protected restore-drill, and filtered-event verification
  contracts. It does not create a second result authority.
- Require backup coverage to remain complete for recoverable application data,
  including credential-bearing rows, and closed under schema-qualified foreign
  keys in the exact exported `pg_dump` snapshot. Every omission must be
  reconstructible trusted-bootstrap state with a documented loss statement.
- Preserve ownership and ACL intent in the recovery artifact and require the
  same protected attempt to run managed bootstrap and complete ownership/ACL
  verification in a disposable target before a restore is recovery-ready.
- Keep the proposed, unapproved filtered-event manifest limited to its named
  scope. It becomes a prerequisite only after separate exact-artifact review
  and owner adoption; neither it nor a run receipt can replace direct,
  memoized artifact-integrity checks or manufacture a restore pass.
- Add an additive, content-blind `recovery_proof` projection to
  `GET /api/system/backups` and a matching System-page presentation. Artifact
  health, last-run outcome, and recovery proof remain visibly distinct.
- Reuse the existing protected restore ledger and truthful restore-drill
  attention contract. Production evidence remains separately gated by
  `bu-e1410` and deferred owner/operations task `bu-lw18o`.
- Renumber the colliding active filtered-event authority requirement to
  `REQ-database-security-011` and qualify cross-change sources so trace identity
  cannot resolve to the unrelated owner-operations overlay.
- Require the future owner decision to name this umbrella and the exact
  `artifact-bound-filtered-event-restore-verification` artifact if both are to
  govern implementation. Review, CI, PR state, or merge adopts neither.

## Capabilities

### New Capabilities

- `backup-recovery-truth`: Composes complete backup coverage, bootstrap and
  ownership integrity, exact-artifact restore evidence, truthful owner-facing
  status, content blindness, and operational authority gates.

### Modified Capabilities

None. The change deliberately adds one composition capability instead of
creating concurrent whole-requirement replacements for active
`restore-drill-recovery-truthfulness` or
`artifact-bound-filtered-event-restore-verification` deltas.

## Impact

Future implementation is expected to reconcile `deploy/backup/pg_dump.sh`,
`docs/operations/backup-restore.md`, the protected restore-drill executor and
ledger, `GET /api/system/backups`, and the System-page Backups tile. Existing
work in `bu-kqnum.8.4` through `bu-kqnum.8.7` remains the owner of result-aware
retry, attention, API/UI failure age, real-PostgreSQL proof, and terminal
reconciliation. This planning change performs no dump, restore, schema change,
credential access, runtime action, deployment, or production inspection.
The bounded ID correction also updates the active artifact-bound filtered-event
spec and its testing reference; it changes no runtime behavior or authority.
