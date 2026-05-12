// ---------------------------------------------------------------------------
// ButlerOverviewTab — bu-t0n03 (epic bu-hdavr F.1), bu-yzllz (F.2)
//
// Overview tab body for the butler detail page. Uses the 4-column panel-grid
// frame from finish-butler-detail-body-panel-grid.
//
// Layout (4 columns, 4 rows):
//   Row 1: identity (span=2)   | process (span=2)
//   Row 2: heartbeat (span=2)  | modules (span=2)
//   Row 3: cost (span=1)       | recent sessions (span=3)
//   Row 4: activity feed (span=4, scroll, height="320px")
//
// The Recent Notifications card has been removed (bu-yzllz F.2). Notification
// content is covered by the activity-feed panel, which merges session_completed,
// approval_raised, and memory_write event sources.
//
// Doctrine:
//   - All panel borders via --border token (Panel atom, border-t border-l frame).
//   - No hex/oklch/rgb literals.
//   - No pid field anywhere.
//   - All timestamps via <Time>.
//   - No em-dashes in copy.
// ---------------------------------------------------------------------------

import { Link } from "react-router";

import { ButlerStatusBadge } from "@/components/butler-detail/ButlerStatusBadge";
import { ButlerPanelGrid, Panel, ErrorLine } from "@/components/butler-detail/atoms";
import { Badge } from "@/components/ui/badge";
import { ButlerMark } from "@/components/ui/ButlerMark";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import EligibilityTimeline from "@/components/butler-detail/EligibilityTimeline";
import { useButler, useButlerModules } from "@/hooks/use-butlers";
import { useButlerActivityFeed } from "@/hooks/use-butler-analytics";
import { useCostSummary } from "@/hooks/use-costs";
import { useRegistry, useSetEligibility } from "@/hooks/use-general";
import { useButlerHeartbeats } from "@/hooks/use-system";
import { useButlerSessions } from "@/hooks/use-sessions";
import type { ActivityEventType, ModuleStatus, ProcessFacts, SessionSummary } from "@/api/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ButlerOverviewTabProps {
  butlerName: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Heartbeat freshness thresholds in seconds. */
const HEARTBEAT_STALE_SECONDS = 5 * 60;
const HEARTBEAT_DEAD_SECONDS = 30 * 60;

type HeartbeatFreshness = "fresh" | "stale" | "dead" | "unknown";

function heartbeatFreshness(ageSeconds: number | null | undefined): HeartbeatFreshness {
  if (ageSeconds === null || ageSeconds === undefined) return "unknown";
  if (ageSeconds <= HEARTBEAT_STALE_SECONDS) return "fresh";
  if (ageSeconds <= HEARTBEAT_DEAD_SECONDS) return "stale";
  return "dead";
}

function HeartbeatFreshnessPill({ freshness }: { freshness: HeartbeatFreshness }) {
  switch (freshness) {
    case "fresh":
      return (
        <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90 text-xs">Fresh</Badge>
      );
    case "stale":
      return (
        <Badge variant="outline" className="border-amber-500 text-amber-600 text-xs">
          Stale
        </Badge>
      );
    case "dead":
      return (
        <Badge variant="destructive" className="text-xs">
          Dead
        </Badge>
      );
    default:
      return (
        <Badge variant="secondary" className="text-xs">
          Unknown
        </Badge>
      );
  }
}

/** Map module health status to a colored badge variant for per-module cells. */
function moduleStatusVariant(status: string): {
  className?: string;
  variant?: "default" | "secondary" | "destructive" | "outline";
} {
  switch (status) {
    case "connected":
    case "ok":
      return { className: "bg-emerald-600 text-white hover:bg-emerald-600/90" };
    case "degraded":
      return { variant: "outline", className: "border-amber-500 text-amber-600" };
    case "error":
      return { variant: "destructive" };
    default:
      return { variant: "secondary" };
  }
}

