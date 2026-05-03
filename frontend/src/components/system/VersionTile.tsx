// ---------------------------------------------------------------------------
// VersionTile -- software version and last-deploy timestamp
// (bu-ngfzz.5)
//
// Data source: useInstanceFacts -> GET /api/system/instance
// Fields used: version, started_at
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
import { useInstanceFacts } from "@/hooks/use-system"

// ---------------------------------------------------------------------------
// Loading / error sub-components
// ---------------------------------------------------------------------------

function TileSkeleton() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Version</CardTitle>
        <CardDescription>Software version</CardDescription>
      </CardHeader>
      <CardContent>
        <div data-testid="version-tile-skeleton" className="space-y-2">
          <Skeleton className="h-8 w-32" />
          <Skeleton className="h-4 w-48" />
        </div>
      </CardContent>
    </Card>
  )
}

function TileError() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Version</CardTitle>
        <CardDescription>Software version</CardDescription>
      </CardHeader>
      <CardContent>
        <p data-testid="version-tile-error" className="text-destructive text-sm">
          Could not load version info.
        </p>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// VersionTile
// ---------------------------------------------------------------------------

/**
 * Displays the running software version and the last-deploy timestamp.
 *
 * "Last deploy" is approximated by the process start time reported by the
 * API (/api/system/instance). In production the process restarts on deploy,
 * so started_at is a close proxy.
 */
export function VersionTile() {
  const { data: response, isPending, isError } = useInstanceFacts()

  if (isPending) return <TileSkeleton />
  if (isError) return <TileError />

  const facts = response?.data

  return (
    <Card>
      <CardHeader>
        <CardTitle>Version</CardTitle>
        <CardDescription>Software version</CardDescription>
      </CardHeader>
      <CardContent data-testid="version-tile-content">
        <dl className="space-y-3 text-sm">
          <div>
            <dt className="text-muted-foreground text-xs">Package version</dt>
            <dd className="font-mono text-lg font-semibold tabular-nums">
              {facts?.version || "unknown"}
            </dd>
          </div>
          {facts?.started_at && (
            <div>
              <dt className="text-muted-foreground text-xs">Last deploy</dt>
              <dd>
                <Time value={facts.started_at} mode="relative" />
              </dd>
            </div>
          )}
        </dl>
      </CardContent>
    </Card>
  )
}
