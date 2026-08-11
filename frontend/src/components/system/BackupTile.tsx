// ---------------------------------------------------------------------------
// BackupTile -- backup recency, verified artifact health, and restore-drill
// state (bu-ngfzz.6, deepened by bu-9r3hd.5)
//
// Data source: useBackupFacts -> GET /api/system/backups
// Fields used: backup_source_reachable, last_backup_at, last_backup_status,
// backup_stale, restore_drill
//
// bu-9r3hd.5: the "Reachable" badge used to render green purely from
// backup_source_reachable, regardless of whether the backup artifact was
// ever actually verified -- a fabricated all-clear. The badge now reflects
// the REAL verified state: corrupt/empty artifact or a stale backup renders
// as a problem, and a failed restore drill (the strongest possible check --
// an actual restore attempt) is surfaced as its own row, never silently
// folded into a green badge.
// ---------------------------------------------------------------------------

import type { BackupFacts, RestoreDrillFacts } from "@/api/types"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Time } from "@/components/ui/time"
import { useBackupFacts } from "@/hooks/use-system"

// ---------------------------------------------------------------------------
// Loading / error sub-components
// ---------------------------------------------------------------------------

function TileSkeleton() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Backups</CardTitle>
        <CardDescription>Backup recency and reachability</CardDescription>
      </CardHeader>
      <CardContent>
        <div data-testid="backup-tile-skeleton" className="space-y-2">
          <Skeleton className="h-8 w-40" />
          <Skeleton className="h-4 w-52" />
        </div>
      </CardContent>
    </Card>
  )
}

function TileError() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Backups</CardTitle>
        <CardDescription>Backup recency and reachability</CardDescription>
      </CardHeader>
      <CardContent>
        <p data-testid="backup-tile-error" className="text-destructive text-sm">
          Could not load backup facts.
        </p>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// BackupTile
// ---------------------------------------------------------------------------

/**
 * Real, verified status badge -- never defaults to green. Absent fields (an
 * older backend, or a fixture predating bu-9r3hd.5) render as "Unverified"
 * rather than silently assuming health. Tailwind classes are static per
 * branch (not built from a dynamic string) so the JIT compiler can see them.
 */
function BackupStatusBadge({ facts }: { facts: BackupFacts }) {
  if (facts.last_backup_status === "corrupt" || facts.last_backup_status === "empty") {
    return (
      <span
        data-testid="backup-tile-status-badge"
        className="bg-[var(--red)] text-white inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
      >
        {facts.last_backup_status === "corrupt" ? "Corrupt" : "Empty"}
      </span>
    )
  }
  if (facts.backup_stale) {
    return (
      <span
        data-testid="backup-tile-status-badge"
        className="bg-[var(--amber)] text-white inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
      >
        Stale
      </span>
    )
  }
  if (facts.last_backup_status === "healthy") {
    return (
      <span
        data-testid="backup-tile-status-badge"
        className="bg-[var(--green)] text-white inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
      >
        Healthy
      </span>
    )
  }
  return (
    <span
      data-testid="backup-tile-status-badge"
      className="bg-[var(--amber)] text-white inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
    >
      Unverified
    </span>
  )
}

const RESTORE_DRILL_LEDGER_UNAVAILABLE_DETAIL = "restore drill ledger unavailable"

function RestoreDrillRow({ drill }: { drill: RestoreDrillFacts | undefined }) {
  if (!drill || drill.result === "pending") {
    return (
      <div>
        <dt className="text-muted-foreground text-xs">Restore drill</dt>
        <dd data-testid="backup-tile-drill-pending" className="text-muted-foreground text-sm">
          No drill yet
        </dd>
      </div>
    )
  }

  if (drill.result === "pass") {
    return (
      <div>
        <dt className="text-muted-foreground text-xs">Restore drill</dt>
        <dd data-testid="backup-tile-drill-pass" className="text-sm">
          <span className="text-[var(--green)]">Passed</span>
          {drill.checked_at ? (
            <>
              {" "}
              <Time value={drill.checked_at} mode="relative" />
            </>
          ) : null}
        </dd>
      </div>
    )
  }

  // "fail" or "degraded" -- both are real problems, never silently dropped.
  // A degraded value originates in an exception boundary. Keep a second UI
  // guard here because this field is rendered directly and a malformed API
  // response must not turn a database exception into dashboard-visible text.
  const problemDetail =
    drill.result === "degraded" ? RESTORE_DRILL_LEDGER_UNAVAILABLE_DETAIL : drill.detail
  return (
    <div>
      <dt className="text-muted-foreground text-xs">Restore drill</dt>
      <dd data-testid="backup-tile-drill-problem" className="text-sm">
        <span className="text-[var(--red-text)]">
          {drill.result === "fail" ? "Failed" : "Unavailable"}
        </span>
        {problemDetail ? <span className="text-muted-foreground"> -- {problemDetail}</span> : null}
      </dd>
    </div>
  )
}

/**
 * Displays backup recency, a verified artifact-health badge, and the most
 * recent restore-drill result.
 *
 * When the backup source is unreachable (or not yet configured), renders a
 * graceful unavailable notice rather than an error state. The endpoint always
 * returns HTTP 200 -- an unreachable source is a known deployment state, not
 * a failure.
 */
export function BackupTile() {
  const { data: response, isPending, isError } = useBackupFacts()

  if (isPending) return <TileSkeleton />
  if (isError) return <TileError />

  const facts = response?.data

  if (!facts?.backup_source_reachable) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Backups</CardTitle>
          <CardDescription>Backup recency and reachability</CardDescription>
        </CardHeader>
        <CardContent data-testid="backup-tile-unavailable">
          <p className="text-muted-foreground text-sm">Backup status unavailable.</p>
          <p className="text-muted-foreground mt-1 text-xs">
            Backup source is unreachable or not configured.
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Backups</CardTitle>
        <CardDescription>Backup recency and reachability</CardDescription>
      </CardHeader>
      <CardContent data-testid="backup-tile-content">
        <dl className="space-y-3 text-sm">
          <div>
            <dt className="text-muted-foreground text-xs">Status</dt>
            <dd>
              <BackupStatusBadge facts={facts} />
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">Last backup</dt>
            <dd>
              {facts.last_backup_at ? (
                <Time value={facts.last_backup_at} mode="relative" />
              ) : (
                <span className="text-muted-foreground text-sm">Never run</span>
              )}
            </dd>
          </div>
          <RestoreDrillRow drill={facts.restore_drill} />
        </dl>
      </CardContent>
    </Card>
  )
}