/** Single module cell showing name + status badge. */
function ModuleCell({ mod }: { mod: ModuleStatus }) {
  const { variant, className } = moduleStatusVariant(mod.status);
  return (
    <div
      className="flex flex-col gap-1 rounded-md border p-3"
      title={mod.error ?? mod.status}
    >
      <span className="text-sm font-medium truncate">{mod.name}</span>
      <Badge variant={variant} className={className}>
        {mod.status}
      </Badge>
    </div>
  );
}

/** Map eligibility state to a badge. Quarantined/stale are clickable to restore. */
function eligibilityBadge(
  state: string,
  onClick?: () => void,
  isPending?: boolean,
) {
  if (state === "active") {
    return (
      <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">Active</Badge>
    );
  }
  if (state === "quarantined") {
    return (
      <Badge
        variant="destructive"
        className={isPending ? "opacity-50" : "cursor-pointer"}
        onClick={isPending ? undefined : onClick}
        title="Click to restore to active"
      >
        {isPending ? "Restoring..." : "Quarantined"}
      </Badge>
    );
  }
  if (state === "stale") {
    return (
      <Badge
        variant="outline"
        className={
          isPending
            ? "border-amber-500 text-amber-600 opacity-50"
            : "border-amber-500 text-amber-600 cursor-pointer"
        }
        onClick={isPending ? undefined : onClick}
        title="Click to restore to active"
      >
        {isPending ? "Restoring..." : "Stale"}
      </Badge>
    );
  }
  return <Badge variant="secondary">{state}</Badge>;
}

/** Format a USD cost value. */
function formatCurrency(amount: number): string {
  if (amount < 0.01) return "$0.00";
  return `$${amount.toFixed(2)}`;
}

/** Format seconds into a human-readable liveness duration. */
function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  if (hours < 24) return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  const remainingHours = hours % 24;
  return remainingHours > 0 ? `${days}d ${remainingHours}h` : `${days}d`;
}

/** Map session success field to a compact status badge. */
function sessionStatusBadge(success: boolean | null) {
  if (success === true) {
    return (
      <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">
        Success
      </Badge>
    );
  }
  if (success === false) {
    return <Badge variant="destructive">Failed</Badge>;
  }
  return (
    <Badge variant="secondary" className="text-muted-foreground">
      Running
    </Badge>
  );
}

/** Format a percentage share, rounded to one decimal place. */
function formatPercent(share: number, total: number): string {
  if (total === 0) return "0.0%";
  return `${((share / total) * 100).toFixed(1)}%`;
}

/** Map activity event type to a compact badge label. */
function activityEventBadge(eventType: ActivityEventType) {
  switch (eventType) {
    case "session_completed":
      return (
        <Badge variant="secondary" className="text-xs shrink-0 w-[80px] justify-center">
          session
        </Badge>
      );
    case "approval_raised":
      return (
        <Badge variant="outline" className="border-amber-500 text-amber-600 text-xs shrink-0 w-[80px] justify-center">
          approval
        </Badge>
      );
    case "memory_write":
      return (
        <Badge variant="secondary" className="text-xs shrink-0 w-[80px] justify-center text-muted-foreground">
          memory
        </Badge>
      );
    default:
      return (
        <Badge variant="secondary" className="text-xs shrink-0 w-[80px] justify-center">
          {eventType}
        </Badge>
      );
  }
}

// ---------------------------------------------------------------------------
// Process facts panel body
// ---------------------------------------------------------------------------

interface ProcessFactsPanelBodyProps {
  processFacts: ProcessFacts | null | undefined;
}

