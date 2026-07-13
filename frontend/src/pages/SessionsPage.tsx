import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";

import type { SessionParams, SessionSummary } from "@/api/types";
import { SessionDetailDrawer } from "@/components/sessions/SessionDetailDrawer";
import { SessionsKpiStrip } from "@/components/sessions/SessionsKpiStrip";
import { SessionsPinnedStrip } from "@/components/sessions/SessionsPinnedStrip";
import { SessionTable } from "@/components/sessions/SessionTable";
import {
  SessionsVerdictOpener,
  SESSIONS_VERDICT_WINDOW_HOURS,
} from "@/components/sessions/SessionsVerdictOpener";
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
import { FetchingDim } from "@/components/ui/fetching-dim";
import { Page } from "@/components/ui/page";
import { useButlers } from "@/hooks/use-butlers";
import { useSessionAggregate, useSessions } from "@/hooks/use-sessions";
import { useRegisterShortcut, type ShortcutBinding } from "@/hooks/use-register-shortcut";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 20;

/** Pinned-strip row caps (bu-ptaub) -- both are small, bounded "strip" sizes,
 * not full-flow substitutes; the caller can always find the rest in the
 * chronological table below. */
const PINNED_RUNNING_LIMIT = 5;
const PINNED_FAILURES_LIMIT = 5;

const STATUS_OPTIONS = [
  { value: "all", label: "All" },
  { value: "success", label: "Success" },
  { value: "failed", label: "Failed" },
  { value: "running", label: "Running" },
] as const;

/** Module-level so Date.now() is not called directly during render (the
 * react-hooks/purity ESLint rule flags impure calls inline in a component/
 * hook body, even inside a useMemo factory). */
