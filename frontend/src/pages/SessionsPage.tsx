import { useState } from "react";

import type { SessionParams, SessionSummary } from "@/api/types";
import { SessionDetailDrawer } from "@/components/sessions/SessionDetailDrawer";
import { SessionTable } from "@/components/sessions/SessionTable";
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
] as const;

// ---------------------------------------------------------------------------
// Filter state
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

// ---------------------------------------------------------------------------
// SessionsPage
// ---------------------------------------------------------------------------

export default function SessionsPage() {
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [page, setPage] = useState(0);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [selectedSessionButler, setSelectedSessionButler] = useState<string>("");
  const autoRefreshControl = useAutoRefresh(10_000);

  // Fetch butler names for the dropdown
  const { data: butlersResponse } = useButlers();
  const butlerNames = butlersResponse?.data?.map((b) => b.name) ?? [];

  // Build API params from filter state
  const params: SessionParams = {
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    ...(filters.butler !== "all" ? { butler: filters.butler } : {}),
    ...(filters.trigger_source ? { trigger_source: filters.trigger_source } : {}),
    ...(filters.request_id ? { request_id: filters.request_id } : {}),
    ...(filters.status !== "all" ? { status: filters.status } : {}),
    ...(filters.since ? { since: filters.since } : {}),
    ...(filters.until ? { until: filters.until } : {}),
  };

  const { data: sessionsResponse, isLoading } = useSessions(params, { refetchInterval: autoRefreshControl.refetchInterval });
  const sessions = sessionsResponse?.data ?? [];
  const meta = sessionsResponse?.meta;
  const total = meta?.total ?? 0;
  const hasMore = meta?.has_more ?? false;

  // Pagination helpers
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = page + 1;

  function handleFilterChange(key: keyof FilterState, value: string) {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(0);
  }

  function handleClearFilters() {
    setFilters(EMPTY_FILTERS);
    setPage(0);
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

  function handleRequestIdClick(requestId: string) {
    setFilters((prev) => ({ ...prev, request_id: requestId }));
    setPage(0);
  }

  return (
    <div className="space-y-6">
      {/* Page heading */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Sessions</h1>
          <p className="text-muted-foreground mt-1">
            Browse session history across all butlers.
          </p>
        </div>
        <AutoRefreshToggle
          enabled={autoRefreshControl.enabled}
          interval={autoRefreshControl.interval}
          onToggle={autoRefreshControl.setEnabled}
          onIntervalChange={autoRefreshControl.setInterval}
        />
      </div>

      {/* Filter bar */}
      <Card>
        <CardContent className="pt-0">
          <div className="flex flex-wrap items-end gap-4">
            {/* Butler dropdown */}
            <div className="space-y-1">
              <label className="text-muted-foreground text-xs font-medium">
                Butler
              </label>
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
              <label className="text-muted-foreground text-xs font-medium">
                Status
              </label>
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
              <Button
                variant="ghost"
                size="sm"
                onClick={handleClearFilters}
              >
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

      {/* Pagination controls */}
      {total > 0 && (
        <div className="flex items-center justify-between">
          <p className="text-muted-foreground text-sm">
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

      {/* Session detail drawer */}
      <SessionDetailDrawer
        butler={selectedSessionButler}
        sessionId={selectedSessionId}
        onClose={() => setSelectedSessionId(null)}
      />
    </div>
  );
}
