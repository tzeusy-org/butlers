import { useState } from "react";

import type { AuditLogParams } from "@/api/types";
import AuditLogTable from "@/components/audit/AuditLogTable";
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
import { useAuditLog } from "@/hooks/use-audit-log";
import { useButlers } from "@/hooks/use-butlers";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 20;

const OPERATION_OPTIONS = [
  "all",
  "trigger",
  "tick",
  "session",
  "schedule.create",
  "schedule.update",
  "schedule.delete",
  "schedule.toggle",
  "state.set",
  "state.delete",
] as const;

// ---------------------------------------------------------------------------
// Filter state
// ---------------------------------------------------------------------------

interface FilterState {
  butler: string;
  operation: string;
  since: string;
  until: string;
}

const EMPTY_FILTERS: FilterState = {
  butler: "all",
  operation: "all",
  since: "",
  until: "",
};

// ---------------------------------------------------------------------------
// AuditLogPage
// ---------------------------------------------------------------------------

export default function AuditLogPage() {
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [page, setPage] = useState(0);

  // Fetch butler names for the dropdown
  const { data: butlersResponse } = useButlers();
  const butlerNames = butlersResponse?.data?.map((b) => b.name) ?? [];

  // Build API params from filter state
  const params: AuditLogParams = {
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    ...(filters.butler !== "all" ? { butler: filters.butler } : {}),
    ...(filters.operation !== "all" ? { operation: filters.operation } : {}),
    ...(filters.since ? { since: filters.since } : {}),
    ...(filters.until ? { until: filters.until } : {}),
  };

  const { data: auditResponse, isLoading } = useAuditLog(params);
  const entries = auditResponse?.data ?? [];
  const meta = auditResponse?.meta;
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
    filters.operation !== "all" ||
    filters.since !== "" ||
    filters.until !== "";

  return (
    <div className="space-y-6">
      {/* Page heading */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Audit Log</h1>
        <p className="text-muted-foreground mt-1">
          Browse audit log entries across all butlers.
        </p>
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

            {/* Operation dropdown */}
            <div className="space-y-1">
              <label className="text-muted-foreground text-xs font-medium">
                Operation
              </label>
              <Select
                value={filters.operation}
                onValueChange={(v) => handleFilterChange("operation", v)}
              >
                <SelectTrigger className="w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {OPERATION_OPTIONS.map((op) => (
                    <SelectItem key={op} value={op}>
                      {op === "all" ? "All" : op}
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

      {/* Audit log table */}
      <Card>
        <CardContent>
          <AuditLogTable entries={entries} isLoading={isLoading} />
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
    </div>
  );
}
