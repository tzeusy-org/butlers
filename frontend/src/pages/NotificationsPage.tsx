import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import type { NotificationParams } from "@/api/types";
import { NotificationFeed } from "@/components/notifications/notification-feed";
import { NotificationStatsBar } from "@/components/notifications/notification-stats-bar";
import {
  NotificationsVerdictOpener,
  NOTIFICATIONS_VERDICT_WINDOW_HOURS,
} from "@/components/notifications/notifications-verdict-opener";
import { NotificationTableSkeleton } from "@/components/skeletons";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { FetchingDim } from "@/components/ui/fetching-dim";
import { Input } from "@/components/ui/input";
import { ListTriageFooterHint } from "@/components/ui/list-triage-footer";
import { Page } from "@/components/ui/page";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useListTriage, type ListTriageVerb } from "@/hooks/use-list-triage";
import { useDebounce } from "@/hooks/use-debounce";
import {
  useAcknowledgeAllFailed,
  useEscalateNotification,
  useMarkNotificationRead,
  useNotifications,
  useNotificationStats,
  useRetryNotification,
} from "@/hooks/use-notifications";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 20;
const FREE_TEXT_DEBOUNCE_MS = 300;

/** Module-level so Date.now() is not called directly during render (the
 * react-hooks/purity ESLint rule flags impure calls inline in a component/
 * hook body, even inside a useMemo factory). */
function closedWindowForHours(hours: number): { since: string; until: string } {
  // The verdict door lands in datetime-local controls, which deliberately
  // represent minutes. Capture both ends at that same precision so the query,
  // link, and visible filters all describe one closed interval.
  const untilMs = Math.floor(Date.now() / 60_000) * 60_000;
  const until = new Date(untilMs);
  return {
    since: new Date(untilMs - hours * 3_600_000).toISOString(),
    until: until.toISOString(),
  };
}

const CHANNEL_OPTIONS = [
  { value: "all", label: "All channels" },
  { value: "telegram", label: "Telegram" },
  { value: "email", label: "Email" },
] as const;

// Exported for tests: the status filter must surface terminal failures/read/
// retried so the dashboard's count predicate and review stream stay explicit.
export const STATUS_OPTIONS = [
  { value: "all", label: "All statuses" },
  { value: "sent", label: "Sent" },
  { value: "failed", label: "Failed" },
  { value: "terminal_failed", label: "Terminal failures" },
  { value: "pending", label: "Pending" },
  { value: "read", label: "Read" },
  { value: "retried", label: "Retried" },
  { value: "escalated", label: "Escalated" },
] as const;

// ---------------------------------------------------------------------------
// Filter state
// ---------------------------------------------------------------------------

interface FilterState {
  butler: string;
  channel: string;
  status: string;
  since: string;
  until: string;
}

const EMPTY_FILTERS: FilterState = {
  butler: "",
  channel: "all",
  status: "all",
  since: "",
  until: "",
};

/** Parse filter state out of the querystring (URL is the source of truth,
 * bu-qvnce.13 — makes the notifications view shareable/reloadable and lets
 * inbound links carry a predicate, e.g. `?status=failed`). */
