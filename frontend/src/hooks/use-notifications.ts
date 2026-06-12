/**
 * TanStack Query hooks for the notifications API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  acknowledgeAllFailed,
  getButlerNotifications,
  getNotifications,
  getNotificationStats,
  markNotificationRead,
} from "@/api/index.ts";
import type { NotificationParams } from "@/api/index.ts";

/** Fetch a paginated list of notifications across all butlers. */
export function useNotifications(params?: NotificationParams) {
  return useQuery({
    queryKey: ["notifications", params],
    queryFn: () => getNotifications(params),
  });
}

/** Fetch aggregate notification statistics. */
export function useNotificationStats() {
  return useQuery({
    queryKey: ["notification-stats"],
    queryFn: () => getNotificationStats(),
  });
}

/** Fetch notifications scoped to a specific butler. */
export function useButlerNotifications(
  name: string,
  params?: NotificationParams,
) {
  return useQuery({
    queryKey: ["butler-notifications", name, params],
    queryFn: () => getButlerNotifications(name, params),
    enabled: !!name,
  });
}

/** Mark a single notification as read. Invalidates the notifications list and stats. */
export function useMarkNotificationRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (notificationId: string) => markNotificationRead(notificationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
      queryClient.invalidateQueries({ queryKey: ["notification-stats"] });
    },
  });
}

/** Acknowledge all failed notifications in bulk. Invalidates list and stats. */
export function useAcknowledgeAllFailed() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => acknowledgeAllFailed(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
      queryClient.invalidateQueries({ queryKey: ["notification-stats"] });
    },
  });
}
