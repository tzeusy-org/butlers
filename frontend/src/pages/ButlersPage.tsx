import { useMemo } from "react";
import { Link } from "react-router";

import type { ButlerSummary } from "@/api/types";
import { useButlers } from "@/hooks/use-butlers";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Page } from "@/components/ui/page";

function statusBadge(status: string) {
  switch (status) {
    case "ok":
    case "online":
      return <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">Up</Badge>;
    case "error":
    case "down":
    case "offline":
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

function typeBadge(type: "butler" | "staffer") {
  if (type === "staffer") {
    return (
      <Badge variant="outline" className="border-violet-500 text-violet-600 text-xs">
        staffer
      </Badge>
    );
  }
  return null;
}

function ButlerCard({ butler }: { butler: ButlerSummary }) {
  const detailPath = `/butlers/${encodeURIComponent(butler.name)}`;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-3">
          <Link to={detailPath} className="hover:underline">
            {butler.name}
          </Link>
          <span className="flex items-center gap-1.5">
            {typeBadge(butler.type)}
            {statusBadge(butler.status)}
          </span>
        </CardTitle>
        <CardDescription>
          {butler.type === "staffer" ? "Staffer" : "Butler"} endpoint on port {butler.port}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-sm">
          <dt className="text-muted-foreground font-medium">Status</dt>
          <dd className="capitalize">{butler.status}</dd>
          <dt className="text-muted-foreground font-medium">Port</dt>
          <dd>{butler.port}</dd>
        </dl>
      </CardContent>
      <CardFooter>
        <Button variant="outline" size="sm" asChild>
          <Link to={detailPath}>Open details</Link>
        </Button>
      </CardFooter>
    </Card>
  );
}

export default function ButlersPage() {
  const { data: response, isLoading, isError, error, refetch } = useButlers();
  const { butlers, staffers, onlineCount } = useMemo(() => {
    const allSorted = [...(response?.data ?? [])].sort((a, b) => a.name.localeCompare(b.name));
    const butlerList = allSorted.filter((b) => b.type !== "staffer");
    const stafferList = allSorted.filter((b) => b.type === "staffer");
    const count = allSorted.filter((b) => b.status === "ok" || b.status === "online").length;
    return { butlers: butlerList, staffers: stafferList, onlineCount: count };
  }, [response?.data]);

  const hasData = butlers.length > 0 || staffers.length > 0;

  // Full-page error only when there is no cached data to show
  const pageError = isError && !hasData ? error : null;

  return (
    <Page
      archetype="overview"
      title="Butlers"
      description="Browse all registered butlers and jump directly to detail views."
      loading={isLoading}
      error={pageError}
      onRetry={pageError != null ? () => void refetch() : undefined}
    >
      {/* Partial-data stale-fetch banner */}
      {isError && hasData && (
        <Card>
          <CardContent className="py-4">
            <p className="text-sm text-destructive">
              Showing last known butler status. Refresh failed:{" "}
              {error instanceof Error ? error.message : "Unknown error"}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Empty state (no data at all, no error) */}
      {!isError && !hasData && (
        <EmptyState
          title="No butlers found."
          description="Check daemon status and try again."
        />
      )}

      {hasData && (
        <>
          {/* Stats row */}
          <div className="grid gap-4 sm:grid-cols-2">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-muted-foreground text-sm font-medium">
                  Total Agents
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{butlers.length + staffers.length}</div>
                {staffers.length > 0 && (
                  <p className="text-muted-foreground mt-1 text-xs">
                    {butlers.length} butler{butlers.length !== 1 ? "s" : ""},{" "}
                    {staffers.length} staffer{staffers.length !== 1 ? "s" : ""}
                  </p>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-muted-foreground text-sm font-medium">
                  Healthy
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{onlineCount}</div>
                <p className="text-muted-foreground mt-1 text-xs">
                  {Math.round(
                    (onlineCount / (butlers.length + staffers.length)) * 100,
                  )}% currently up
                </p>
              </CardContent>
            </Card>
          </div>

          {butlers.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-lg font-semibold tracking-tight">Butlers</h2>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {butlers.map((butler) => (
                  <ButlerCard key={butler.name} butler={butler} />
                ))}
              </div>
            </div>
          )}

          {staffers.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-lg font-semibold tracking-tight">Staffers</h2>
              <p className="text-muted-foreground text-sm -mt-1">
                Infrastructure services that support butler operations.
              </p>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {staffers.map((staffer) => (
                  <ButlerCard key={staffer.name} butler={staffer} />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </Page>
  );
}
