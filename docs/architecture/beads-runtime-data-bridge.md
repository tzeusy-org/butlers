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

The bridge cannot serve a separate runtime host and can pin a replaced file inode. Its planned
multi-host successor, a tracker-host PostgreSQL projection exporter, is owned by
[RFC 0025](../../about/legends-and-lore/rfcs/0025-tracker-host-beads-projection-exporter.md) and
the [`beads-projection-exporter`](../../openspec/changes/beads-projection-exporter/) change.
