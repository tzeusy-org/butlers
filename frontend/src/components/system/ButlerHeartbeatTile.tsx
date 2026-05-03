// ---------------------------------------------------------------------------
// ButlerHeartbeatTile
//
// Shows per-butler heartbeat state: name, last_seen_at (relative), active
// session badge, and a stale-heartbeat warning indicator.
//
// Stale threshold: 5 minutes (STALE_THRESHOLD_SECONDS). Butlers with no
// heartbeat recorded are also flagged as stale.
//
// Graceful per-butler error handling: entries with error="schema_unreachable"
// are rendered with a degraded indicator rather than crashing the tile.
//
// Data source: useButlerHeartbeats (refetches every 30 s).
// ---------------------------------------------------------------------------

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Time } from "@/components/ui/time";
import { useButlerHeartbeats } from "@/hooks/use-system";
import type { ButlerHeartbeat } from "@/api/types";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Heartbeats older than this are flagged as stale. */
const STALE_THRESHOLD_SECONDS = 5 * 60;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isStale(butler: ButlerHeartbeat): boolean {
  if (butler.heartbeat_age_seconds === null || butler.heartbeat_age_seconds === undefined) {
    return true;
  }
  return butler.heartbeat_age_seconds > STALE_THRESHOLD_SECONDS;
}

function sortByHeartbeat(butlers: ButlerHeartbeat[]): ButlerHeartbeat[] {
  return [...butlers].sort((a, b) => {
    // Null last_heartbeat_at sorts last (oldest / no heartbeat)
    if (!a.last_heartbeat_at && !b.last_heartbeat_at) return 0;
    if (!a.last_heartbeat_at) return 1;
    if (!b.last_heartbeat_at) return -1;
    return b.last_heartbeat_at.localeCompare(a.last_heartbeat_at);
  });
}

// ---------------------------------------------------------------------------
// Sub-component: single butler row
// ---------------------------------------------------------------------------

interface ButlerRowProps {
  butler: ButlerHeartbeat;
}

function ButlerRow({ butler }: ButlerRowProps) {
  const stale = isStale(butler);
  const unreachable = butler.error === "schema_unreachable";

  return (
    <li className="flex items-center justify-between gap-2 py-1.5">
      <div className="flex min-w-0 flex-col gap-0.5">
        <div className="flex items-center gap-1.5">
          {stale && (
            <span
              className="inline-block size-2 shrink-0 rounded-full bg-severity-medium"
              aria-label="Stale heartbeat"
              title={
                butler.last_heartbeat_at
                  ? "No heartbeat in the last 5 minutes"
                  : "No heartbeat recorded"
              }
            />
          )}
          {!stale && (
            <span
              className="inline-block size-2 shrink-0 rounded-full bg-severity-low"
              aria-label="Healthy heartbeat"
            />
          )}
          <span className="truncate text-sm font-medium">{butler.name}</span>
          {unreachable && (
            <Badge variant="outline" className="shrink-0 text-xs text-muted-foreground">
              unreachable
            </Badge>
          )}
        </div>
        <div className="pl-3.5 text-xs text-muted-foreground">
          {butler.last_heartbeat_at ? (
            <>
              Last seen{" "}
              <Time value={butler.last_heartbeat_at} mode="relative" />
            </>
          ) : (
            <span>No heartbeat recorded</span>
          )}
        </div>
      </div>
      {butler.active_session_count > 0 && (
        <Badge variant="secondary" className="shrink-0">
          {butler.active_session_count} active
        </Badge>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ButlerHeartbeatTile() {
  const { data, isLoading, error } = useButlerHeartbeats();

  if (isLoading) {
    return (
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Butler Heartbeats</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-16 animate-pulse rounded bg-muted" />
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Butler Heartbeats</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-destructive">Failed to load heartbeat data.</p>
        </CardContent>
      </Card>
    );
  }

  const butlers = sortByHeartbeat(data?.data.butlers ?? []);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">Butler Heartbeats</CardTitle>
        <span className="text-xs text-muted-foreground">
          {butlers.length} butler{butlers.length !== 1 ? "s" : ""}
        </span>
      </CardHeader>
      <CardContent>
        {butlers.length === 0 ? (
          <p className="text-sm text-muted-foreground">No butlers registered.</p>
        ) : (
          <ul className="divide-y divide-border">
            {butlers.map((butler) => (
              <ButlerRow key={butler.name} butler={butler} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
