import { useState } from "react";
import { useSearchParams } from "react-router";

import type { AuditLogParams } from "@/api/types";
import AuditLogTable from "@/components/audit/AuditLogTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Page } from "@/components/ui/page";
import { useAuditLog } from "@/hooks/use-audit-log";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 20;

// ---------------------------------------------------------------------------
// Filter state
// ---------------------------------------------------------------------------

interface FilterState {
  actor: string;
  action: string;
  since: string;
}

const EMPTY_FILTERS: FilterState = {
  actor: "",
  action: "",
  since: "",
};

function filtersFromSearchParams(searchParams: URLSearchParams): FilterState {
  return {
    // Hydrate the actor filter bar from the ?actor= deep-link so the input
    // reflects the active actor filter (e.g. arriving from a passport link).
    actor: searchParams.get("actor") ?? "",
    action: searchParams.get("action") ?? "",
    since: searchParams.get("since") ?? "",
  };
}

// ---------------------------------------------------------------------------
// AuditLogPage
// ---------------------------------------------------------------------------

export default function AuditLogPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [filters, setFilters] = useState<FilterState>(() =>
    filtersFromSearchParams(searchParams),
  );
  const [page, setPage] = useState(0);

  // Deep-link filters from URL: ?key= and ?actor= are read directly from URL
  // and forwarded to the backend. They are not part of the mutable FilterState
  // because they originate from passport deep-links, not the filter bar UI.
  const keyFilter = searchParams.get("key") ?? undefined;
  const actorFilter = searchParams.get("actor") ?? undefined;

  // Build API params from filter state
  const params: AuditLogParams = {
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    ...(filters.actor ? { actor: filters.actor } : {}),
    ...(filters.action ? { action: filters.action } : {}),
    ...(filters.since ? { since: filters.since } : {}),
    ...(keyFilter ? { key: keyFilter } : {}),
    // ?actor= deep-link overrides the filter-bar actor when present
    ...(actorFilter ? { actor: actorFilter } : {}),
  };

  const { data: auditResponse, isLoading, isError } = useAuditLog(params);
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

  function handleClearKeyFilter() {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("key");
      return next;
    });
    setPage(0);
  }

  function handleClearActorFilter() {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("actor");
      return next;
    });
    setPage(0);
  }

  const hasActiveFilters =
    filters.actor !== "" ||
    filters.action !== "" ||
    filters.since !== "";

  return (
    <Page
      archetype="list"
      title="Audit Log"
      description="Browse audit log entries across all butlers."
    >
      {/* Deep-link filter chips — shown when ?key= or ?actor= are present */}
      {(keyFilter || actorFilter) && (
        <div className="flex flex-wrap items-center gap-2" data-testid="deep-link-filters">
          {keyFilter && (
            <Badge
              variant="secondary"
              className="gap-1.5 py-1 pl-2.5 pr-1.5 text-xs"
              data-testid="key-filter-chip"
            >
              key: {keyFilter}
              <button
                type="button"
                aria-label={`Remove key filter ${keyFilter}`}
                className="hover:text-foreground text-muted-foreground ml-0.5 rounded-sm text-xs leading-none"
                onClick={handleClearKeyFilter}
              >
                &times;
              </button>
            </Badge>
          )}
          {actorFilter && (
            <Badge
              variant="secondary"
              className="gap-1.5 py-1 pl-2.5 pr-1.5 text-xs"
              data-testid="actor-filter-chip"
            >
              actor: {actorFilter}
              <button
                type="button"
                aria-label={`Remove actor filter ${actorFilter}`}
                className="hover:text-foreground text-muted-foreground ml-0.5 rounded-sm text-xs leading-none"
                onClick={handleClearActorFilter}
              >
                &times;
              </button>
            </Badge>
          )}
        </div>
      )}

      {/* Filter bar */}
      <Card>
        <CardContent className="pt-0">
          <div className="flex flex-wrap items-end gap-4">
            {/* Actor text input */}
            <div className="space-y-1">
              <label
                htmlFor="filter-actor"
                className="text-muted-foreground text-xs font-medium"
              >
                Actor
              </label>
              <Input
                id="filter-actor"
                type="text"
                placeholder="e.g. owner"
                value={filters.actor}
                onChange={(e) => handleFilterChange("actor", e.target.value)}
                className="w-40"
              />
            </div>

            {/* Action text input */}
            <div className="space-y-1">
              <label
                htmlFor="filter-action"
                className="text-muted-foreground text-xs font-medium"
              >
                Action
              </label>
              <Input
                id="filter-action"
                type="text"
                placeholder="e.g. model.priority"
                value={filters.action}
                onChange={(e) => handleFilterChange("action", e.target.value)}
                className="w-48"
              />
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
          <AuditLogTable entries={entries} isLoading={isLoading} isError={isError} />
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
    </Page>
  );
}
