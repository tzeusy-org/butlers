// ---------------------------------------------------------------------------
// VersionTile -- software version and last-deploy timestamp
// (bu-ngfzz.5)
//
// Data source: useInstanceFacts -> GET /api/system/instance
// Fields used: version, started_at
// ---------------------------------------------------------------------------

import {
  Tile,
  TileContent,
  TileDescription,
  TileHeader,
  TileTitle,
} from "@/components/ui/Tile"
import { Skeleton } from "@/components/ui/skeleton"
import { Time } from "@/components/ui/time"
import { useInstanceFacts } from "@/hooks/use-system"

// ---------------------------------------------------------------------------
// Loading / error sub-components
// ---------------------------------------------------------------------------

function TileSkeleton() {
  return (
    <Tile loading>
      <TileHeader>
        <TileTitle>Version</TileTitle>
        <TileDescription>Software version</TileDescription>
      </TileHeader>
      <TileContent>
        <div data-testid="version-tile-skeleton" className="space-y-2">
          <Skeleton className="h-8 w-32" />
          <Skeleton className="h-4 w-48" />
        </div>
      </TileContent>
    </Tile>
  )
}

function TileError() {
  return (
    <Tile degraded>
      <TileHeader>
        <TileTitle>Version</TileTitle>
        <TileDescription>Software version</TileDescription>
      </TileHeader>
      <TileContent>
        <p data-testid="version-tile-error" className="text-destructive text-sm">
          Could not load version info.
        </p>
      </TileContent>
    </Tile>
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
    <Tile>
      <TileHeader>
        <TileTitle>Version</TileTitle>
        <TileDescription>Software version</TileDescription>
      </TileHeader>
      <TileContent data-testid="version-tile-content">
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
      </TileContent>
    </Tile>
  )
}
