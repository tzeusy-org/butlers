// /system keeps board activity and explicit tick behavior separate from the
// existing heartbeat endpoint's server-authoritative control-plane facts.
// REQ-system-overview-page-007's adopted liveness successor forbids treating
// board activity, timestamp age or a probe attempt as service availability.
// Independent query snapshots are joined by exact name, not as one transaction.

import { toast } from "sonner";
import { useNavigate } from "react-router";

import { Tile, TileContent, TileHeader, TileTitle } from "@/components/ui/Tile";
import { StateDot } from "@/components/ui/StateDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RowLink } from "@/components/ui/RowLink";
import { Time } from "@/components/ui/time";
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import { useButlerHeartbeats } from "@/hooks/use-system";
import type { ButlerHeartbeat } from "@/api/types";
import { useForceButlerTick } from "@/hooks/use-butlers";
import type { StatusBoardRow, ActivityVerb } from "@/hooks/use-butler-status-board";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Observation alone owns this dot; policy and eligibility remain separate facts. */
function observationDotState(observation: string): "ok" | "degraded" | "error" | "waiting" {
  switch (observation) {
    case "healthy": return "ok";
    case "stale": return "degraded";
    case "unavailable": return "error";
    default: return "waiting";
  }
}

function fact(value: unknown, vocabulary: readonly string[]): string {
  return typeof value === "string" && vocabulary.includes(value)
    ? value.replaceAll("_", " ") : "unknown";
}

type HeartbeatRow = Pick<StatusBoardRow,
  "name" | "activity" | "activeSessionCount" | "schemaUnreachable" | "lastHeartbeatISO"
> & { facts?: ButlerHeartbeat };

/** Rows whose scheduler being silent is itself the problem -- the "Trigger tick" remedy applies. */
function isStale(activity: ActivityVerb): boolean {
  return activity === "overdue" || activity === "offline";
}

function sortByHeartbeat(rows: HeartbeatRow[]): HeartbeatRow[] {
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
  row: HeartbeatRow;
  factsUnavailable: boolean;
  onTriggerTick: (name: string) => void;
  isTicking: boolean;
}

