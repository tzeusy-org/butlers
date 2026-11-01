# RFC 0025: Tracker-Host Beads Projection Exporter

**Status:** Withdrawn by owner decision bu-g2zuv2 = A (2026-10-21)

**Date:** 2026-08-13

The former projection and development tracker write bridge are retired from the
personal-assistant product under bu-99t5s3.6. Their original design remains in
Git history. This RFC no longer authorizes implementation, activation, exporter
installation, tracker credentials, runtime readers or decision application.
The pending exporter and write-bridge OpenSpec changes are withdrawn.

The dashboard pages, API operations, snapshot parser, jobs, schedules, Telegram
decision transport, Helm bridge and Compose export plumbing are removed in
source. Engineering decisions stay in local `bd` or an owner session; the
standalone decision-bead linter and Dolt workflow remain supported.

The effective runtime-to-tracker deny remains mandatory. Bridge retirement
must not grant runtime tracker connectivity or credentials, and does not prove
live network confinement or authorize a production rollout.

Existing intent and prompt tables remain. Owner approval includes eventual
irreversible loss of that history, but does not waive deployment ordering:
first deploy the code removal and stop every old reader and writer, then review
and execute a separate drop migration. No DDL or deployment is part of the
source retirement. A rollback before that later migration may restore old
code; it does not promise data recovery after the approved destructive step.

See [the current retirement boundary](../../../docs/architecture/beads-runtime-data-bridge.md),
[tracker isolation](../../../docs/operations/kubernetes-deployment.md#engineering-tracker-isolation),
and [the retained engineering convention](../../../docs/operations/decision-beads.md).
