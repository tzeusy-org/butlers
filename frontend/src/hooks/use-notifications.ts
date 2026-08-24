/**
 * TanStack Query hooks for the notifications API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  acknowledgeAllFailed,
  escalateNotification,
  getButlerNotifications,
  getNotifications,
  getNotificationStats,
  markNotificationRead,
  retryNotification,
} from "@/api/index.ts";
import type {
  NotificationActionResult,
  NotificationParams,
  NotificationStatsParams,
  NotificationSummary,
} from "@/api/index.ts";
import { useOptimisticListMutation } from "@/hooks/use-optimistic-mutation.ts";
import { useBusAwarePollInterval } from "@/hooks/use-bus-aware-poll-interval";

/** Both query-key namespaces a notification is cached under (the cross-butler feed and the per-butler feed). */
const NOTIFICATION_LIST_PREFIXES = [["notifications"], ["butler-notifications"]] as const;

/**
 * Fetch a paginated list of notifications across all butlers.
 *
 * Bus-covered: event-cache-registry.ts's notificationPatch invalidates this
 * key on every "notification" bus event (see event-cache-manifest.ts).
 * Before bu-01r64.3 this had NO refetchInterval at all -- a dead socket meant
 * infinite staleness, the exact gap the notifications surface was proven to
 * have. useBusAwarePollInterval gives it a bus-aware reconciliation sweep
 * (5 minutes while the bus is connected, a fast fallback while it's down)
 * instead of nothing.
 */
export function useNotifications(params?: NotificationParams) {
  const refetchInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: ["notifications", params],
    queryFn: () => getNotifications(params),
    refetchInterval,
    // Never-blank list (JARVIS audit move 10): keep the previous page/filter's
    // rows visible while the new combination fetches.
    placeholderData: (prev) => prev,
  });
}

/**
 * Fetch aggregate notification statistics.
 *
 * `params` is optional window scoping (`since`/`until`, bu-y0v0c) -- omitted,
 * this is the same all-time rollup every existing caller (NotificationsPage's
 * KPI tiles, the dashboard Overview row) already relies on. Passing a window
 * (e.g. the notifications verdict opener) keys the query on those params so
 * it caches independently of the all-time query.
 */
export function useNotificationStats(params?: NotificationStatsParams) {
  // Bus-covered (see useNotifications above) -- was missing a refetchInterval
  // entirely before bu-01r64.3.
  const refetchInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: ["notification-stats", params],
    queryFn: () => getNotificationStats(params),
    refetchInterval,
  });
}

/** Fetch notifications scoped to a specific butler. */
export function useButlerNotifications(
  name: string,
  params?: NotificationParams,
) {
  // Bus-covered (see useNotifications above) -- was missing a refetchInterval
  // entirely before bu-01r64.3.
  const refetchInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: ["butler-notifications", name, params],
    queryFn: () => getButlerNotifications(name, params),
    enabled: !!name,
    refetchInterval,
    placeholderData: (prev) => prev,
  });
}

/**
 * Mark a single notification as read (ack — OPTIMISTIC: flips `status` to
 * "read" in every cached list immediately, rolls back on error).
 */
export function useMarkNotificationRead() {
  return useOptimisticListMutation<unknown, string, NotificationSummary>({
    mutationFn: (notificationId: string) => markNotificationRead(notificationId),
    listKeyPrefix: NOTIFICATION_LIST_PREFIXES,
    updateItems: (notifications, notificationId) =>
      notifications.map((n) => (n.id === notificationId ? { ...n, status: "read" } : n)),
    invalidateQueryKeys: [...NOTIFICATION_LIST_PREFIXES, ["notification-stats"]],
  });
}

/**
 * Acknowledge all failed notifications in bulk (ack — OPTIMISTIC: flips every
 * cached "failed" notification to "read" immediately, rolls back on error).
 */
export function useAcknowledgeAllFailed() {
  return useOptimisticListMutation<unknown, void, NotificationSummary>({
    mutationFn: () => acknowledgeAllFailed(),
    listKeyPrefix: NOTIFICATION_LIST_PREFIXES,
    updateItems: (notifications) =>
      notifications.map((n) => (n.status === "failed" ? { ...n, status: "read" } : n)),
    invalidateQueryKeys: [...NOTIFICATION_LIST_PREFIXES, ["notification-stats"]],
  });
}

/**
 * Manually re-attempt delivery of a failed notification, right now, on the
 * same channel (HONEST-PENDING: this triggers a real, possibly-slow send —
 * no optimistic "sent" before the backend confirms it). Invalidates the list
 * and stats caches on settle so both the retried row's new "retried" chip
 * and the new attempt's own row appear.
 */
export function useRetryNotification() {
  const queryClient = useQueryClient();
  return useMutation<NotificationActionResult, unknown, string>({
    mutationFn: async (notificationId: string) => {
      const resp = await retryNotification(notificationId);
      return resp.data;
    },
    onSettled: () => {
      for (const prefix of NOTIFICATION_LIST_PREFIXES) {
        void queryClient.invalidateQueries({ queryKey: prefix });
      }
      void queryClient.invalidateQueries({ queryKey: ["notification-stats"] });
    },
  });
}

/**
 * Re-attempt a failed notification on the owner's alternate channel
 * (telegram<->email). Same HONEST-PENDING/invalidate-on-settle contract as
 * {@link useRetryNotification}.
 */
export function useEscalateNotification() {
  const queryClient = useQueryClient();
  return useMutation<NotificationActionResult, unknown, string>({
    mutationFn: async (notificationId: string) => {
      const resp = await escalateNotification(notificationId);
      return resp.data;
    },
    onSettled: () => {
      for (const prefix of NOTIFICATION_LIST_PREFIXES) {
        void queryClient.invalidateQueries({ queryKey: prefix });
      }
      void queryClient.invalidateQueries({ queryKey: ["notification-stats"] });
    },
  });
}
