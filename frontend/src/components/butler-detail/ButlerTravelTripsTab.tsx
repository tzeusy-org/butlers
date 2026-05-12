/**
 * ButlerTravelTripsTab
 *
 * Wires the travel butler's trips and upcoming-travel endpoints to the Trips
 * bespoke tab on the travel butler detail page. Consumes existing hooks only —
 * no new HTTP routes are added.
 *
 * Three rows (4-col panel grid):
 *  1. KPI strip        — next departure, active count, planned count, open actions.
 *  2. Week ahead (span 2) + upcoming checklist (span 2).
 *  3. Trips roster (span 4) — paginated card list; row-click opens detail drawer.
 *
 * Document-expiry alert: no dedicated endpoint exists yet. A follow-up is filed
 * as a discovered gap (see DISCOVERED_FOLLOW_UPS below).
 *
 * bead: bu-iuol4.36 (redesign)
 * original bead: bu-0eac9
 */

import { useState, useMemo } from "react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { KpiCell, Panel } from "@/components/butler-detail/atoms";
import { toneClass } from "@/components/butler-detail/atoms-utils";
import type { Tone } from "@/components/butler-detail/atoms-utils";
import {
  useUpcomingTravel,
  useTravelTrips,
  useTravelTripSummary,
  useExpiringDocuments,
} from "@/hooks/use-travel";
import type {
  TravelExpiringDocument,
  TravelLeg,
  TravelAccommodation,
  TravelTimelineEntry,
  TravelTrip,
  TravelUpcomingTrip,
} from "@/api/index.ts";

// Look-ahead window for expiring-document alerts (must match hook default).
const EXPIRING_DOCS_LOOKAHEAD_DAYS = 180;

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

/** Non-spinner loading placeholder. */
function LoadingLine() {
  return (
    <p className="text-sm text-muted-foreground" data-testid="loading-line">
      Loading…
    </p>
  );
}

/** Severity badge for pre-trip actions and alerts. Maps to design token variants only. */
function SeverityBadge({ severity }: { severity: string }) {
  const variant =
    severity === "high"
      ? "destructive"
      : severity === "medium"
        ? "default"
        : "outline";
  return (
    <Badge variant={variant} className="text-xs shrink-0">
      {severity}
    </Badge>
  );
}

/** Status badge for trips. Maps to design token variants only. */
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
 * Normalise a timeline sort_key for use with the <Time> primitive.
 *
 * The backend can emit:
 *   - ISO datetime:        "2025-06-01T14:00:00+00:00"
 *   - Date-only:           "2025-06-01"  (accommodations)
 *   - Space-separated UTC: "2025-06-01 14:00:00+00:00" (str(datetime))
 *
 * Returns null when sortKey is null/undefined so callers can show "—" instead.
 */
function normaliseSortKey(sortKey: string | null | undefined): string | null {
  if (!sortKey) return null;
  // Normalise Python str(datetime) space to ISO T-separator.
  return sortKey.includes("T") ? sortKey : sortKey.replace(" ", "T");
}

// ---------------------------------------------------------------------------
// Expiring docs banner (rendered above KPI strip when count > 0)
// ---------------------------------------------------------------------------

interface ExpiringDocsBannerProps {
  documents: TravelExpiringDocument[];
  lookaheadDays: number;
}

