/**
 * TanStack Query hooks for the schedules API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createButlerSchedule,
  deleteButlerSchedule,
  getButlerSchedules,
  toggleButlerSchedule,
  triggerButlerSchedule,
  updateButlerSchedule,
} from "@/api/index.ts";
import type { Schedule, ScheduleCreate, ScheduleUpdate } from "@/api/types.ts";
import { useOptimisticListMutation } from "@/hooks/use-optimistic-mutation.ts";

/**
 * Primary poll interval for schedules queries (bu-ep4ks.15).
 * No fleet-bus event type covers this domain (see
 * event-cache-registry.ts's EVENT_CACHE_REGISTRY) -- this cadence IS
 * the update path, not a reconciliation sweep.
 */
const SCHEDULES_POLL_MS = 30_000;

/** Fetch all schedules for a butler with auto-refresh. */
export function useSchedules(butlerName: string) {
  return useQuery({
    queryKey: ["butlers", butlerName, "schedules"],
    queryFn: () => getButlerSchedules(butlerName),
    enabled: !!butlerName,
    refetchInterval: SCHEDULES_POLL_MS,
  });
}

/** Mutation to create a new schedule for a butler. */
export function useCreateSchedule(butlerName: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (body: ScheduleCreate) => createButlerSchedule(butlerName, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["butlers", butlerName, "schedules"] });
    },
  });
}

/** Mutation to update an existing schedule. */
export function useUpdateSchedule(butlerName: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ scheduleId, body }: { scheduleId: string; body: ScheduleUpdate }) =>
      updateButlerSchedule(butlerName, scheduleId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["butlers", butlerName, "schedules"] });
    },
  });
}

/** Mutation to delete a schedule. */
export function useDeleteSchedule(butlerName: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (scheduleId: string) => deleteButlerSchedule(butlerName, scheduleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["butlers", butlerName, "schedules"] });
    },
  });
}

/** Mutation to trigger a schedule immediately (one-off dispatch). */
export function useTriggerSchedule(butlerName: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (scheduleId: string) => triggerButlerSchedule(butlerName, scheduleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["butlers", butlerName, "schedules"] });
    },
  });
}

/**
 * Mutation to toggle a schedule's enabled/disabled state (toggle —
 * OPTIMISTIC: flips `enabled` in the cached list immediately, rolls back on
 * error).
 */
export function useToggleSchedule(butlerName: string) {
  return useOptimisticListMutation<unknown, string, Schedule>({
    mutationFn: (scheduleId: string) => toggleButlerSchedule(butlerName, scheduleId),
    listKeyPrefix: ["butlers", butlerName, "schedules"],
    updateItems: (schedules, scheduleId) =>
      schedules.map((s) => (s.id === scheduleId ? { ...s, enabled: !s.enabled } : s)),
  });
}
