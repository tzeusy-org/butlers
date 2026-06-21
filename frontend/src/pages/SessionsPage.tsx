import { useState } from "react";
import { useSearchParams } from "react-router";

import type { SessionParams, SessionSummary } from "@/api/types";
import { SessionDetailDrawer } from "@/components/sessions/SessionDetailDrawer";
import { SessionsKpiStrip } from "@/components/sessions/SessionsKpiStrip";
import { SessionTable } from "@/components/sessions/SessionTable";
import { SessionStripeChart } from "@/components/dashboard/SessionStripeChart";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Page } from "@/components/ui/page";
import { useButlers } from "@/hooks/use-butlers";
import { useSessions } from "@/hooks/use-sessions";
import { useAutoRefresh } from "@/hooks/use-auto-refresh";
import { AutoRefreshToggle } from "@/components/ui/auto-refresh-toggle";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 20;

const STATUS_OPTIONS = [
  { value: "all", label: "All" },
  { value: "success", label: "Success" },
  { value: "failed", label: "Failed" },
  { value: "running", label: "Running" },
] as const;

// ---------------------------------------------------------------------------
// URL-state — filters + cursor mirrored to the querystring (shareable, refresh-safe)
// ---------------------------------------------------------------------------

interface FilterState {
  butler: string;
  trigger_source: string;
  request_id: string;
  status: string;
  since: string;
  until: string;
}

const EMPTY_FILTERS: FilterState = {
  butler: "all",
  trigger_source: "",
  request_id: "",
  status: "all",
  since: "",
  until: "",
};

/** Parse filter state out of the querystring (URL is the source of truth). */
function parseFilters(sp: URLSearchParams): FilterState {
  return {
    butler: sp.get("butler") ?? "all",
    trigger_source: sp.get("trigger") ?? "",
    request_id: sp.get("request") ?? "",
    status: sp.get("status") ?? "all",
    since: sp.get("since") ?? "",
    until: sp.get("until") ?? "",
  };
}

/** Write filter state into a URLSearchParams, omitting default/empty values. */
function applyFilters(sp: URLSearchParams, f: FilterState): void {
  const set = (key: string, value: string, empty: string) => {
    if (value !== empty) sp.set(key, value);
    else sp.delete(key);
  };
  set("butler", f.butler, "all");
  set("trigger", f.trigger_source, "");
  set("request", f.request_id, "");
  set("status", f.status, "all");
  set("since", f.since, "");
  set("until", f.until, "");
}

// ---------------------------------------------------------------------------
// SessionsPage
// ---------------------------------------------------------------------------

