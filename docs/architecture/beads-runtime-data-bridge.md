# Beads Runtime Data Bridge

Beads is authoritative in a Dolt server on the tracker host; runtime containers never get network
or credential access to it. The shipped bridge is single-host: bd's own auto-export keeps
`.beads/issues.export.jsonl` fresh, and `docker-compose.yml` bind-mounts only that one file,
read-only, at `/app/.beads/issues.export.jsonl` into `butlers-up` and `dashboard-api` (and their
hot-reload variants). Mounting `.beads/` wholesale is forbidden because it holds tracker
credentials. Two consumers read it: the Switchboard decision digest
(`src/butlers/jobs/decision_review.py`) and `GET /api/beads/{id}` through `BeadSnapshotReader`
(`src/butlers/beads_snapshot.py`), both bounded parsers that expose safe dataclasses only. A
missing, unreadable, oversized, or stale (older than `STALE_BEADS_EXPORT_AGE`) export is reported
as unavailable, never as an empty queue.

The Kubernetes chart ships a second, optional bridge (`beadsExport.enabled`, on in `butlers-dev`
by owner ruling 2026-10-08, off in prod): a CronJob that runs `bd export` against the Dolt server
into a PVC, which `butlers-up` and `dashboard-api` mount read-only as a directory at
`/app/.beads`. The PVC holds only the export file. The CronJob is the sole holder of the Dolt
endpoint and credential, and a NetworkPolicy denies every other pod a route to the tracker, so it
stays outside the runtime trust boundary; see
[Kubernetes deployment](../operations/kubernetes-deployment.md#beads-export).

The same CronJob is the Decision Desk's only tracker writer (`bu-ckkpz.3`,
`REQ-beads-projection-007`, `REQ-owner-decision-desk-002`). The runtime records an owner's choice
as a `switchboard.decision_intents` row (dashboard API or Telegram one-tap); before each export
the CronJob closes the decision bead with `bd`, at most once per intent, and records the outcome
on the row. The runtime still never reaches the tracker.

Both bridges are single-host and cannot serve a separate runtime host, and the host-file bridge
can pin a replaced file inode. Their planned multi-host successor, a tracker-host PostgreSQL
projection exporter, is owned by
[RFC 0025](../../about/legends-and-lore/rfcs/0025-tracker-host-beads-projection-exporter.md) and
the [`beads-projection-exporter`](../../openspec/changes/beads-projection-exporter/) change.
