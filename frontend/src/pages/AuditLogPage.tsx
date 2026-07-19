import { useState } from "react";
import { useSearchParams } from "react-router";

import type { AuditLogParams } from "@/api/types";
import AuditLogTable from "@/components/audit/AuditLogTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { FetchingDim } from "@/components/ui/fetching-dim";
import { Input } from "@/components/ui/input";
import { Page } from "@/components/ui/page";
import { useAuditLog } from "@/hooks/use-audit-log";
import { useDebounce } from "@/hooks/use-debounce";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 20;
const FREE_TEXT_DEBOUNCE_MS = 300;

// ---------------------------------------------------------------------------
// Filter state
// ---------------------------------------------------------------------------

interface FilterState {
  actor: string;
  action: string;
  since: string;
  from_date: string;
  to_date: string;
}

/**
 * Read filter-bar state directly out of the URL (bu-qvnce.13) — actor/
 * action/date filters have exactly ONE source of truth, the querystring. Previously
 * `actor` also lived in a component-state mirror that a `?actor=` deep-link
 * silently overrode without updating (the bug: the visible input and the
 * request params could disagree). Collapsing to a single URL-serialized
 * state makes that impossible: the input's `value` and the params sent to
 * `useAuditLog` are read from the exact same place.
 */
function filtersFromSearchParams(searchParams: URLSearchParams): FilterState {
  return {
    actor: searchParams.get("actor") ?? "",
    action: searchParams.get("action") ?? "",
    since: searchParams.get("since") ?? "",
    from_date: searchParams.get("from_date") ?? "",
    to_date: searchParams.get("to_date") ?? "",
  };
}

// ---------------------------------------------------------------------------
// AuditLogPage
// ---------------------------------------------------------------------------

export default function AuditLogPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [page, setPage] = useState(0);

  // Single URL-serialized filter state (bu-qvnce.13) — no local mirror.
  const filters = filtersFromSearchParams(searchParams);
  // The inputs still reflect the URL immediately; debounce only the request
  // predicates so a typed actor/action does not continuously re-query.
  const debouncedActor = useDebounce(filters.actor, FREE_TEXT_DEBOUNCE_MS);
  const debouncedAction = useDebounce(filters.action, FREE_TEXT_DEBOUNCE_MS);
  const hasPendingFreeTextFilter =
    debouncedActor !== filters.actor || debouncedAction !== filters.action;

  // `key` and `result` are also URL-only deep-link params (no filter-bar
  // input owns them), forwarded straight through.
  const keyFilter = searchParams.get("key") ?? undefined;
  const resultFilter = searchParams.get("result") ?? undefined;

  // Noise toggle (JARVIS audit move 6): the page defaults to kind=privileged
  // (mutation/security rows only, backend predicate already existed) --
  // ?noise=all opts OUT of that default to show every row including
  // heartbeats and routine GETs. This is a distinct URL param from `kind` (not
  // its literal value) so "default privileged" and "explicit all" are
  // unambiguous regardless of whether a link happens to carry `?kind=privileged`.
  const showAllNoise = searchParams.get("noise") === "all";

  function handleToggleNoise() {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (showAllNoise) {
        next.delete("noise");
      } else {
        next.set("noise", "all");
      }
      return next;
    });
    setPage(0);
  }

  // Build API params from filter state. `filters.actor` is the single source
  // of truth (URL) now — no separate deep-link override needed.
  const params: AuditLogParams = {
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    ...(debouncedActor ? { actor: debouncedActor } : {}),
    ...(debouncedAction ? { action: debouncedAction } : {}),
    ...(filters.since ? { since: filters.since } : {}),
    ...(filters.from_date ? { from_date: filters.from_date } : {}),
    ...(filters.to_date ? { to_date: filters.to_date } : {}),
    ...(keyFilter ? { key: keyFilter } : {}),
    ...(resultFilter ? { result: resultFilter } : {}),
    ...(showAllNoise ? {} : { kind: "privileged" }),
  };

  const { data: auditResponse, isLoading, isFetching, isError, error } = useAuditLog(params);
  const entries = auditResponse?.data ?? [];
  const meta = auditResponse?.meta;
  const total = meta?.total ?? 0;
  const hasMore = meta?.has_more ?? false;
  const isListRefreshing = !isLoading && (isFetching || hasPendingFreeTextFilter);

  // Pagination helpers
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = page + 1;

  function handleFilterChange(key: keyof FilterState, value: string) {
    // replace: true — actor/action are free-text inputs; without this, every
    // keystroke pushes a new history entry, so Back would have to be clicked
    // once per character typed instead of once to leave the page.
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );
    setPage(0);
  }

  function handleClearFilters() {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("actor");
      next.delete("action");
      next.delete("since");
      next.delete("from_date");
      next.delete("to_date");
      return next;
    });
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

  // Clearing the actor deep-link chip and clearing the filter-bar actor input
  // are now the same action (bu-qvnce.13 — there is only one actor state).
  function handleClearActorFilter() {
    handleFilterChange("actor", "");
  }

  const hasActiveFilters =
    filters.actor !== "" ||
    filters.action !== "" ||
    filters.since !== "" ||
    filters.from_date !== "" ||
    filters.to_date !== "";

  return (
    <Page
      archetype="list"
      title="Audit Log"
      description="Browse audit log entries across all butlers."
    >
      {/* Deep-link filter chips — shown when ?key= or ?actor= are present */}
      {(keyFilter || filters.actor) && (
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
          {filters.actor && (
            <Badge
              variant="secondary"
              className="gap-1.5 py-1 pl-2.5 pr-1.5 text-xs"
              data-testid="actor-filter-chip"
            >
              actor: {filters.actor}
              <button
                type="button"
                aria-label={`Remove actor filter ${filters.actor}`}
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

            {/* Owner-timezone day bounds */}
            <div className="space-y-1">
              <label
                htmlFor="filter-from-date"
                className="text-muted-foreground text-xs font-medium"
              >
                From
              </label>
              <Input
                id="filter-from-date"
                type="date"
                value={filters.from_date}
                onChange={(e) => handleFilterChange("from_date", e.target.value)}
                className="w-40"
              />
            </div>

            <div className="space-y-1">
              <label
                htmlFor="filter-to-date"
                className="text-muted-foreground text-xs font-medium"
              >
                To
              </label>
              <Input
                id="filter-to-date"
                type="date"
                value={filters.to_date}
                onChange={(e) => handleFilterChange("to_date", e.target.value)}
                className="w-40"
              />
            </div>

            {/* Noise toggle (JARVIS audit move 6): defaults to privileged-only
                (mutation/security rows) -- this opts into every row including
                heartbeats and routine GETs. */}
            <div className="space-y-1">
              <span className="text-muted-foreground text-xs font-medium block">Noise</span>
              <Button
                variant="outline"
                size="sm"
                onClick={handleToggleNoise}
                data-testid="noise-toggle"
                aria-pressed={showAllNoise}
              >
                {showAllNoise ? "Showing all activity" : "Privileged only"}
              </Button>
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

      {/* Audit log table — dims (never blanks) while a filter/page change refetches */}
      <Card>
        <CardContent>
          <FetchingDim isFetching={isListRefreshing}>
            <AuditLogTable entries={entries} isLoading={isLoading} isError={isError} error={error} />
          </FetchingDim>
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
