import { Link } from "react-router";

import { NotificationFeed } from "@/components/notifications/notification-feed";
import { NotificationTableSkeleton } from "@/components/skeletons";
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
import { useButler } from "@/hooks/use-butlers";
import { useCostSummary } from "@/hooks/use-costs";
import { useRegistry, useSetEligibility } from "@/hooks/use-general";
import { useButlerNotifications } from "@/hooks/use-notifications";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ButlerOverviewTabProps {
  butlerName: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Map butler status string to a colored badge. */
function statusBadge(status: string) {
  switch (status) {
    case "ok":
      return (
        <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">Up</Badge>
      );
    case "error":
    case "down":
      return <Badge variant="destructive">Down</Badge>;
    case "degraded":
      return (
        <Badge variant="outline" className="border-amber-500 text-amber-600">
          Degraded
        </Badge>
      );
    default:
      return <Badge variant="secondary">{status}</Badge>;
  }
}

/** Map module health status to a colored badge. */
function moduleHealthBadge(name: string, status: string) {
  const label = name;
  switch (status) {
    case "connected":
    case "ok":
      return (
        <Badge key={name} className="bg-emerald-600 text-white hover:bg-emerald-600/90">
          {label}
        </Badge>
      );
    case "degraded":
      return (
        <Badge key={name} variant="outline" className="border-amber-500 text-amber-600">
          {label}
        </Badge>
      );
    case "error":
      return (
        <Badge key={name} variant="destructive">
          {label}
        </Badge>
      );
    default:
      return (
        <Badge key={name} variant="secondary">
          {label}
        </Badge>
      );
  }
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
        </CardContent>
      </Card>

      {/* Module health skeleton */}
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-32" />
        </CardHeader>
        <CardContent>
          <div className="flex gap-2">
            <Skeleton className="h-6 w-16 rounded-full" />
            <Skeleton className="h-6 w-20 rounded-full" />
            <Skeleton className="h-6 w-16 rounded-full" />
          </div>
        </CardContent>
      </Card>

      {/* Cost card skeleton */}
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-28" />
        </CardHeader>
        <CardContent>
          <Skeleton className="h-8 w-20" />
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
  const { data: costResponse, isLoading: costLoading } = useCostSummary("today");
  const {
    data: notificationsResponse,
    isLoading: notificationsLoading,
  } = useButlerNotifications(butlerName, { limit: 5 });
  const { data: registryResponse } = useRegistry();
  const setEligibility = useSetEligibility();

  if (butlerLoading) {
    return <OverviewSkeleton />;
  }

  const butler = butlerResponse?.data;
  const costSummary = costResponse?.data;
  const notifications = notificationsResponse?.data ?? [];
  const butlerCostToday = costSummary?.by_butler?.[butlerName] ?? 0;
  const butlerCostShare =
    costSummary && costSummary.total_cost_usd > 0
      ? Math.round((butlerCostToday / costSummary.total_cost_usd) * 100)
      : 0;

  // Extract modules from butler data if available
  const modules =
    butler && "modules" in butler
      ? (butler as Record<string, unknown>).modules as
          | { name: string; status: string }[]
          | undefined
      : undefined;

  // Find this butler's registry entry for eligibility state
  const registryEntry = registryResponse?.data?.find((r) => r.name === butlerName);

  return (
    <div className="space-y-6">
      {/* Identity Card */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-3">
            {butler?.name ?? butlerName}
            {butler && statusBadge(butler.status)}
          </CardTitle>
          {butler && "description" in butler && !!(butler as Record<string, unknown>).description && (
            <CardDescription>
              {String((butler as Record<string, unknown>).description)}
            </CardDescription>
          )}
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted-foreground font-medium">Port</dt>
            <dd>{butler?.port ?? "--"}</dd>
            <dt className="text-muted-foreground font-medium">Status</dt>
            <dd className="capitalize">{butler?.status ?? "unknown"}</dd>
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
              </>
            )}
          </dl>
        </CardContent>
      </Card>

      {/* Module Health */}
      <Card>
        <CardHeader>
          <CardTitle>Module Health</CardTitle>
        </CardHeader>
        <CardContent>
          {modules && modules.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {modules.map((mod) => moduleHealthBadge(mod.name, mod.status))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No modules registered</p>
          )}
        </CardContent>
      </Card>

      {/* Cost Card */}
      <Card>
        <CardHeader>
          <CardTitle>Cost Today</CardTitle>
        </CardHeader>
        <CardContent>
          {costLoading ? (
            <Skeleton className="h-8 w-20" />
          ) : costSummary ? (
            <div>
              <div className="text-2xl font-bold">
                {formatCurrency(butlerCostToday)}
              </div>
              <div className="mt-2 grid grid-cols-2 gap-4 text-sm text-muted-foreground">
                <div>
                  <span className="font-medium">Share:</span> {butlerCostShare}%
                </div>
                <div>
                  <span className="font-medium">Global total:</span>{" "}
                  {formatCurrency(costSummary.total_cost_usd)}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No cost data</p>
          )}
        </CardContent>
      </Card>

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
