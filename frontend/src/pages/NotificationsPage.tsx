import { useCallback, useState } from "react";

import type { NotificationParams } from "@/api/types";
import { NotificationFeed } from "@/components/notifications/notification-feed";
import { NotificationStatsBar } from "@/components/notifications/notification-stats-bar";
import { NotificationTableSkeleton } from "@/components/skeletons";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Page } from "@/components/ui/page";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useAcknowledgeAllFailed,
  useMarkNotificationRead,
  useNotifications,
  useNotificationStats,
} from "@/hooks/use-notifications";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 20;

const CHANNEL_OPTIONS = [
  { value: "all", label: "All channels" },
  { value: "telegram", label: "Telegram" },
  { value: "email", label: "Email" },
] as const;

// Exported for tests: the status filter must surface read/retried so those rows
// are not hidden from the review-the-stream view (bu-5gf99).
export const STATUS_OPTIONS = [
  { value: "all", label: "All statuses" },
  { value: "sent", label: "Sent" },
  { value: "failed", label: "Failed" },
  { value: "pending", label: "Pending" },
  { value: "read", label: "Read" },
  { value: "retried", label: "Retried" },
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

// ---------------------------------------------------------------------------
// NotificationsPage
// ---------------------------------------------------------------------------

export default function NotificationsPage() {
  // Filter state
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [page, setPage] = useState(0);
  // Track which notification IDs are pending individual acks for UX feedback
  const [pendingAckIds, setPendingAckIds] = useState<Set<string>>(new Set());

  // Build API params from filter state
  const params: NotificationParams = {
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    ...(filters.butler ? { butler: filters.butler } : {}),
    ...(filters.channel !== "all" ? { channel: filters.channel } : {}),
    ...(filters.status !== "all" ? { status: filters.status } : {}),
    ...(filters.since ? { since: filters.since } : {}),
    ...(filters.until ? { until: filters.until } : {}),
  };

  // Data hooks
  const { data: statsResponse, isLoading: statsLoading } =
    useNotificationStats();
  const {
    data: notificationsResponse,
    isLoading: notificationsLoading,
    isError: notificationsError,
  } = useNotifications(params);

  // Mutation hooks
  const markReadMutation = useMarkNotificationRead();
  const ackAllMutation = useAcknowledgeAllFailed();

  const notifications = notificationsResponse?.data ?? [];
  const meta = notificationsResponse?.meta;
  const total = meta?.total ?? 0;
  // has_more is a computed property on the backend; derive it client-side as a
  // fallback in case the backend serialization omits it.
  const hasMore = meta?.has_more ?? (total > 0 && page * PAGE_SIZE + PAGE_SIZE < total);

  // Pagination helpers
  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  function handleFilterChange(key: keyof FilterState, value: string) {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(0); // Reset to first page when filters change
  }

  function handleClearFilters() {
    setFilters(EMPTY_FILTERS);
    setPage(0);
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
      markReadMutation.mutate(notificationId, {
        onSettled: () => {
          setPendingAckIds((prev) => {
            const next = new Set(prev);
            next.delete(notificationId);
            return next;
          });
        },
      });
    },
    [markReadMutation],
  );

  // Dismiss is semantically identical to mark-read: the backend exposes a single
  // PATCH /{id}/read endpoint that sets status='read' for any status, so both
  // affordances route through the same mutation.
  const handleDismiss = handleMarkRead;

  const handleAcknowledgeAll = useCallback(() => {
    ackAllMutation.mutate();
  }, [ackAllMutation]);

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
      {/* Stats bar */}
      <NotificationStatsBar
        stats={statsResponse?.data}
        isLoading={statsLoading}
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
              <label className="text-muted-foreground text-xs font-medium">
                Channel
              </label>
              <Select
                value={filters.channel}
                onValueChange={(v) => handleFilterChange("channel", v)}
              >
                <SelectTrigger className="w-40">
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
              <label className="text-muted-foreground text-xs font-medium">
                Status
              </label>
              <Select
                value={filters.status}
                onValueChange={(v) => handleFilterChange("status", v)}
              >
                <SelectTrigger className="w-40">
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
                Since
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
                Until
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

      {/* Notification feed */}
      <Card>
        <CardContent>
          {notificationsLoading ? (
            <NotificationTableSkeleton rows={10} />
          ) : notificationsError ? (
            <p className="text-destructive py-8 text-center text-sm">
              Failed to load notifications. Please try refreshing the page.
            </p>
          ) : (
            <NotificationFeed
              notifications={notifications}
              isLoading={false}
              hasActiveFilters={hasActiveFilters}
              onMarkRead={handleMarkRead}
              onDismiss={handleDismiss}
              pendingAckIds={pendingAckIds}
            />
          )}
        </CardContent>
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