function ButlerRow({ row, factsUnavailable, onTriggerTick, isTicking }: ButlerRowProps) {
  const navigate = useNavigate();
  const detailPath = `/butlers/${encodeURIComponent(row.name)}`;
  const showTickRemedy = isStale(row.activity);
  const observation = factsUnavailable ? "unknown" : fact(row.facts?.observation_state,
    ["healthy", "stale", "unavailable", "observer_unknown"]);
  const policy = fact(row.facts?.administrative_policy,
    ["active", "paused", "quarantined", "review_required", "unknown"]);
  const compatibility = fact(row.facts?.route_compatibility, ["compatible", "incompatible", "unknown"]);
  const eligibility = fact(row.facts?.effective_eligibility, ["eligible", "paused", "ineligible", "unknown"]);
  const lastHeartbeat = row.facts ? row.facts.last_heartbeat_at : row.lastHeartbeatISO;

  return (
    <li className="py-1.5">
      {/* bu-86c4c.4 -- drill-down sweep: the whole row deep-links to the
          affected butler, not the generic /system page it already lives on
          (JARVIS audit finding: "stale heartbeat rows are not links to
          /butlers/:name -- while the same butlers ARE clickable in the graph
          below"). RowLink (bu-86c4c.16) gives a real <a> since this row has
          no nested interactive controls -- except stale rows, which nest the
          "Trigger tick" button (bu-86c4c.15) and so switch to the accessible
          role="link" fallback with imperative navigation. */}
      <RowLink
        to={detailPath}
        hasNestedInteractive={showTickRemedy}
        onActivate={() => navigate(detailPath)}
        aria-label={`View ${row.name}`}
        className="flex items-center justify-between gap-2 no-underline text-inherit -mx-1 rounded px-1 hover:bg-accent/40"
      >
        <div className="flex min-w-0 flex-col gap-0.5">
          <div className="flex items-center gap-1.5">
            <StateDot state={observationDotState(observation)} size={8}
              aria-hidden="true" title={observation} />
            <span className="truncate text-sm font-medium">{row.name}</span>
            {(row.schemaUnreachable || row.facts?.error === "schema_unreachable") && (
              <Badge variant="outline" className="shrink-0 text-xs text-muted-foreground">
                unreachable
              </Badge>
            )}
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 pl-3.5 text-xs sm:grid-cols-4">
            {[["Observation", observation], ["Administrative policy", policy],
              ["Service compatibility", compatibility], ["Service eligibility", eligibility]].map(([label, value]) => (
              <div key={label}>
                <dt className="text-muted-foreground">{label}</dt>
                <dd aria-label={`${label}: ${value}`}>{value}</dd>
              </div>
            ))}
          </dl>
          <div className="flex flex-wrap gap-x-3 gap-y-1 pl-3.5 text-xs text-muted-foreground">
            <span>Activity: {row.activity}</span>
            <span>{lastHeartbeat ? <>Last recorded heartbeat <Time value={lastHeartbeat} mode="relative" /></> : "No heartbeat recorded"}</span>
            <span>{row.facts?.last_probe_at ? <>Last probe attempt <Time value={row.facts.last_probe_at} mode="relative" /></> : "No probe recorded"}</span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {row.activeSessionCount > 0 && (
            <Badge variant="secondary" className="shrink-0">
              {row.activeSessionCount} active
            </Badge>
          )}
          {showTickRemedy && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-1.5 text-xs text-muted-foreground"
              disabled={isTicking}
              onClick={(e) => {
                // Nested inside RowLink's role="link" container -- stop the
                // click from also bubbling into onActivate's navigate().
                e.stopPropagation();
                onTriggerTick(row.name);
              }}
            >
              {isTicking ? "Triggering…" : "Trigger tick"}
            </Button>
          )}
        </div>
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
  const heartbeat = useButlerHeartbeats();
  const heartbeatRows = heartbeat.data?.data?.butlers;
  const byName = new Map(heartbeatRows?.map((entry) => [entry.name, entry]));
  const combined: HeartbeatRow[] = rows.map((row) => ({ ...row, facts: byName.get(row.name) }));
  const boardNames = new Set(rows.map((row) => row.name));
  if (heartbeatRows) {
    for (const entry of heartbeatRows) {
      if (!boardNames.has(entry.name)) combined.push({ name: entry.name, activity: "unknown",
        activeSessionCount: 0, schemaUnreachable: entry.error === "schema_unreachable",
        lastHeartbeatISO: entry.last_heartbeat_at, facts: entry });
    }
  }
  const forceTick = useForceButlerTick();

  function handleTriggerTick(name: string) {
    forceTick.mutate(name, {
      onSuccess: (response) => {
        if (response.data.success) {
          toast.success(
            response.data.message ? `${name}: ${response.data.message}` : `${name}: tick triggered`,
          );
        } else {
          toast.error(`${name}: tick did not complete successfully`);
        }
      },
      onError: (err) => {
        toast.error(`Failed to trigger tick for ${name}`, {
          description: err instanceof Error ? err.message : undefined,
        });
      },
    });
  }

  if (heartbeat.isPending && combined.length === 0) {
    return (
      <Tile loading>
        <TileHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <TileTitle>Butler Heartbeats</TileTitle>
        </TileHeader>
        <TileContent>
          <div className="h-16 rounded bg-muted" data-testid="butler-heartbeat-skeleton" />
        </TileContent>
      </Tile>
    );
  }

  const sortedRows = sortByHeartbeat(combined);
  const tickingName = forceTick.isPending ? forceTick.variables : undefined;

  return (
    <Tile degraded={isError || heartbeat.isError}>
      <TileHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <TileTitle>Butler Heartbeats</TileTitle>
        {(sortedRows.length > 0 || (!isLoading && !isError && !heartbeat.isPending && !heartbeat.isError)) &&
          <span className="text-xs text-muted-foreground">{sortedRows.length} butler{sortedRows.length !== 1 ? "s" : ""}</span>}
      </TileHeader>
      <TileContent>
        {heartbeat.isPending && <p role="status" className="text-xs text-muted-foreground">Loading heartbeat facts…</p>}
        {heartbeat.isError && <p role="status" className="text-sm text-destructive">Failed to load heartbeat data. {heartbeat.data ? "Last-known facts; current observation unknown." : "Heartbeat facts unavailable."}</p>}
        {!heartbeat.isPending && !heartbeat.isError && (!heartbeatRows || heartbeatRows.length === 0) && <p className="text-xs text-muted-foreground">No heartbeat facts recorded.</p>}
        {heartbeat.isFetching && !heartbeat.isPending && <p role="status" className="text-xs text-muted-foreground">Updating heartbeat facts…</p>}
        {isError && <p className="text-xs text-muted-foreground">Board activity unavailable.</p>}
        {sortedRows.length === 0 ? (
          !isError && !heartbeat.isError && !heartbeat.isPending && !isLoading
            ? <p className="text-sm text-muted-foreground">No butlers registered.</p> : null
        ) : (
          <ul className="divide-y divide-border">
            {sortedRows.map((row) => (
              <ButlerRow
                key={row.name}
                row={row}
                factsUnavailable={heartbeat.isError || heartbeat.isPending}
                onTriggerTick={handleTriggerTick}
                isTicking={tickingName === row.name}
              />
            ))}
          </ul>
        )}
      </TileContent>
    </Tile>
  );
}
