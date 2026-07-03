// ---------------------------------------------------------------------------
// ButlerHeartbeatTile
//
// Shows per-butler heartbeat state: name, last heartbeat (relative), active
// session badge, and a liveness dot.
//
// Canonical liveness (bu-86c4c.17): this tile now consumes the SAME
// activity/tone verdict as the roster board and the /system topology graph
// (useButlerStatusBoard / GET /api/butlers/board), rather than deriving its
// own 5-minute heartbeat-age threshold. Previously the topology graph could
// render a butler green while this tile independently rendered it
// amber-stale from a different threshold with no reconciliation -- both
// surfaces now agree by construction because there is only one computation.
//
// Graceful per-butler error handling: rows with schemaUnreachable=true are
// rendered with a degraded indicator rather than crashing the tile.
//
// Data source: useButlerStatusBoard (refetches every 30 s).
// ---------------------------------------------------------------------------

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { RowLink } from "@/components/ui/RowLink";
import { Time } from "@/components/ui/time";
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import type { StatusBoardRow, ActivityVerb } from "@/hooks/use-butler-status-board";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Liveness dot color, keyed off the canonical activity verb (one meaning everywhere). */
function dotClassFor(activity: ActivityVerb): string {
  switch (activity) {
    case "running":
    case "idle":
      return "bg-severity-low";
    case "overdue":
      return "bg-severity-medium";
    case "offline":
    case "quarantined":
      return "bg-severity-high";
    case "unknown":
      return "bg-muted-foreground/40";
  }
}

function sortByHeartbeat(rows: StatusBoardRow[]): StatusBoardRow[] {
  return [...rows].sort((a, b) => {
    // Null lastHeartbeatISO sorts last (oldest / no heartbeat)
    if (!a.lastHeartbeatISO && !b.lastHeartbeatISO) return 0;
    if (!a.lastHeartbeatISO) return 1;
    if (!b.lastHeartbeatISO) return -1;
    return b.lastHeartbeatISO.localeCompare(a.lastHeartbeatISO);
  });
}

// ---------------------------------------------------------------------------
// Sub-component: single butler row
// ---------------------------------------------------------------------------

interface ButlerRowProps {
  row: StatusBoardRow;
}

function ButlerRow({ row }: ButlerRowProps) {
  return (
    <li className="py-1.5">
      {/* bu-86c4c.4 -- drill-down sweep: the whole row deep-links to the
          affected butler, not the generic /system page it already lives on
          (JARVIS audit finding: "stale heartbeat rows are not links to
          /butlers/:name -- while the same butlers ARE clickable in the graph
          below"). RowLink (bu-86c4c.16) gives a real <a> since this row has
          no nested interactive controls. */}
      <RowLink
        to={`/butlers/${encodeURIComponent(row.name)}`}
        aria-label={`View ${row.name}`}
        className="flex items-center justify-between gap-2 no-underline text-inherit -mx-1 rounded px-1 hover:bg-accent/40"
      >
        <div className="flex min-w-0 flex-col gap-0.5">
          <div className="flex items-center gap-1.5">
            <span
              className={`inline-block size-2 shrink-0 rounded-full ${dotClassFor(row.activity)}`}
              aria-label={`Liveness: ${row.activity}`}
              title={row.activity}
            />
            <span className="truncate text-sm font-medium">{row.name}</span>
            {row.schemaUnreachable && (
              <Badge variant="outline" className="shrink-0 text-xs text-muted-foreground">
                unreachable
              </Badge>
            )}
          </div>
          <div className="pl-3.5 text-xs text-muted-foreground">
            {row.lastHeartbeatISO ? (
              <>
                Last seen{" "}
                <Time value={row.lastHeartbeatISO} mode="relative" />
              </>
            ) : (
              <span>No heartbeat recorded</span>
            )}
          </div>
        </div>
        {row.activeSessionCount > 0 && (
          <Badge variant="secondary" className="shrink-0">
            {row.activeSessionCount} active
          </Badge>
        )}
      </RowLink>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ButlerHeartbeatTile() {
  const { rows, aggregates } = useButlerStatusBoard();
  const { isLoading, isError } = aggregates;

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

  if (isError) {
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

  const sortedRows = sortByHeartbeat(rows);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">Butler Heartbeats</CardTitle>
        <span className="text-xs text-muted-foreground">
          {sortedRows.length} butler{sortedRows.length !== 1 ? "s" : ""}
        </span>
      </CardHeader>
      <CardContent>
        {sortedRows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No butlers registered.</p>
        ) : (
          <ul className="divide-y divide-border">
            {sortedRows.map((row) => (
              <ButlerRow key={row.name} row={row} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
