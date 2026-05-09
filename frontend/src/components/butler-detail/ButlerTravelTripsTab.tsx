/**
 * ButlerTravelTripsTab
 *
 * Wires the travel butler's trips and upcoming-travel endpoints to the Trips
 * bespoke tab on the travel butler detail page. Consumes existing hooks only —
 * no new HTTP routes are added.
 *
 * Four sections:
 *  1. KPI strip        — next departure, active count, planned count, open actions.
 *  2. Pre-trip actions — urgency-ranked action list (high / medium / low severity).
 *  3. Trip roster      — paginated card list; row-click opens detail drawer.
 *  4. Trip detail      — drawer with full trip timeline on row-click.
 *
 * bead: bu-0eac9
 */

import { useState } from "react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useUpcomingTravel, useTravelTrips, useTravelTripSummary } from "@/hooks/use-travel";
import type {
  TravelPreTripAction,
  TravelTimelineEntry,
  TravelTrip,
  TravelUpcomingTrip,
} from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Shared primitives
// ---------------------------------------------------------------------------

/** Empty-state text: serif italic per Dispatch typography guidelines. */
function EmptyStateLine({ children }: { children: ReactNode }) {
  return (
    <p
      className="text-sm text-muted-foreground italic font-[family-name:var(--font-serif,serif)]"
      data-testid="empty-state-line"
    >
      {children}
    </p>
  );
}

/** Skeleton loading placeholder (non-spinner). */
function LoadingLine() {
  return (
    <p className="text-sm text-muted-foreground" data-testid="loading-line">
      Loading…
    </p>
  );
}

/** Severity chip for pre-trip actions and alerts. */
function SeverityBadge({ severity }: { severity: string }) {
  const variant =
    severity === "high"
      ? "destructive"
      : severity === "medium"
        ? "secondary"
        : "outline";
  return (
    <Badge variant={variant} className="text-xs shrink-0">
      {severity}
    </Badge>
  );
}

/** Status chip for trips. */
function StatusBadge({ status }: { status: string }) {
  const variant =
    status === "active"
      ? "default"
      : status === "planned"
        ? "secondary"
        : status === "completed"
          ? "outline"
          : "destructive";
  return (
    <Badge variant={variant} className="text-xs shrink-0">
      {status}
    </Badge>
  );
}

/**
 * Safely format a timeline sort_key for display.
 *
 * The backend can emit:
 *   - ISO datetime:        "2025-06-01T14:00:00+00:00"
 *   - Date-only:           "2025-06-01"  (accommodations)
 *   - Space-separated UTC: "2025-06-01 14:00:00+00:00" (str(datetime))
 *
 * Returns "—" when sortKey is null/undefined, or the raw sortKey when parsing
 * fails (avoids "Invalid Date" leaking to the UI).
 */