export default function SessionsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = parseFilters(searchParams);
  const cursor = searchParams.get("cursor") ?? undefined;

  // History of cursors for pages BEFORE the current one (powers "Newer").
  const [prevCursors, setPrevCursors] = useState<(string | undefined)[]>([]);

  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [selectedSessionButler, setSelectedSessionButler] = useState<string>("");
  const autoRefreshControl = useAutoRefresh(10_000);

  // Fetch butler names for the dropdown + chart hue ordering.
  const { data: butlersResponse } = useButlers();
  const butlers = butlersResponse?.data ?? [];
  const butlerNames = butlers.map((b) => b.name);

  // Filter params shared by the chart and KPI strip (window-true, no cursor).
  const filterParams: SessionParams = {
    ...(filters.butler !== "all" ? { butler: filters.butler } : {}),
    ...(filters.trigger_source ? { trigger_source: filters.trigger_source } : {}),
    ...(filters.request_id ? { request_id: filters.request_id } : {}),
    ...(filters.status !== "all" ? { status: filters.status } : {}),
    ...(filters.since ? { since: filters.since } : {}),
    ...(filters.until ? { until: filters.until } : {}),
  };

  // List params add pagination (cursor + limit) on top of the filters.
  const params: SessionParams = {
    limit: PAGE_SIZE,
    ...(cursor ? { cursor } : {}),
    ...filterParams,
  };

  const {
    data: sessionsResponse,
    isLoading,
    isError,
    error,
    refetch,
  } = useSessions(params, { refetchInterval: autoRefreshControl.refetchInterval });
  const sessions = sessionsResponse?.data ?? [];
  const meta = sessionsResponse?.meta;
  const hasMore = meta?.has_more ?? false;
  const nextCursor = meta?.next_cursor ?? null;

  const canGoNewer = cursor != null || prevCursors.length > 0;

  // -- Filter handlers -------------------------------------------------------

  function handleFilterChange(key: keyof FilterState, value: string) {
    setPrevCursors([]);
    setSearchParams((prev) => {
      const sp = new URLSearchParams(prev);
      applyFilters(sp, { ...parseFilters(prev), [key]: value });
      sp.delete("cursor");
      return sp;
    });
  }

  function handleClearFilters() {
    setPrevCursors([]);
    setSearchParams((prev) => {
      const sp = new URLSearchParams(prev);
      applyFilters(sp, EMPTY_FILTERS);
      sp.delete("cursor");
      return sp;
    });
  }

  function handleRequestIdClick(requestId: string) {
    handleFilterChange("request_id", requestId);
  }

  // -- Keyset pagination handlers --------------------------------------------

  function goOlder() {
    if (!nextCursor) return;
    setPrevCursors((s) => [...s, cursor]);
    setSearchParams((prev) => {
      const sp = new URLSearchParams(prev);
      sp.set("cursor", nextCursor);
      return sp;
    });
  }

  function goNewer() {
    if (prevCursors.length > 0) {
      const target = prevCursors[prevCursors.length - 1];
      setPrevCursors((s) => s.slice(0, -1));
      setSearchParams((prev) => {
        const sp = new URLSearchParams(prev);
        if (target) sp.set("cursor", target);
        else sp.delete("cursor");
        return sp;
      });
    } else {
      // Reload-safe fallback: jump back to the first page.
      setSearchParams((prev) => {
        const sp = new URLSearchParams(prev);
        sp.delete("cursor");
        return sp;
      });
    }
  }

  const hasActiveFilters =
    filters.butler !== "all" ||
    filters.trigger_source !== "" ||
    filters.request_id !== "" ||
    filters.status !== "all" ||
    filters.since !== "" ||
    filters.until !== "";

  function handleSessionClick(session: SessionSummary) {
    setSelectedSessionId(session.id);
    setSelectedSessionButler(session.butler ?? "");
  }

  return (
    <Page
      archetype="list"
      title="Sessions"
      description="Browse session history across all butlers."
      actions={
        <AutoRefreshToggle
          enabled={autoRefreshControl.enabled}
          interval={autoRefreshControl.interval}
          onToggle={autoRefreshControl.setEnabled}
          onIntervalChange={autoRefreshControl.setInterval}
        />
      }
      error={isError ? error : null}
      onRetry={() => refetch()}
      empty={null}
    >
      {/* KPI strip — window-true, scoped to the active filters (not the page rows) */}
      <SessionsKpiStrip filterParams={filterParams} />

      {/* Primary visualization — wired to the active filters, not the cursor */}
      <Card>
        <CardContent className="pt-6">
          <SessionStripeChart butlers={butlers} filterParams={filterParams} />
        </CardContent>
      </Card>

      {/* Filter bar */}
      <Card>
        <CardContent className="pt-0">
          <div className="flex flex-wrap items-end gap-4">
            {/* Butler dropdown */}
            <div className="space-y-1">
              <label className="text-muted-foreground text-xs font-medium">Butler</label>
              <Select
                value={filters.butler}
                onValueChange={(v) => handleFilterChange("butler", v)}
              >
                <SelectTrigger className="w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All</SelectItem>
                  {butlerNames.map((name) => (
                    <SelectItem key={name} value={name}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Trigger source */}
            <div className="space-y-1">
              <label
                htmlFor="filter-trigger"
                className="text-muted-foreground text-xs font-medium"
              >
                Trigger
              </label>
              <Input
                id="filter-trigger"
                placeholder="Filter by trigger..."
                value={filters.trigger_source}
                onChange={(e) => handleFilterChange("trigger_source", e.target.value)}
                className="w-44"
              />
            </div>

            {/* Request ID */}
            <div className="space-y-1">
              <label
                htmlFor="filter-request-id"
                className="text-muted-foreground text-xs font-medium"
              >
                Request ID
              </label>
              <Input
                id="filter-request-id"
                placeholder="Filter by request ID..."
                value={filters.request_id}
                onChange={(e) => handleFilterChange("request_id", e.target.value)}
                className="w-56 font-mono"
              />
            </div>

            {/* Status dropdown */}
            <div className="space-y-1">
              <label className="text-muted-foreground text-xs font-medium">Status</label>
              <Select
                value={filters.status}
                onValueChange={(v) => handleFilterChange("status", v)}
              >
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STATUS_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Since date */}
            <div className="space-y-1">
              <label
                htmlFor="filter-since"
                className="text-muted-foreground text-xs font-medium"
              >
                From
              </label>
              <Input
                id="filter-since"
                type="date"
                value={filters.since}
                onChange={(e) => handleFilterChange("since", e.target.value)}
                className="w-40"
              />
            </div>

            {/* Until date */}
            <div className="space-y-1">
              <label
                htmlFor="filter-until"
                className="text-muted-foreground text-xs font-medium"
              >
                To
              </label>
              <Input
                id="filter-until"
                type="date"
                value={filters.until}
                onChange={(e) => handleFilterChange("until", e.target.value)}
                className="w-40"
              />
            </div>

            {/* Clear filters */}
            {hasActiveFilters && (
              <Button variant="ghost" size="sm" onClick={handleClearFilters}>
                Clear filters
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Session table */}
      <Card>
        <CardContent>
          <SessionTable
            sessions={sessions}
            isLoading={isLoading}
            onSessionClick={handleSessionClick}
            onRequestIdClick={handleRequestIdClick}
            showButlerColumn={true}
          />
        </CardContent>
      </Card>

      {/* Keyset pagination controls (Newer / Older — no page count) */}
      {(sessions.length > 0 || canGoNewer) && (
        <div className="flex items-center justify-end">
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!canGoNewer}
              onClick={goNewer}
              data-testid="sessions-newer"
            >
              Newer
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!hasMore}
              onClick={goOlder}
              data-testid="sessions-older"
            >
              Older
            </Button>
          </div>
        </div>
      )}

      {/* Session detail drawer */}
      <SessionDetailDrawer
        butler={selectedSessionButler}
        sessionId={selectedSessionId}
        onClose={() => setSelectedSessionId(null)}
      />
    </Page>
  );
}