function ExpiringDocsBanner({ documents, lookaheadDays }: ExpiringDocsBannerProps) {
  if (documents.length === 0) return null;

  const urgentCount = documents.filter((d) => d.days_until_expiry <= 30).length;
  const tone: Tone = urgentCount > 0 ? "red" : "amber";

  return (
    <div
      className="flex items-center gap-2 px-4 py-2 border border-border/60 bg-muted/30 rounded text-sm"
      data-testid="expiring-docs-banner"
      role="alert"
    >
      <span className={`font-medium tnum ${toneClass(tone)}`} data-testid="expiring-docs-count">
        {documents.length}
      </span>
      <span className="text-muted-foreground">
        {documents.length === 1 ? "document" : "documents"} expiring within {lookaheadDays} days
        {urgentCount > 0 && (
          <span className={`${toneClass("red")} font-medium ml-1`}>
            ({urgentCount} within 30 days)
          </span>
        )}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section 1: KPI strip (full 4-col width)
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

  const nextDepartureName =
    nextTrip != null
      ? nextTrip.trip.name
      : "—";
  const nextDepartureSub =
    nextTrip?.days_until_departure != null
      ? `${nextTrip.days_until_departure}d away`
      : undefined;

  return (
    <div
      className="grid grid-cols-1 lg:grid-cols-4 border-t border-l border-border/60"
      data-testid="travel-kpi-strip"
    >
      <Panel>
        <KpiCell
          label="Next departure"
          value={
            <span data-testid="kpi-next-departure">
              {isLoading ? "…" : nextDepartureName}
            </span>
          }
          sub={isLoading ? undefined : nextDepartureSub}
        />
      </Panel>

      <Panel>
        <KpiCell
          label="Active trips"
          value={
            <span data-testid="kpi-active-count" className="tnum">
              {isLoading ? "…" : activeCount}
            </span>
          }
        />
      </Panel>

      <Panel>
        <KpiCell
          label="Planned trips"
          value={
            <span data-testid="kpi-planned-count" className="tnum">
              {isLoading ? "…" : plannedCount}
            </span>
          }
        />
      </Panel>

      <Panel>
        <KpiCell
          label="Open actions"
          tone={highSeverityCount > 0 ? "red" : "fg"}
          value={
            <span data-testid="kpi-open-actions" className="tnum">
              {isLoading ? "…" : actions.length}
            </span>
          }
          sub={highSeverityCount > 0 ? `${highSeverityCount} high severity` : undefined}
        />
      </Panel>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section 2a: Week ahead schedule (span 2)
//
// Shows legs (flights/trains) and accommodation check-ins occurring within the
// next 7 days across all upcoming trips.
// ---------------------------------------------------------------------------

interface WeekAheadEntry {
  date: string;       // ISO date or datetime for <Time>
  label: string;      // Human-readable one-liner
  kind: "leg" | "checkin";
  kindLabel: string;  // Display label derived from leg.type or "hotel"
}

function buildWeekAheadEntries(upcomingTrips: TravelUpcomingTrip[]): WeekAheadEntry[] {
  const now = Date.now();
  const sevenDays = 7 * 24 * 60 * 60 * 1000;
  const entries: WeekAheadEntry[] = [];

  for (const ut of upcomingTrips) {
    // Legs: use departure_at for date
    for (const leg of ut.legs as TravelLeg[]) {
      const ms = new Date(leg.departure_at).getTime();
      if (!isNaN(ms) && ms >= now && ms <= now + sevenDays) {
        const from = leg.departure_city ?? leg.departure_airport_station ?? "?";
        const to = leg.arrival_city ?? leg.arrival_airport_station ?? "?";
        const carrier = leg.carrier ? ` (${leg.carrier})` : "";
        entries.push({
          date: leg.departure_at,
          label: `${from} → ${to}${carrier}`,
          kind: "leg",
          kindLabel: leg.type ?? "leg",
        });
      }
    }

    // Accommodations: use check_in for date
    for (const acc of ut.accommodations as TravelAccommodation[]) {
      if (!acc.check_in) continue;
      // Date-only: anchor to noon UTC to avoid TZ shifting issues
      const ms = new Date(acc.check_in + "T12:00:00.000Z").getTime();
      if (!isNaN(ms) && ms >= now && ms <= now + sevenDays) {
        const name = acc.name ?? acc.type;
        entries.push({
          date: acc.check_in,
          label: `Check-in: ${name}`,
          kind: "checkin",
          kindLabel: acc.type ?? "hotel",
        });
      }
    }
  }

  // Sort by date ascending
  entries.sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());
  return entries;
}

interface WeekAheadScheduleProps {
  upcoming: ReturnType<typeof useUpcomingTravel>["data"];
  isLoading: boolean;
  error: Error | null;
}

function WeekAheadSchedule({ upcoming, isLoading, error }: WeekAheadScheduleProps) {
  const entries = useMemo(
    () => buildWeekAheadEntries(upcoming?.upcoming_trips ?? []),
    [upcoming],
  );

  return (
    <Panel title="Week ahead" sub="next 7 days" span={2} scroll height="280px">
      {isLoading ? (
        <LoadingLine />
      ) : error ? (
        <EmptyStateLine>{error.message || "Error loading schedule."}</EmptyStateLine>
      ) : entries.length === 0 ? (
        <EmptyStateLine>No legs or check-ins in the next 7 days.</EmptyStateLine>
      ) : (
        <ol
          className="space-y-2"
          aria-label="Week ahead schedule"
          data-testid="week-ahead-list"
        >
          {entries.map((entry, idx) => (
            <li
              key={`${entry.date}-${idx}`}
              className="flex items-start gap-3 text-sm"
              data-testid="week-ahead-entry"
            >
              <span className="shrink-0 w-24 text-xs text-muted-foreground font-mono tnum pt-0.5">
                <Time value={entry.date} mode="absolute" precision="day" compact />
              </span>
              <span className="min-w-0 flex-1">
                {entry.label}
                <span className="ml-2">
                  <Badge
                    variant={entry.kind === "leg" ? "secondary" : "outline"}
                    className="text-[10px] py-0 px-1"
                  >
                    {entry.kindLabel}
                  </Badge>
                </span>
              </span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Section 2b: Upcoming checklist (span 2)
//
// Pre-trip actions ranked by urgency_rank ascending (high urgency first).
// ---------------------------------------------------------------------------

interface UpcomingChecklistProps {
  upcoming: ReturnType<typeof useUpcomingTravel>["data"];
  isLoading: boolean;
  error: Error | null;
}

function UpcomingChecklist({ upcoming, isLoading, error }: UpcomingChecklistProps) {
  // Sort by urgency_rank ascending — lowest rank = highest urgency.
  const ranked = useMemo(
    () => [...(upcoming?.actions ?? [])].sort((a, b) => a.urgency_rank - b.urgency_rank),
    [upcoming],
  );

  return (
    <Panel title="Upcoming checklist" sub="ranked by urgency" span={2} scroll height="280px">
      {isLoading ? (
        <LoadingLine />
      ) : error ? (
        <EmptyStateLine>{error.message || "Error loading checklist."}</EmptyStateLine>
      ) : ranked.length === 0 ? (
        <EmptyStateLine>All clear. No pre-trip actions required.</EmptyStateLine>
      ) : (
        <ul className="divide-y divide-border/40" data-testid="pre-trip-actions-list">
          {ranked.map((action) => (
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
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Section 3: Trips roster (span 4, paginated)
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
    <Panel title="Trips roster" sub="all trips" span={4}>
      {isLoading ? (
        <div className="space-y-2" data-testid="trip-roster-loading">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : trips.length === 0 ? (
        <EmptyStateLine>No trips found.</EmptyStateLine>
      ) : (
        <>
          <ul className="divide-y divide-border/40" data-testid="trip-roster-list">
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
                    <p className="text-xs text-muted-foreground truncate font-mono tnum">
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
              <p className="text-xs text-muted-foreground tnum">
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
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Trip detail drawer
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
                <p className="text-sm font-mono tnum">
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
                    {summary.timeline.map((entry) => {
                      const iso = normaliseSortKey(entry.sort_key);
                      return (
                        <li
                          key={`${entry.entity_type}-${entry.entity_id}`}
                          className="flex gap-2 items-start"
                          data-testid="timeline-entry"
                        >
                          <span className="text-xs text-muted-foreground w-28 shrink-0 pt-0.5 font-mono tnum">
                            {iso ? (
                              <Time value={iso} mode="absolute" precision="day" compact />
                            ) : (
                              "—"
                            )}
                          </span>
                          <span className="text-sm">{timelineLabel(entry)}</span>
                        </li>
                      );
                    })}
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
                          <span className="text-muted-foreground text-xs ml-2 font-mono tnum">
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

  const { data: upcoming, isLoading: upcomingLoading, error: upcomingError } = useUpcomingTravel(90);
  const { data: expiringDocs } = useExpiringDocuments(EXPIRING_DOCS_LOOKAHEAD_DAYS);

  const expiringDocuments = expiringDocs?.documents ?? [];

  function handleTripClick(trip: TravelTrip) {
    setSelectedTripId(trip.id);
  }

  function handleDrawerClose() {
    setSelectedTripId(null);
  }

  return (
    <div className="pt-4" data-testid="travel-trips-tab">
      {/* Expiring docs banner — only shown when documents are expiring */}
      {expiringDocuments.length > 0 && (
        <div className="px-4 pb-3">
          <ExpiringDocsBanner documents={expiringDocuments} lookaheadDays={EXPIRING_DOCS_LOOKAHEAD_DAYS} />
        </div>
      )}

      {/* Row 1: KPI strip — full 4-col width */}
      <KpiStrip upcoming={upcoming} isLoading={upcomingLoading} />

      {/* Row 2: Week ahead (span 2) + Upcoming checklist (span 2) */}
      <div className="grid grid-cols-1 lg:grid-cols-4 border-l border-border/60">
        <WeekAheadSchedule upcoming={upcoming} isLoading={upcomingLoading} error={upcomingError} />
        <UpcomingChecklist upcoming={upcoming} isLoading={upcomingLoading} error={upcomingError} />
      </div>

      {/* Row 3: Trips roster (span 4) */}
      <div className="grid grid-cols-1 lg:grid-cols-4 border-l border-border/60">
        <TripRoster onTripClick={handleTripClick} />
      </div>

      {/* Trip detail drawer */}
      <TripDetailDrawer tripId={selectedTripId} onClose={handleDrawerClose} />
    </div>
  );
}