function ProcessFactsPanelBody({ processFacts }: ProcessFactsPanelBodyProps) {
  const unavailable = "--";
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm font-mono">
      <dt className="text-muted-foreground font-medium font-sans">Container</dt>
      <dd>{processFacts?.container_name ?? unavailable}</dd>
      <dt className="text-muted-foreground font-medium font-sans">Port</dt>
      <dd>{processFacts?.port ?? unavailable}</dd>
      <dt className="text-muted-foreground font-medium font-sans">Registered</dt>
      <dd>
        {processFacts?.registered_duration_seconds != null
          ? formatDuration(processFacts.registered_duration_seconds)
          : unavailable}
      </dd>
      <dt className="text-muted-foreground font-medium font-sans">Config</dt>
      <dd>{processFacts?.config_path ?? unavailable}</dd>
    </dl>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton (panel grid shape)
// ---------------------------------------------------------------------------

function OverviewSkeleton() {
  return (
    <ButlerPanelGrid
      className="sm:grid-cols-2 md:grid-cols-4"
      data-testid="overview-skeleton"
    >
      {/* identity span=2 */}
      <div className="col-span-1 sm:col-span-2 border-r border-b border-border/60 p-4 space-y-3">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-4 w-64" />
        <Skeleton className="h-4 w-32" />
      </div>
      {/* process span=2 */}
      <div className="col-span-1 sm:col-span-2 border-r border-b border-border/60 p-4 space-y-3">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-4 w-56" />
      </div>
      {/* heartbeat span=2 */}
      <div className="col-span-1 sm:col-span-2 border-r border-b border-border/60 p-4 space-y-3">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-4 w-40" />
      </div>
      {/* modules span=2 */}
      <div className="col-span-1 sm:col-span-2 border-r border-b border-border/60 p-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Skeleton className="h-16 rounded-md" />
          <Skeleton className="h-16 rounded-md" />
          <Skeleton className="h-16 rounded-md" />
        </div>
      </div>
      {/* cost span=1 */}
      <div className="col-span-1 border-r border-b border-border/60 p-4 space-y-2">
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-6 w-24" />
      </div>
      {/* recent sessions span=3 */}
      <div className="col-span-1 sm:col-span-2 md:col-span-3 border-r border-b border-border/60 p-4 space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
      {/* activity feed span=4 */}
      <div className="col-span-1 sm:col-span-2 md:col-span-4 border-r border-b border-border/60 p-4 space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    </ButlerPanelGrid>
  );
}

// ---------------------------------------------------------------------------
// ButlerOverviewTab
// ---------------------------------------------------------------------------

export default function ButlerOverviewTab({ butlerName }: ButlerOverviewTabProps) {
  const { data: butlerResponse, isLoading: butlerLoading } = useButler(butlerName);
  const { data: costTodayResponse, isLoading: costTodayLoading } = useCostSummary("today");
  const { data: cost7dResponse, isLoading: cost7dLoading } = useCostSummary("7d");
  const { data: sessionsResponse, isLoading: sessionsLoading } = useButlerSessions(butlerName, {
    limit: 5,
  });
  const { data: registryResponse } = useRegistry();
  const setEligibility = useSetEligibility();
  const { data: heartbeatsResponse, isLoading: heartbeatsLoading } = useButlerHeartbeats();
  const { data: modulesResponse, isLoading: modulesLoading } = useButlerModules(butlerName);
  const {
    data: activityFeedData,
    isLoading: activityFeedLoading,
    isError: activityFeedError,
  } = useButlerActivityFeed(butlerName);

  if (butlerLoading) {
    return <OverviewSkeleton />;
  }

  const butler = butlerResponse?.data;
  // Derive per-butler costs. A value of undefined means the summary wasn't available
  // (loading or error); 0 means the summary loaded but this butler had no spend.
  const costTodaySummary = costTodayResponse?.data;
  const cost7dSummary = cost7dResponse?.data;
  const costToday = costTodaySummary ? (costTodaySummary.by_butler?.[butlerName] ?? 0) : undefined;
  const cost7d = cost7dSummary ? (cost7dSummary.by_butler?.[butlerName] ?? 0) : undefined;
  const globalTotalToday = costTodaySummary?.total_cost_usd;
  const costLoading = costTodayLoading || cost7dLoading;
  const recentSessions = sessionsResponse?.data ?? [];

  // Find this butler's registry entry for eligibility state
  const registryEntry = registryResponse?.data?.find((r) => r.name === butlerName);

  // Find this butler's heartbeat entry
  const heartbeatEntry = heartbeatsResponse?.data?.butlers?.find((b) => b.name === butlerName);
  const freshness = heartbeatFreshness(heartbeatEntry?.heartbeat_age_seconds);

  // Per-module health from dedicated endpoint
  const modules = modulesResponse?.data ?? [];

  // Activity feed events
  const activityEvents = activityFeedData?.events ?? [];

  const showShareRow =
    costToday != null && globalTotalToday != null && globalTotalToday > 0;

  return (
    <ButlerPanelGrid
      className="sm:grid-cols-2 md:grid-cols-4"
      data-testid="overview-panel-grid"
    >
      {/* ----------------------------------------------------------------- */}
      {/* Row 1: identity (span=2) | process (span=2)                        */}
      {/* ----------------------------------------------------------------- */}

      {/* Identity panel */}
      <Panel
        title="identity"
        span={2}
        className="sm:col-span-2"
        testId="panel-identity"
      >
        {/* Butler mark + name + status */}
        <div className="flex items-center gap-3 mb-2">
          <ButlerMark name={butlerName} tone="fill" />
          <span className="font-semibold text-foreground">
            {butler?.name ?? butlerName}
          </span>
          {butler && <ButlerStatusBadge status={butler.status} />}
        </div>
        {butler?.description && (
          <p className="italic font-[family-name:var(--font-serif,serif)] text-sm text-muted-foreground mb-3">
            {butler.description}
          </p>
        )}
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
          <dt className="text-muted-foreground font-medium">Port</dt>
          <dd>{butler?.port ?? "--"}</dd>
          <dt className="text-muted-foreground font-medium">Status</dt>
          <dd className="capitalize">{butler?.status ?? "unknown"}</dd>
        </dl>
      </Panel>

      {/* Process panel */}
      <Panel
        title="process"
        span={2}
        className="sm:col-span-2"
        testId="panel-process"
      >
        <ProcessFactsPanelBody processFacts={butler?.process_facts ?? null} />
      </Panel>

      {/* ----------------------------------------------------------------- */}
      {/* Row 2: heartbeat (span=2) | modules (span=2)                       */}
      {/* ----------------------------------------------------------------- */}

      {/* Heartbeat and eligibility panel */}
      <Panel
        title="heartbeat"
        span={2}
        className="sm:col-span-2"
        testId="panel-heartbeat"
      >
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt className="text-muted-foreground font-medium">Last heartbeat</dt>
          <dd className="flex items-center gap-2" data-testid="heartbeat-row">
            {heartbeatsLoading ? (
              <Skeleton className="h-5 w-16" />
            ) : (
              <>
                <HeartbeatFreshnessPill freshness={freshness} />
                {heartbeatEntry?.last_heartbeat_at ? (
                  <span className="text-xs text-muted-foreground">
                    <Time value={heartbeatEntry.last_heartbeat_at} mode="relative" />
                  </span>
                ) : (
                  <span className="text-xs text-muted-foreground">No heartbeat recorded</span>
                )}
              </>
            )}
          </dd>
          {heartbeatEntry?.heartbeat_age_seconds != null && (
            <>
              <dt className="text-muted-foreground font-medium">Age</dt>
              <dd className="text-sm font-mono">
                {formatDuration(heartbeatEntry.heartbeat_age_seconds)}
              </dd>
            </>
          )}
          {registryEntry && (
            <>
              <dt className="text-muted-foreground font-medium">Eligibility</dt>
              <dd>
                {eligibilityBadge(
                  registryEntry.eligibility_state,
                  () =>
                    setEligibility.mutate({
                      name: butlerName,
                      state: "active",
                    }),
                  setEligibility.isPending,
                )}
                {registryEntry.quarantine_reason && (
                  <span className="ml-2 text-xs text-muted-foreground">
                    {registryEntry.quarantine_reason}
                  </span>
                )}
              </dd>
              <dt className="text-muted-foreground font-medium">24h History</dt>
              <dd className="pt-1">
                <EligibilityTimeline butlerName={butlerName} />
              </dd>
            </>
          )}
        </dl>
      </Panel>

      {/* Modules panel */}
      <Panel
        title="modules"
        span={2}
        className="sm:col-span-2"
        testId="panel-modules"
      >
        {modulesLoading ? (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <Skeleton className="h-16 rounded-md" />
            <Skeleton className="h-16 rounded-md" />
            <Skeleton className="h-16 rounded-md" />
          </div>
        ) : modules.length > 0 ? (
          <div
            className="grid grid-cols-2 gap-2 sm:grid-cols-3"
            data-testid="module-health-grid"
          >
            {modules.map((mod) => (
              <ModuleCell key={mod.name} mod={mod} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No modules registered</p>
        )}
      </Panel>

      {/* ----------------------------------------------------------------- */}
      {/* Row 3: cost (span=1) | recent sessions (span=3)                    */}
      {/* ----------------------------------------------------------------- */}

      {/* Cost panel */}
      <Panel
        title="cost today"
        span={1}
        testId="panel-cost"
      >
        {costLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-6 w-24" />
            <Skeleton className="h-6 w-24" />
            <Skeleton className="h-6 w-32" />
          </div>
        ) : costToday == null && cost7d == null ? (
          <p className="text-sm text-muted-foreground">No cost data</p>
        ) : (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted-foreground font-medium">Today</dt>
            <dd className="font-mono">
              {costToday != null ? formatCurrency(costToday) : "--"}
            </dd>
            <dt className="text-muted-foreground font-medium">Last 7d</dt>
            <dd className="font-mono">
              {cost7d != null ? formatCurrency(cost7d) : "--"}
            </dd>
            {showShareRow && (
              <>
                <dt className="text-muted-foreground font-medium">Share (today)</dt>
                <dd className="font-mono" data-testid="cost-share-row">
                  {formatCurrency(costToday!)} / {formatCurrency(globalTotalToday!)}{" "}
                  ({formatPercent(costToday!, globalTotalToday!)})
                </dd>
              </>
            )}
          </dl>
        )}
      </Panel>

      {/* Recent sessions panel */}
      <Panel
        title="recent sessions"
        span={3}
        className="sm:col-span-2 md:col-span-3"
        testId="panel-recent-sessions"
      >
        <div className="flex items-center justify-end mb-2">
          <Button variant="link" size="sm" asChild className="h-auto p-0 text-xs">
            <Link to={`/butlers/${encodeURIComponent(butlerName)}/sessions`}>
              View all
            </Link>
          </Button>
        </div>
        {sessionsLoading ? (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : recentSessions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No sessions yet</p>
        ) : (
          <ul className="divide-y divide-border" aria-label="sessions list">
            {recentSessions.map((session: SessionSummary) => (
              <li key={session.id} className="py-2 flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm truncate text-foreground" title={session.prompt}>
                    {session.prompt}
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    <Time value={session.started_at} mode="smart" compact />
                  </p>
                </div>
                <div className="shrink-0">{sessionStatusBadge(session.success)}</div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {/* ----------------------------------------------------------------- */}
      {/* Row 4: activity feed (span=4)                                      */}
      {/* ----------------------------------------------------------------- */}

      <Panel
        title="activity"
        span={4}
        scroll
        height="320px"
        className="sm:col-span-2 md:col-span-4"
        testId="panel-activity-feed"
      >
        {activityFeedLoading ? (
          <div className="space-y-2" data-testid="activity-feed-loading">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : activityFeedError ? (
          <ErrorLine>Could not load activity feed.</ErrorLine>
        ) : activityEvents.length === 0 ? (
          <p className="text-sm text-muted-foreground italic font-[family-name:var(--font-serif,serif)]">
            No recent activity.
          </p>
        ) : (
          <div data-testid="activity-feed-list">
            {activityEvents.map((event, idx) => (
              <div
                key={`${event.ts}-${idx}`}
                className="flex items-baseline gap-3 py-1.5 border-b border-border/40 last:border-b-0 min-w-0"
                data-testid="activity-feed-row"
              >
                {/* Timestamp — 80px, relative */}
                <span className="shrink-0 w-[80px] text-xs text-muted-foreground">
                  <Time value={event.ts} mode="relative" />
                </span>
                {/* Event type badge — 80px */}
                {activityEventBadge(event.event_type)}
                {/* Summary text — flex, truncated */}
                <span className="flex-1 text-xs text-foreground min-w-0 truncate">
                  {event.summary}
                </span>
              </div>
            ))}
          </div>
        )}
      </Panel>

    </ButlerPanelGrid>
  );
}
