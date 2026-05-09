import { Link } from "react-router";

import { NotificationFeed } from "@/components/notifications/notification-feed";
import { NotificationTableSkeleton } from "@/components/skeletons";
import { ButlerStatusBadge } from "@/components/butler-detail/ButlerStatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import EligibilityTimeline from "@/components/butler-detail/EligibilityTimeline";
import { useButler, useButlerModules } from "@/hooks/use-butlers";
import { useCostSummary } from "@/hooks/use-costs";
import { useRegistry, useSetEligibility } from "@/hooks/use-general";
import { useButlerNotifications } from "@/hooks/use-notifications";
import { useButlerHeartbeats } from "@/hooks/use-system";
import { useButlerSessions } from "@/hooks/use-sessions";
import type { ModuleStatus, ProcessFacts, SessionSummary } from "@/api/types";

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

// ---------------------------------------------------------------------------
// Process facts card
// ---------------------------------------------------------------------------

interface ProcessFactsCardProps {
  processFacts: ProcessFacts | null | undefined;
}

function ProcessFactsCard({ processFacts }: ProcessFactsCardProps) {
  const unavailable = "--";
  return (
    <Card aria-label="Process facts">
      <CardHeader>
        <CardTitle>Process Facts</CardTitle>
      </CardHeader>
      <CardContent>
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
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Cost card
// ---------------------------------------------------------------------------

interface CostCardProps {
  butlerName: string;
  cost24h: number | undefined;
  cost7d: number | undefined;
  isLoading: boolean;
}

function CostCard({ butlerName: _butlerName, cost24h, cost7d, isLoading }: CostCardProps) {
  return (
    <Card aria-label="Cost summary">
      <CardHeader>
        <CardTitle>Cost</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-6 w-24" />
            <Skeleton className="h-6 w-24" />
          </div>
        ) : cost24h == null && cost7d == null ? (
          <p className="text-sm text-muted-foreground">No cost data</p>
        ) : (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted-foreground font-medium">Last 24h</dt>
            <dd className="font-mono">{formatCurrency(cost24h ?? 0)}</dd>
            <dt className="text-muted-foreground font-medium">Last 7d</dt>
            <dd className="font-mono">{formatCurrency(cost7d ?? 0)}</dd>
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Recent sessions card
// ---------------------------------------------------------------------------

interface RecentSessionsCardProps {
  butlerName: string;
  sessions: SessionSummary[];
  isLoading: boolean;
}

function RecentSessionsCard({ butlerName, sessions, isLoading }: RecentSessionsCardProps) {
  return (
    <Card aria-label="Recent sessions">
      <CardHeader>
        <CardTitle>Recent Sessions</CardTitle>
        <CardAction>
          <Button variant="link" size="sm" asChild>
            <Link to={`/butlers/${encodeURIComponent(butlerName)}/sessions`}>
              View all
            </Link>
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : sessions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No sessions yet</p>
        ) : (
          <ul className="divide-y divide-border" aria-label="sessions list">
            {sessions.map((session) => (
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
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function OverviewSkeleton() {
  return (
    <div className="space-y-6">
      {/* Identity card skeleton */}
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-4 w-64" />
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-40" />
        </CardContent>
      </Card>

      {/* Process facts skeleton */}
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-36" />
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-4 w-56" />
        </CardContent>
      </Card>

      {/* Module health skeleton */}
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-32" />
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <Skeleton className="h-16 rounded-md" />
            <Skeleton className="h-16 rounded-md" />
            <Skeleton className="h-16 rounded-md" />
          </div>
        </CardContent>
      </Card>

      {/* Cost card skeleton */}
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-28" />
        </CardHeader>
        <CardContent className="space-y-2">
          <Skeleton className="h-6 w-24" />
          <Skeleton className="h-6 w-24" />
        </CardContent>
      </Card>

      {/* Recent sessions skeleton */}
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-36" />
        </CardHeader>
        <CardContent className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </CardContent>
      </Card>

      {/* Notifications skeleton */}
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-44" />
        </CardHeader>
        <CardContent>
          <NotificationTableSkeleton rows={5} />
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ButlerOverviewTab
// ---------------------------------------------------------------------------

export default function ButlerOverviewTab({ butlerName }: ButlerOverviewTabProps) {
  const { data: butlerResponse, isLoading: butlerLoading } = useButler(butlerName);
  const { data: cost24hResponse, isLoading: cost24hLoading } = useCostSummary("24h");
  const { data: cost7dResponse, isLoading: cost7dLoading } = useCostSummary("7d");
  const { data: sessionsResponse, isLoading: sessionsLoading } = useButlerSessions(butlerName, {
    limit: 5,
  });
  const {
    data: notificationsResponse,
    isLoading: notificationsLoading,
  } = useButlerNotifications(butlerName, { limit: 5 });
  const { data: registryResponse } = useRegistry();
  const setEligibility = useSetEligibility();
  const { data: heartbeatsResponse, isLoading: heartbeatsLoading } = useButlerHeartbeats();
  const { data: modulesResponse, isLoading: modulesLoading } = useButlerModules(butlerName);

  if (butlerLoading) {
    return <OverviewSkeleton />;
  }

  const butler = butlerResponse?.data;
  // Use the per-butler cost if the summary is available; default to 0 when the butler
  // had no spend in the period. Both are null/undefined only when the request failed
  // or is still loading.
  const cost24hSummary = cost24hResponse?.data;
  const cost7dSummary = cost7dResponse?.data;
  const cost24h = cost24hSummary ? (cost24hSummary.by_butler?.[butlerName] ?? 0) : undefined;
  const cost7d = cost7dSummary ? (cost7dSummary.by_butler?.[butlerName] ?? 0) : undefined;
  const costLoading = cost24hLoading || cost7dLoading;
  const recentSessions = sessionsResponse?.data ?? [];
  const notifications = notificationsResponse?.data ?? [];

  // Find this butler's registry entry for eligibility state
  const registryEntry = registryResponse?.data?.find((r) => r.name === butlerName);

  // Find this butler's heartbeat entry
  const heartbeatEntry = heartbeatsResponse?.data?.butlers?.find((b) => b.name === butlerName);
  const freshness = heartbeatFreshness(heartbeatEntry?.heartbeat_age_seconds);

  // Per-module health from dedicated endpoint
  const modules = modulesResponse?.data ?? [];

  return (
    <div className="space-y-6">
      {/* Identity Card */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-3">
            {butler?.name ?? butlerName}
            {butler && <ButlerStatusBadge status={butler.status} />}
          </CardTitle>
          {butler?.description && (
            <CardDescription className="italic font-[family-name:var(--font-serif,serif)]">
              {butler.description}
            </CardDescription>
          )}
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted-foreground font-medium">Port</dt>
            <dd>{butler?.port ?? "--"}</dd>
            <dt className="text-muted-foreground font-medium">Status</dt>
            <dd className="capitalize">{butler?.status ?? "unknown"}</dd>
            <dt className="text-muted-foreground font-medium">Heartbeat</dt>
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
        </CardContent>
      </Card>

      {/* Process Facts */}
      <ProcessFactsCard processFacts={butler?.process_facts ?? null} />

      {/* Module Health */}
      <Card>
        <CardHeader>
          <CardTitle>Module Health</CardTitle>
        </CardHeader>
        <CardContent>
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
        </CardContent>
      </Card>

      {/* Cost Card */}
      <CostCard
        butlerName={butlerName}
        cost24h={cost24h}
        cost7d={cost7d}
        isLoading={costLoading}
      />

      {/* Recent Sessions */}
      <RecentSessionsCard
        butlerName={butlerName}
        sessions={recentSessions}
        isLoading={sessionsLoading}
      />

      {/* Recent Notifications */}
      <Card>
        <CardHeader>
          <CardTitle>Recent Notifications</CardTitle>
          <CardDescription>
            Last {notifications.length || 5} notifications from this butler
          </CardDescription>
          <CardAction>
            <Button variant="link" size="sm" asChild>
              <Link to={`/notifications?butler=${encodeURIComponent(butlerName)}`}>
                View all
              </Link>
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent>
          {notificationsLoading ? (
            <NotificationTableSkeleton rows={5} />
          ) : (
            <NotificationFeed notifications={notifications} isLoading={false} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
