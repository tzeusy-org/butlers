## Why

Owner decision `perception-backup-boundary` = A (2026-11-08) allows private perception
and DND history in the full application-data backup. Today the DND mutation receipts,
generation guard and DND context are omitted, and the ten perception relations of the
`perception-registry` capability would be omitted too, so a restore silently loses
history the system treats as durable. The dump identity cannot read these relations
(they belong to fenced owner roles) and the deployment-hardening spec forbids widening
it, so the staging blocks used for pending confirms and job health cannot reach them.

## What Changes

- Include the three DND populations (all mutation receipts including expired and
  superseded rows, the guard row, `user_context` rows with `signal_type = 'dnd'`) and the
  ten perception relations in the full backup.
- Carry them through fixed `SECURITY DEFINER` export/restore functions, owned by the
  fenced owner and executable only by two inert `NOLOGIN` roles, in the same exported
  snapshot as the dump. The dump identity gains no grant, membership or EXECUTE;
  `dnd_generation_owner` keeps its no-membership fence; runtime grants and forced RLS
  are unchanged.
- Ship an inactive prerequisite: until an authorized export identity exists the backup
  script reports omission, and that artifact does not satisfy full-inclusion acceptance.
  All ten canonical perception sources/exporters remain unavailable; this increment
  does not complete the source or release its human hold. Activation (LOGIN, credential,
  deployment, restore execution) is explicitly out of scope.
- State the retention consequence: the artifact holds this private history for up to
  `BACKUP_RETAIN_DAYS` (default 14 days) beyond live expiry.
- Guard that any relation registered as perception is covered by the export manifest.

## Impact

- `scripts/init-db.sql`, `deploy/backup/pg_dump.sh`,
  `deploy/backup/private_history_manifest.json`, `docs/operations/backup-restore.md`.
- Replaces `bu-7e2acn`'s exact-hash release condition with independent conformance
  review (owner choice recorded on `bu-cew60z`).
