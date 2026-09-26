// ---------------------------------------------------------------------------
// UptimeTile -- process uptime (started_at relative + d/h/m breakdown)
// (bu-ngfzz.5)
//
// Data source: useInstanceFacts -> GET /api/system/instance
// Fields used: started_at, uptime_seconds
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
import { formatUptimeParts } from "./uptime-utils"

// ---------------------------------------------------------------------------
// Loading / error sub-components
// ---------------------------------------------------------------------------

function TileSkeleton() {
  return (
    <Tile>
      <TileHeader>
        <TileTitle>Uptime</TileTitle>
        <TileDescription>Process uptime</TileDescription>
      </TileHeader>
      <TileContent>
        <div data-testid="uptime-tile-skeleton" className="space-y-2">
          <Skeleton className="h-8 w-28" />
          <Skeleton className="h-4 w-44" />
        </div>
      </TileContent>
    </Tile>
  )
}

function TileError() {
  return (
    <Tile>
      <TileHeader>
        <TileTitle>Uptime</TileTitle>
        <TileDescription>Process uptime</TileDescription>
      </TileHeader>
      <TileContent>
        <p data-testid="uptime-tile-error" className="text-destructive text-sm">
          Could not load uptime info.
        </p>
      </TileContent>
    </Tile>
  )
}

// ---------------------------------------------------------------------------
// UptimeTile
// ---------------------------------------------------------------------------

/**
 * Displays the process start time (relative) and a d/h/m uptime breakdown.
 */
export function UptimeTile() {
  const { data: response, isPending, isError } = useInstanceFacts()

  if (isPending) return <TileSkeleton />
  if (isError) return <TileError />

  const facts = response?.data

  return (
    <Tile>
      <TileHeader>
        <TileTitle>Uptime</TileTitle>
        <TileDescription>Process uptime</TileDescription>
      </TileHeader>
      <TileContent data-testid="uptime-tile-content">
        <dl className="space-y-3 text-sm">
          <div>
            <dt className="text-muted-foreground text-xs">Running for</dt>
            <dd className="text-lg font-semibold tabular-nums">
              {facts != null ? formatUptimeParts(facts.uptime_seconds) : "--"}
            </dd>
          </div>
          {facts?.started_at && (
            <div>
              <dt className="text-muted-foreground text-xs">Started</dt>
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