function cutoffIsoForWindow(hours: number): string {
  return new Date(Date.now() - hours * 3_600_000).toISOString();
}

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

  // Selection mirrors ?selected= in the URL (bu-qvnce.5 pursuit move 5 slice
  // 4) — shareable/reloadable, and the j/k roving-focus shortcuts below can
  // move it without any local component state.
  const selectedSessionId = searchParams.get("selected");

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
    isFetching,
    isError,
    error,
    refetch,
  } = useSessions(params);
  const sessions = sessionsResponse?.data ?? [];
  const meta = sessionsResponse?.meta;
  const hasMore = meta?.has_more ?? false;
  const nextCursor = meta?.next_cursor ?? null;
  // Butler pools dropped from THIS list page's fan-out (KeysetMeta.sources_degraded,
  // bu-tpudw.2). Consumed below: a note above the table AND gating the empty
  // state, so a degraded partial/zero page never reads as the whole list or a
  // calm "No sessions found" (bu-hmdqz.12). Distinct from the aggregate-level
  // flag the KPI strip / verdict opener already read — this is the list surface.
  const listSourcesDegraded = meta?.sources_degraded ?? [];

  const canGoNewer = cursor != null || prevCursors.length > 0;

  // Verdict opener data — window-scoped failure clustering + nearest running
  // session (bu-y0v0c, JARVIS pursuit move 9 slice 3). The cutoff is memoized
  // once per mount so the aggregate's query key stays stable across renders
  // (a fresh Date.now() every render would key-thrash the query cache).
  const verdictSinceIso = useMemo(
    () => cutoffIsoForWindow(SESSIONS_VERDICT_WINDOW_HOURS),
    [],
  );
  const {
    data: failedAggregateResponse,
    isLoading: failedAggregateLoading,
    isError: failedAggregateError,
  } = useSessionAggregate({
    status: "failed",
    since: verdictSinceIso,
    include_trigger_breakdown: true,
  });
  // Widened from the verdict opener's original limit:1 ("nearest running")
  // to PINNED_RUNNING_LIMIT so the SAME query also feeds the pinned strip
  // below (bu-ptaub) -- one canonical running-sessions fetch, not a parallel
  // one. `runningSessions[0]` remains the verdict opener's "nearest" row.
  const {
    data: runningSessionsResponse,
    isLoading: runningSessionsLoading,
    isError: runningSessionsError,
  } = useSessions({ status: "running", limit: PINNED_RUNNING_LIMIT });

  // Recent-failures pin (bu-ptaub) -- same window as the verdict opener's
  // failure-clustering clause (verdictSinceIso), capped to the pinned strip's
  // small row budget. "Recent" thus means one consistent thing on this page:
  // the last SESSIONS_VERDICT_WINDOW_HOURS, newest-first, capped at
  // PINNED_FAILURES_LIMIT rows.
  const { data: recentFailuresResponse, isError: recentFailuresError } = useSessions({
    status: "failed",
    since: verdictSinceIso,
    limit: PINNED_FAILURES_LIMIT,
  });

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

  function selectSession(id: string | null) {
    // replace: true — j/k rove the list one keypress at a time (see the
    // shortcut bindings below), and closing the drawer also calls this; a
    // default push here would spam one history entry per keypress/close,
    // the same "N Back-clicks to leave the page" defect PR #2928's
    // follow-up (bu-k14bg) fixed for free-text filter inputs.
    setSearchParams(
      (prev) => {
        const sp = new URLSearchParams(prev);
        if (id) sp.set("selected", id);
        else sp.delete("selected");
        return sp;
      },
      { replace: true },
    );
  }

  function handleSessionClick(session: SessionSummary) {
    selectSession(session.id);
  }

  // The failed-window aggregate fans out across butler pools; a pool that
  // dropped is named in meta.sources_degraded (bu-tpudw.2). Non-empty means the
  // "No sessions failed in the last 24h" all-clear would be a half-truth, so we
  // feed it to the verdict opener to suppress + name it (mirrors approvals).
  // ApiMeta is an untyped extensible bag; sources_degraded rides it per the
  // fleet-wide convention (the aggregate endpoint writes ApiMeta(...)).
  const failedSourcesDegraded =
    (failedAggregateResponse?.meta?.sources_degraded as string[] | undefined) ?? [];

  // Pinned-strip pool degradation (bu-hmdqz.12): the running + recent-failures
  // list fetches each fan out across butler pools; a dropped pool undercounts
  // the pinned rows just like the main list, so name it in the strip rather
  // than letting the partial pins read as the full set. Distinct from the
  // query-level `*Error` flags (a whole-request failure) the strip already
  // renders — this is a partial per-pool drop on an otherwise-200 response.
  const runningSourcesDegraded = runningSessionsResponse?.meta?.sources_degraded ?? [];
  const recentFailuresSourcesDegraded = recentFailuresResponse?.meta?.sources_degraded ?? [];

  // -- j/k/[/]/y keyboard loop (bu-qvnce.5, pursuit move 5 slice 4) ----------
  // j/k rove the current page's rows; [ / ] step Older/Newer (matching the
  // reading-order convention: `[` steps deeper into history, `]` steps back
  // toward now); y copies the selected session's id. Migrated onto the
  // shared page-scoped shortcut registry (bu-qvnce.11), same as
  // ApprovalsPage's j/k/a/d/x — not a hand-rolled keydown handler.
  function moveSelection(delta: 1 | -1) {
    if (sessions.length === 0) return;
    const idx = sessions.findIndex((s) => s.id === selectedSessionId);
    const nextIdx =
      idx === -1
        ? delta === 1
          ? 0
          : sessions.length - 1
        : Math.min(Math.max(idx + delta, 0), sessions.length - 1);
    const next = sessions[nextIdx];
    if (next) selectSession(next.id);
  }

  const shortcutBindings = useMemo<ShortcutBinding[]>(() => {
    const bindings: ShortcutBinding[] = [
      { key: "j", display: ["j"], description: "Next session", handler: () => moveSelection(1) },
      { key: "k", display: ["k"], description: "Previous session", handler: () => moveSelection(-1) },
      {
        key: "[",
        display: ["["],
        description: "Older sessions",
        handler: () => goOlder(),
      },
      {
        key: "]",
        display: ["]"],
        description: "Newer sessions",
        handler: () => goNewer(),
      },
    ];
    if (selectedSessionId) {
      bindings.push({
        key: "y",
        display: ["y"],
        description: "Copy selected session ID",
        handler: () => {
          navigator.clipboard.writeText(selectedSessionId).then(() => {
            toast.success("Copied session ID");
          });
        },
      });
    }
    return bindings;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- moveSelection/goOlder/goNewer close over sessions/selectedSessionId/cursor/nextCursor/prevCursors directly; listing the actual referenced values keeps this memo fresh each render without re-deriving the closures.
  }, [sessions, selectedSessionId, cursor, nextCursor, prevCursors]);

  useRegisterShortcut(shortcutBindings);

  return (
    <Page
      archetype="list"
      title="Sessions"
      description="Browse session history across all butlers."
      error={isError ? error : null}
      onRetry={() => refetch()}
      empty={null}
    >
      {/* Verdict opener — window-scoped failure clustering + nearest running
          session, independent of the page's own filters (JARVIS pursuit
          move 9 slice 3). */}
      <div className="border-b border-border/60 px-6 py-3">
        <SessionsVerdictOpener
          failedAggregate={failedAggregateResponse?.data}
          failedLoading={failedAggregateLoading}
          failedError={failedAggregateError}
          failedSourcesDegraded={failedSourcesDegraded}
          runningSessions={runningSessionsResponse?.data ?? []}
          runningLoading={runningSessionsLoading}
          runningError={runningSessionsError}
        />
      </div>

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
              <label htmlFor="sessions-butler-filter" className="text-muted-foreground text-xs font-medium">Butler</label>
              <Select
                value={filters.butler}
                onValueChange={(v) => handleFilterChange("butler", v)}
              >
                <SelectTrigger id="sessions-butler-filter" className="w-44">
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
              <label htmlFor="sessions-status-filter" className="text-muted-foreground text-xs font-medium">Status</label>
              <Select
                value={filters.status}
                onValueChange={(v) => handleFilterChange("status", v)}
              >
                <SelectTrigger id="sessions-status-filter" className="w-32">
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

      {/* Pinned strip — running (ticking elapsed) + recent failures (inline
          error excerpt) surfaced above the chronological flow (bu-ptaub,
          follow-up from bu-86c4c.17 / PR #2875). Reuses handleSessionClick /
          selectedSessionId so a pinned row opens the same drawer and
          participates in the same ?selected= URL mirroring as a table row. */}
      <SessionsPinnedStrip
        runningSessions={runningSessionsResponse?.data ?? []}
        recentFailures={recentFailuresResponse?.data ?? []}
        runningError={runningSessionsError}
        recentFailuresError={recentFailuresError}
        runningSourcesDegraded={runningSourcesDegraded}
        recentFailuresSourcesDegraded={recentFailuresSourcesDegraded}
        onSessionClick={handleSessionClick}
        selectedId={selectedSessionId}
      />

      {/* Session table — dims (never blanks) while a filter/cursor change refetches */}
      <Card>
        <CardContent>
          <FetchingDim isFetching={isFetching && !isLoading}>
            <SessionTable
              sessions={sessions}
              isLoading={isLoading}
              onSessionClick={handleSessionClick}
              onRequestIdClick={handleRequestIdClick}
              showButlerColumn={true}
              selectedId={selectedSessionId}
              sourcesDegraded={listSourcesDegraded}
            />
          </FetchingDim>
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

      {/* Session detail drawer — resolves globally by id (bu-tpudw.2), so a
          pinned row, deep link, or paged-away row opens without a butler hint. */}
      <SessionDetailDrawer
        sessionId={selectedSessionId}
        onClose={() => selectSession(null)}
      />
    </Page>
  );
}
