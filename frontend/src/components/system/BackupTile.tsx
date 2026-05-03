// ---------------------------------------------------------------------------
// BackupTile -- backup recency and source reachability
// (bu-ngfzz.6)
//
// Data source: useBackupFacts -> GET /api/system/backups
// Fields used: backup_source_reachable, last_backup_at
// ---------------------------------------------------------------------------

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
 * Displays backup source reachability and the most recent backup timestamp.
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
              <span
                data-testid="backup-tile-reachable-badge"
                className="bg-emerald-600 text-white inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
              >
                Reachable
              </span>
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
        </dl>
      </CardContent>
    </Card>
  )
}