function parseFilters(sp: URLSearchParams): FilterState {
  return {
    butler: sp.get("butler") ?? "",
    channel: sp.get("channel") ?? "all",
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
  set("butler", f.butler, "");
  set("channel", f.channel, "all");
  set("status", f.status, "all");
  set("since", f.since, "");
  set("until", f.until, "");
}

/** Translate URL/API timestamps into the browser's local datetime input form. */
function dateTimeLocalValue(timestamp: string): string {
  if (!timestamp) return "";
  // Keep existing date-only deep links on their literal calendar day instead
  // of shifting them backward/forward when interpreted as UTC.
  if (/^\d{4}-\d{2}-\d{2}$/.test(timestamp)) return `${timestamp}T00:00`;

  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return "";
  const localTime = new Date(
    parsed.getTime() - parsed.getTimezoneOffset() * 60_000,
  );
  return localTime.toISOString().slice(0, 16);
}

/** Serialize a local datetime-control change as the API's unambiguous ISO timestamp. */
function queryTimestamp(value: string): string {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toISOString();
}

// ---------------------------------------------------------------------------
// NotificationsPage
// ---------------------------------------------------------------------------

export default function NotificationsPage() {
  // Filter + page state — URL-backed (bu-qvnce.13): no local mirror, so a
  // deep-link (e.g. from the dashboard's "N failed notifications" tile) and
  // the visible filter bar can never disagree.
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = parseFilters(searchParams);
  const page = Number.parseInt(searchParams.get("page") ?? "0", 10) || 0;
  // Preserve the URL as the visible/shareable source of truth while avoiding
  // a network request for every keystroke in the butler filter.
  const debouncedButler = useDebounce(filters.butler, FREE_TEXT_DEBOUNCE_MS);
  const hasPendingButlerFilter = debouncedButler !== filters.butler;
  // Track which notification IDs are pending individual acks for UX feedback
  const [pendingAckIds, setPendingAckIds] = useState<Set<string>>(new Set());
  const [pendingRetryIds, setPendingRetryIds] = useState<Set<string>>(new Set());
  const [pendingEscalateIds, setPendingEscalateIds] = useState<Set<string>>(
    new Set(),
  );

  // Build API params from filter state
  const params: NotificationParams = {
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    ...(debouncedButler ? { butler: debouncedButler } : {}),
    ...(filters.channel !== "all" ? { channel: filters.channel } : {}),
    ...(filters.status !== "all" ? { status: filters.status } : {}),
    ...(filters.since ? { since: filters.since } : {}),
    ...(filters.until ? { until: filters.until } : {}),
  };

  // Data hooks
  const { data: statsResponse, isLoading: statsLoading } =
    useNotificationStats();
  // Verdict opener's own windowed stats (bu-y0v0c, JARVIS pursuit move 9
  // slice 3) -- a separate query, keyed on its own closed interval, so it
  // caches independently of the all-time stats bar above. The interval is
  // memoized once per mount so its predicate-carrying doors name the exact
  // set counted by this response instead of re-evaluating "last 24h" on click.
  const verdictWindow = useMemo(
    () => closedWindowForHours(NOTIFICATIONS_VERDICT_WINDOW_HOURS),
    [],
  );
  const {
    data: verdictStatsResponse,
    isLoading: verdictStatsLoading,
    isError: verdictStatsError,
  } = useNotificationStats(verdictWindow);
  const {
    data: notificationsResponse,
    isLoading: notificationsLoading,
    isFetching: notificationsFetching,
    isError: notificationsError,
  } = useNotifications(params);
  const isNotificationsRefreshing =
    !notificationsLoading && (notificationsFetching || hasPendingButlerFilter);

  // Mutation hooks
  const markReadMutation = useMarkNotificationRead();
  const ackAllMutation = useAcknowledgeAllFailed();
  const retryMutation = useRetryNotification();
  const escalateMutation = useEscalateNotification();
  const markRead = markReadMutation.mutate;
  const acknowledgeAll = ackAllMutation.mutate;
  const retry = retryMutation.mutate;
  const escalate = escalateMutation.mutate;

  const notifications = useMemo(
    () => notificationsResponse?.data ?? [],
    [notificationsResponse],
  );
  const meta = notificationsResponse?.meta;
  const total = meta?.total ?? 0;
  // has_more is a computed property on the backend; derive it client-side as a
  // fallback in case the backend serialization omits it.
  const hasMore =
    meta?.has_more ?? (total > 0 && page * PAGE_SIZE + PAGE_SIZE < total);

  // Pagination helpers
  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  function handleFilterChange(key: keyof FilterState, value: string) {
    // replace: true — the butler filter is a free-text input; without this,
    // every keystroke pushes a new history entry, so Back would have to be
    // clicked once per character typed instead of once to leave the page.
    setSearchParams(
      (prev) => {
        const sp = new URLSearchParams(prev);
        applyFilters(sp, { ...parseFilters(prev), [key]: value });
        sp.delete("page"); // Reset to first page when filters change
        return sp;
      },
      { replace: true },
    );
  }

  function handleClearFilters() {
    setSearchParams((prev) => {
      const sp = new URLSearchParams(prev);
      applyFilters(sp, EMPTY_FILTERS);
      sp.delete("page");
      return sp;
    });
  }

  function handlePageChange(next: number) {
    setSearchParams((prev) => {
      const sp = new URLSearchParams(prev);
      if (next > 0) sp.set("page", String(next));
      else sp.delete("page");
      return sp;
    });
  }

  const hasActiveFilters =
    filters.butler !== "" ||
    filters.channel !== "all" ||
    filters.status !== "all" ||
    filters.since !== "" ||
    filters.until !== "";

  const handleMarkRead = useCallback(
    (notificationId: string) => {
      setPendingAckIds((prev) => new Set(prev).add(notificationId));
      markRead(notificationId, {
        onSettled: () => {
          setPendingAckIds((prev) => {
            const next = new Set(prev);
            next.delete(notificationId);
            return next;
          });
        },
      });
    },
    [markRead],
  );

  // Dismiss is semantically identical to mark-read: the backend exposes a single
  // PATCH /{id}/read endpoint that sets status='read' for any status, so both
  // affordances route through the same mutation.
  const handleDismiss = handleMarkRead;

  const handleRetry = useCallback(
    (notificationId: string) => {
      setPendingRetryIds((prev) => new Set(prev).add(notificationId));
      retry(notificationId, {
        onSettled: () => {
          setPendingRetryIds((prev) => {
            const next = new Set(prev);
            next.delete(notificationId);
            return next;
          });
        },
      });
    },
    [retry],
  );

  const handleEscalate = useCallback(
    (notificationId: string) => {
      setPendingEscalateIds((prev) => new Set(prev).add(notificationId));
      escalate(notificationId, {
        onSettled: () => {
          setPendingEscalateIds((prev) => {
            const next = new Set(prev);
            next.delete(notificationId);
            return next;
          });
        },
      });
    },
    [escalate],
  );

  const handleAcknowledgeAll = useCallback(() => {
    acknowledgeAll();
  }, [acknowledgeAll]);

  // j/k roving selection + a=mark-read act key over the notification rows
  // (bu-qvnce.11 slice 4 -- useListTriage, extracted from ApprovalsPage's own
  // former hand-rolled version of this exact pattern). Selection is
  // ephemeral component state, not URL-backed (bu-qvnce.13's filters/page own
  // the URL; a row cursor is not shareable state the way a filter is).
  const [selectedNotificationId, setSelectedNotificationId] = useState<
    string | null
  >(null);
  const notificationIds = useMemo(
    () => notifications.map((n) => n.id),
    [notifications],
  );
  const notificationVerbs = useMemo<ListTriageVerb[]>(() => {
    const notification = notifications.find(
      (n) => n.id === selectedNotificationId,
    );
    if (!notification) return [];
    // Mirrors NotificationFeed's own gating: an already-read row has nothing
    // left to triage, so no act key is offered for it.
    const displayStatus = notification.effective_status ?? notification.status;
    if (displayStatus === "read") return [];
    return [
      {
        key: "a",
        description: "Mark read",
        handler: () => handleMarkRead(notification.id),
        command: {
          id: "mark-selected-notification-read",
          label: "Mark selected notification read",
          keywords: ["notification", "read"],
        },
      },
    ];
  }, [notifications, selectedNotificationId, handleMarkRead]);
  const { hints: notificationTriageHints } = useListTriage({
    ids: notificationIds,
    selectedId: selectedNotificationId,
    onSelect: setSelectedNotificationId,
    verbs: notificationVerbs,
  });

  // Keep DOM focus in sync with the current selection, mirroring
  // ApprovalsPage's identical rail-focus effect.
  useEffect(() => {
    if (!selectedNotificationId) return;
    const nodes = document.querySelectorAll<HTMLElement>(
      '[data-testid="notification-row"]',
    );
    for (const node of nodes) {
      if (
        node.getAttribute("data-notification-id") === selectedNotificationId
      ) {
        node.focus({ preventScroll: true });
        break;
      }
    }
  }, [selectedNotificationId]);

  // Compute whether there are any failed notifications in the stats
  const failedCount = statsResponse?.data?.failed ?? 0;

  return (
    <Page
      archetype="list"
      title="Notifications"
      description="Monitor notification delivery across all butlers."
      actions={
        failedCount > 0 ? (
          <Button
            variant="outline"
            size="sm"
            disabled={ackAllMutation.isPending}
            onClick={handleAcknowledgeAll}
          >
            {ackAllMutation.isPending
              ? "Acknowledging…"
              : `Acknowledge all failed (${failedCount})`}
          </Button>
        ) : undefined
      }
    >
      {/* Verdict opener — windowed failed-notification count + dominant
          butler, composed from by_butler (fetched but discarded until now,
          JARVIS pursuit move 9 slice 3). */}
      <div className="border-b border-border/60 px-6 py-3">
        <NotificationsVerdictOpener
          stats={verdictStatsResponse?.data}
          isLoading={verdictStatsLoading}
          isError={verdictStatsError}
          since={verdictWindow.since}
          until={verdictWindow.until}
        />
      </div>

      {/* Stats bar — Sent/Failed tiles are filter anchors (bu-qvnce.13): click
          one to pivot the filter bar to that status without leaving the page. */}
      <NotificationStatsBar
        stats={statsResponse?.data}
        isLoading={statsLoading}
        onFilterClick={(status) => handleFilterChange("status", status)}
      />

      {/* Filter bar */}
      <Card>
        <CardContent className="pt-0">
          <div className="flex flex-wrap items-end gap-4">
            {/* Butler name */}
            <div className="space-y-1">
              <label
                htmlFor="filter-butler"
                className="text-muted-foreground text-xs font-medium"
              >
                Butler
              </label>
              <Input
                id="filter-butler"
                placeholder="Filter by butler..."
                value={filters.butler}
                onChange={(e) => handleFilterChange("butler", e.target.value)}
                className="w-44"
              />
            </div>

            {/* Channel dropdown */}
            <div className="space-y-1">
              <label
                htmlFor="notifications-channel-filter"
                className="text-muted-foreground text-xs font-medium"
              >
                Channel
              </label>
              <Select
                value={filters.channel}
                onValueChange={(v) => handleFilterChange("channel", v)}
              >
                <SelectTrigger
                  id="notifications-channel-filter"
                  className="w-40"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CHANNEL_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Status dropdown */}
            <div className="space-y-1">
              <label
                htmlFor="notifications-status-filter"
                className="text-muted-foreground text-xs font-medium"
              >
                Status
              </label>
              <Select
                value={filters.status}
                onValueChange={(v) => handleFilterChange("status", v)}
              >
                <SelectTrigger
                  id="notifications-status-filter"
                  className="w-40"
                >
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

            {/* Since date and time */}
            <div className="space-y-1">
              <label
                htmlFor="filter-since"
                className="text-muted-foreground text-xs font-medium"
              >
                Since (local time)
              </label>
              <Input
                id="filter-since"
                type="datetime-local"
                value={dateTimeLocalValue(filters.since)}
                onChange={(e) =>
                  handleFilterChange("since", queryTimestamp(e.target.value))
                }
                className="w-52"
              />
            </div>

            {/* Until date and time */}
            <div className="space-y-1">
              <label
                htmlFor="filter-until"
                className="text-muted-foreground text-xs font-medium"
              >
                Until (local time)
              </label>
              <Input
                id="filter-until"
                type="datetime-local"
                value={dateTimeLocalValue(filters.until)}
                onChange={(e) =>
                  handleFilterChange("until", queryTimestamp(e.target.value))
                }
                className="w-52"
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

      {/* Notification feed — dims (never blanks) while a filter/page change refetches */}
      <Card>
        <CardContent>
          {notificationsLoading ? (
            <NotificationTableSkeleton rows={10} hasTriageControls />
          ) : notificationsError ? (
            <p className="text-destructive py-8 text-center text-sm">
              Failed to load notifications. Please try refreshing the page.
            </p>
          ) : (
            <FetchingDim isFetching={isNotificationsRefreshing}>
              <NotificationFeed
                notifications={notifications}
                isLoading={false}
                hasActiveFilters={hasActiveFilters}
                onMarkRead={handleMarkRead}
                onDismiss={handleDismiss}
                onRetry={handleRetry}
                onEscalate={handleEscalate}
                pendingAckIds={pendingAckIds}
                pendingRetryIds={pendingRetryIds}
                pendingEscalateIds={pendingEscalateIds}
                selectedId={selectedNotificationId}
                sourceUnavailable={
                  notificationsResponse?.source_available === false
                }
              />
            </FetchingDim>
          )}
        </CardContent>
        {/* Shared footer hint strip (bu-qvnce.11 slice 4) -- advertises the
            EXACT j/k/a bindings useListTriage just registered. */}
        <ListTriageFooterHint bindings={notificationTriageHints} />
      </Card>

      {/* Pagination */}
      {total > 0 && (
        <div className="flex items-center justify-between">
          <p className="text-muted-foreground text-sm">
            Showing {rangeStart}–{rangeEnd} of {total.toLocaleString()}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => handlePageChange(Math.max(0, page - 1))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!hasMore}
              onClick={() => handlePageChange(page + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </Page>
  );
}