function formatSortKey(sortKey: string | null | undefined): string {
  if (!sortKey) return "—";
  // Normalise space-separated datetime (Python str(datetime)) to ISO T-form
  const iso = sortKey.includes("T") ? sortKey : sortKey.replace(" ", "T");
  const dateOnly = /^\d{4}-\d{2}-\d{2}(T00:00:00.*)?$/.test(iso);
  const d = new Date(iso);
  if (isNaN(d.getTime())) return sortKey;
  if (dateOnly) {
    // Render date-only strings without a time component to avoid TZ shifts
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// Section 1: KPI strip
// ---------------------------------------------------------------------------

interface KpiStripProps {
  upcoming: ReturnType<typeof useUpcomingTravel>["data"];
  isLoading: boolean;
}

function KpiStrip({ upcoming, isLoading }: KpiStripProps) {
  const upcomingTrips = upcoming?.upcoming_trips ?? [];
  const actions = upcoming?.actions ?? [];

  const nextTrip: TravelUpcomingTrip | undefined = upcomingTrips[0];
  const activeCount = upcomingTrips.filter((ut) => ut.trip.status === "active").length;
  const plannedCount = upcomingTrips.filter((ut) => ut.trip.status === "planned").length;
  const highSeverityCount = actions.filter((a) => a.severity === "high").length;

  const nextDeparture =
    nextTrip != null
      ? `${nextTrip.trip.name}${nextTrip.days_until_departure != null ? ` · ${nextTrip.days_until_departure}d` : ""}`
      : "—";

  const kpis = [
    { label: "Next departure", value: isLoading ? "…" : nextDeparture, testId: "kpi-next-departure" },
    { label: "Active trips", value: isLoading ? "…" : activeCount, testId: "kpi-active-count" },
    { label: "Planned trips", value: isLoading ? "…" : plannedCount, testId: "kpi-planned-count" },
    { label: "Open actions", value: isLoading ? "…" : actions.length, testId: "kpi-open-actions", highlight: highSeverityCount > 0 },
  ];

  return (
    <div
      className="grid grid-cols-2 gap-3 sm:grid-cols-4"
      data-testid="travel-kpi-strip"
    >
      {kpis.map((kpi) => (
        <Card key={kpi.label}>
          <CardContent className="pt-4">
            <p className="text-xs text-muted-foreground">{kpi.label}</p>
            <p
              className={`text-2xl font-bold font-mono truncate ${kpi.highlight ? "text-destructive" : ""}`}
              data-testid={kpi.testId}
            >
              {kpi.value}
            </p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section 2: Pre-trip actions panel
// ---------------------------------------------------------------------------

interface PreTripActionsPanelProps {
  actions: TravelPreTripAction[];
  isLoading: boolean;
}

function PreTripActionsPanel({ actions, isLoading }: PreTripActionsPanelProps) {
  return (
    <Card data-testid="pre-trip-actions-panel">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Pre-trip actions</CardTitle>
        <CardDescription>Urgency-ranked items requiring attention</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingLine />
        ) : actions.length === 0 ? (
          <EmptyStateLine>All clear — no pre-trip actions required.</EmptyStateLine>
        ) : (
          <ul className="divide-y" data-testid="pre-trip-actions-list">
            {actions.map((action) => (
              <li
                key={`${action.trip_id}-${action.type}`}
                className="flex items-start justify-between gap-2 py-2"
                data-testid="pre-trip-action-item"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate">{action.message}</p>
                  <p className="text-xs text-muted-foreground truncate">{action.trip_name}</p>
                </div>
                <SeverityBadge severity={action.severity} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section 3: Trip roster
// ---------------------------------------------------------------------------

const TRIPS_PAGE_SIZE = 10;

interface TripRosterProps {
  onTripClick: (trip: TravelTrip) => void;
}

function TripRoster({ onTripClick }: TripRosterProps) {
  const [page, setPage] = useState(0);

  const { data: tripsResp, isLoading } = useTravelTrips({
    offset: page * TRIPS_PAGE_SIZE,
    limit: TRIPS_PAGE_SIZE,
  });

  const trips = tripsResp?.data ?? [];
  const total = tripsResp?.meta?.total ?? 0;
  const hasMore = tripsResp?.meta?.has_more ?? false;
  const totalPages = Math.max(1, Math.ceil(total / TRIPS_PAGE_SIZE));
  const currentPage = page + 1;

  return (
    <Card data-testid="trip-roster">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Trip roster</CardTitle>
        <CardDescription>All trips — click a row for details</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        ) : trips.length === 0 ? (
          <EmptyStateLine>No trips found.</EmptyStateLine>
        ) : (
          <>
            <ul className="divide-y" data-testid="trip-roster-list">
              {trips.map((trip) => (
                <li key={trip.id}>
                  <button
                    type="button"
                    className="w-full flex items-center justify-between gap-2 py-2 text-left hover:bg-muted/50 rounded px-1 -mx-1 transition-colors"
                    onClick={() => onTripClick(trip)}
                    data-testid="trip-roster-row"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium truncate">{trip.name}</p>
                      <p className="text-xs text-muted-foreground truncate">
                        {trip.destination} · {trip.start_date} – {trip.end_date}
                      </p>
                    </div>
                    <StatusBadge status={trip.status} />
                  </button>
                </li>
              ))}
            </ul>

            {total > TRIPS_PAGE_SIZE && (
              <div className="flex items-center justify-between pt-3">
                <p className="text-xs text-muted-foreground">
                  Page {currentPage} of {totalPages}
                </p>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page === 0}
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                  >
                    Previous
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!hasMore}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section 4: Trip detail drawer
// ---------------------------------------------------------------------------

/** Format a timeline entry into a readable one-liner. */
function timelineLabel(entry: TravelTimelineEntry): string {
  return entry.summary || entry.entity_type;
}

interface TripDetailDrawerProps {
  tripId: string | null;
  onClose: () => void;
}

function TripDetailDrawer({ tripId, onClose }: TripDetailDrawerProps) {
  const { data: summary, isLoading } = useTravelTripSummary(tripId);

  return (
    <Sheet open={tripId != null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-md flex flex-col gap-0 p-0 overflow-y-auto"
        data-testid="trip-detail-drawer"
      >
        {/* Header */}
        <SheetHeader className="border-b px-4 py-3 shrink-0">
          <SheetTitle className="font-semibold text-sm truncate">
            {isLoading ? "Loading…" : (summary?.trip.name ?? "Trip")}
          </SheetTitle>
          <SheetDescription className="sr-only">
            Trip detail, including timeline, alerts, and accommodations.
          </SheetDescription>
        </SheetHeader>

        {/* Explicit close button (for tests and keyboard users; Sheet also provides ESC) */}
        <div className="flex justify-end px-4 pt-2 shrink-0">
          <Button variant="ghost" size="sm" onClick={onClose} data-testid="trip-drawer-close">
            Close
          </Button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : !summary ? (
            <p className="text-sm text-muted-foreground" data-testid="drawer-loading-line">
              Trip data unavailable.
            </p>
          ) : (
            <>
              {/* Trip meta */}
              <div data-testid="drawer-trip-meta">
                <p className="text-xs text-muted-foreground mb-1">Destination</p>
                <p className="text-sm font-medium">{summary.trip.destination}</p>
                <p className="text-xs text-muted-foreground mt-2 mb-1">Dates</p>
                <p className="text-sm">
                  {summary.trip.start_date} – {summary.trip.end_date}
                </p>
                <div className="mt-2">
                  <StatusBadge status={summary.trip.status} />
                </div>
              </div>

              {/* Alerts */}
              {summary.alerts.length > 0 && (
                <div data-testid="drawer-alerts">
                  <p className="text-xs font-medium text-muted-foreground mb-2">Alerts</p>
                  <ul className="space-y-1">
                    {summary.alerts.map((alert) => (
                      <li
                        key={alert.type}
                        className="flex items-start gap-2 text-sm"
                        data-testid="drawer-alert-item"
                      >
                        <SeverityBadge severity={alert.severity} />
                        <span className="text-xs text-muted-foreground">{alert.message}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Timeline */}
              <div data-testid="drawer-timeline">
                <p className="text-xs font-medium text-muted-foreground mb-2">Timeline</p>
                {summary.timeline.length === 0 ? (
                  <EmptyStateLine>No timeline entries yet.</EmptyStateLine>
                ) : (
                  <ol className="space-y-2">
                    {summary.timeline.map((entry) => (
                      <li
                        key={`${entry.entity_type}-${entry.entity_id}`}
                        className="flex gap-2 items-start"
                        data-testid="timeline-entry"
                      >
                        <span className="text-xs text-muted-foreground w-28 shrink-0 pt-0.5">
                          {formatSortKey(entry.sort_key)}
                        </span>
                        <span className="text-sm">{timelineLabel(entry)}</span>
                      </li>
                    ))}
                  </ol>
                )}
              </div>

              {/* Accommodations summary */}
              {summary.accommodations.length > 0 && (
                <div data-testid="drawer-accommodations">
                  <p className="text-xs font-medium text-muted-foreground mb-2">
                    Accommodations
                  </p>
                  <ul className="space-y-1">
                    {summary.accommodations.map((acc) => (
                      <li key={acc.id} className="text-sm">
                        <span className="font-medium">{acc.name ?? acc.type}</span>
                        {acc.check_in && acc.check_out && (
                          <span className="text-muted-foreground text-xs ml-2">
                            {acc.check_in} – {acc.check_out}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------
// ButlerTravelTripsTab — composed entry point
// ---------------------------------------------------------------------------

export default function ButlerTravelTripsTab() {
  const [selectedTripId, setSelectedTripId] = useState<string | null>(null);

  const { data: upcoming, isLoading: upcomingLoading } = useUpcomingTravel(90);

  const actions = upcoming?.actions ?? [];

  function handleTripClick(trip: TravelTrip) {
    setSelectedTripId(trip.id);
  }

  function handleDrawerClose() {
    setSelectedTripId(null);
  }

  return (
    <div className="space-y-4 pt-4" data-testid="travel-trips-tab">
      {/* Section 1: KPI strip */}
      <KpiStrip upcoming={upcoming} isLoading={upcomingLoading} />

      {/* Section 2: Pre-trip actions */}
      <PreTripActionsPanel actions={actions} isLoading={upcomingLoading} />

      {/* Section 3: Trip roster */}
      <TripRoster onTripClick={handleTripClick} />

      {/* Section 4: Trip detail drawer (Sheet / Radix Dialog for a11y) */}
      <TripDetailDrawer tripId={selectedTripId} onClose={handleDrawerClose} />
    </div>
  );
}
