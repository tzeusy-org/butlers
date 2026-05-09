import { useMemo, useState } from "react";
import { Link } from "react-router";

import type { ButlerSummary } from "@/api/types";
import { useButlers } from "@/hooks/use-butlers";
import { useRegistry } from "@/hooks/use-general";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Page } from "@/components/ui/page";
import { ButlerMark } from "@/components/ui/ButlerMark";

// ---------------------------------------------------------------------------
// Status pill
// ---------------------------------------------------------------------------

function statusPill(status: string) {
  switch (status) {
    case "ok":
    case "online":
      return <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90 text-xs">Up</Badge>;
    case "error":
    case "down":
    case "offline":
      return <Badge variant="destructive" className="text-xs">Down</Badge>;
    case "degraded":
      return (
        <Badge variant="outline" className="border-amber-500 text-amber-600 text-xs">
          Degraded
        </Badge>
      );
    default:
      return <Badge variant="secondary" className="text-xs">{status}</Badge>;
  }
}

// ---------------------------------------------------------------------------
// Eligibility chip
// ---------------------------------------------------------------------------

function eligibilityChip(state: string) {
  if (state === "active") {
    return (
      <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90 text-xs">
        Active
      </Badge>
    );
  }
  if (state === "quarantined") {
    return <Badge variant="destructive" className="text-xs">Quarantined</Badge>;
  }
  if (state === "stale") {
    return (
      <Badge variant="outline" className="border-amber-500 text-amber-600 text-xs">
        Stale
      </Badge>
    );
  }
  return <Badge variant="secondary" className="text-xs capitalize">{state}</Badge>;
}

// ---------------------------------------------------------------------------
// ButlerCard — denser Dispatch layout (bu-insd4.1)
//
// Layout: 3-column grid
//   col-1  40px   ButlerMark glyph
//   col-2  1fr    name + status pill / description
//   col-3  auto   sessions count + open → link + eligibility chip
//
// Hover: inline margin/padding tween gives a subtle lift without a shadow.
// ---------------------------------------------------------------------------

function ButlerCard({
  butler,
  eligibilityState,
}: {
  butler: ButlerSummary;
  eligibilityState?: string;
}) {
  const detailPath = `/butlers/${encodeURIComponent(butler.name)}`;
  const [hover, setHover] = useState(false);

  const isActive = butler.status === "ok" || butler.status === "online";
  const tone = isActive ? "fill" : "neutral";

  return (
    <Link
      to={detailPath}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className="grid grid-cols-[40px_1fr_auto] gap-6 py-[22px] items-start no-underline text-inherit transition-[margin-inline,padding-inline] duration-[120ms] ease-in-out"
      style={{
        marginInline: hover ? "-16px" : "0",
        paddingInline: hover ? "16px" : "0",
      }}
    >
      {/* Col 1: ButlerMark glyph */}
      <div style={{ marginTop: 2 }}>
        <ButlerMark name={butler.name} tone={tone} />
      </div>

      {/* Col 2: name + status pill / description */}
      <div style={{ minWidth: 0 }}>
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="text-base font-medium tracking-tight capitalize whitespace-nowrap">
            {butler.name}
          </span>
          {statusPill(butler.status)}
        </div>
        {butler.description ? (
          <p className="text-sm text-muted-foreground mt-1 leading-snug max-w-[52ch] italic font-[family-name:var(--font-serif,serif)]">
            {butler.description}
          </p>
        ) : null}
      </div>

      {/* Col 3: sessions count + open → link + eligibility chip */}
      <div className="flex flex-col items-end gap-2 min-w-[8rem] mt-1">
        {eligibilityState ? eligibilityChip(eligibilityState) : null}
        <div className="flex items-baseline gap-3 font-mono text-[11px]">
          <span>
            <span className="font-medium tabular-nums">{butler.sessions_24h}</span>
            <span className="text-muted-foreground ml-1">sess</span>
          </span>
          <span
            className="font-sans text-[13px] font-medium underline underline-offset-4 decoration-border/50"
            style={{ opacity: hover ? 1 : 0.65, transition: "opacity 120ms ease" }}
          >
            open →
          </span>
        </div>
      </div>
    </Link>
  );
}

// ---------------------------------------------------------------------------
// ButlerGroup — rule-separated list of ButlerCard rows
// ---------------------------------------------------------------------------

function ButlerGroup({
  butlers,
  eligibilityMap,
}: {
  butlers: ButlerSummary[];
  eligibilityMap: Map<string, string>;
}) {
  return (
    <div className="divide-y divide-border/40">
      {butlers.map((butler) => (
        <ButlerCard
          key={butler.name}
          butler={butler}
          eligibilityState={eligibilityMap.get(butler.name)}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ButlersPage
// ---------------------------------------------------------------------------

export default function ButlersPage() {
  const { data: response, isLoading, isError, error, refetch } = useButlers();
  const { data: registryResponse } = useRegistry();

  const { butlers, staffers, onlineCount } = useMemo(() => {
    const allSorted = [...(response?.data ?? [])].sort((a, b) => a.name.localeCompare(b.name));
    const butlerList = allSorted.filter((b) => b.type !== "staffer");
    const stafferList = allSorted.filter((b) => b.type === "staffer");
    const count = allSorted.filter((b) => b.status === "ok" || b.status === "online").length;
    return { butlers: butlerList, staffers: stafferList, onlineCount: count };
  }, [response?.data]);

  const eligibilityMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const entry of registryResponse?.data ?? []) {
      map.set(entry.name, entry.eligibility_state);
    }
    return map;
  }, [registryResponse?.data]);

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
              <CardContent className="pt-6">
                <div className="text-muted-foreground text-sm font-medium mb-1">Total Agents</div>
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
              <CardContent className="pt-6">
                <div className="text-muted-foreground text-sm font-medium mb-1">Healthy</div>
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
            <div className="space-y-2">
              <h2 className="text-lg font-semibold tracking-tight">Butlers</h2>
              <ButlerGroup butlers={butlers} eligibilityMap={eligibilityMap} />
            </div>
          )}

          {staffers.length > 0 && (
            <div className="space-y-2">
              <h2 className="text-lg font-semibold tracking-tight">Staffers</h2>
              <p className="text-muted-foreground text-sm -mt-1">
                Infrastructure services that support butler operations.
              </p>
              <ButlerGroup butlers={staffers} eligibilityMap={eligibilityMap} />
            </div>
          )}
        </>
      )}
    </Page>
  );
}
