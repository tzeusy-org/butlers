/**
 * TanStack Query hooks for the relationship / CRM API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  getGroupMembers,
  getGroups,
  getLabels,
  createLabel,
  assignGroupLabel,
  removeGroupLabel,
  getOverdueContacts,
  getUpcomingDates,
} from "@/api/index.ts";
import type {
  ApiResponse,
  Group,
  Label,
  GroupParams,
} from "@/api/index.ts";
import {
  type ListSnapshot,
  rollbackLists,
  snapshotAndUpdateLists,
  useOptimisticListMutation,
  useOptimisticMutation,
} from "@/hooks/use-optimistic-mutation.ts";

/** Fetch a paginated list of groups. */
export function useGroups(params?: GroupParams) {
  return useQuery({
    queryKey: ["groups", params],
    queryFn: () => getGroups(params),
    placeholderData: (prev) => prev,
  });
}

/** Fetch a group's member roster (bu-5umz4 — Circles lens deep-links). */
export function useGroupMembers(groupId: string | undefined) {
  return useQuery({
    queryKey: ["group-members", groupId],
    queryFn: () => getGroupMembers(groupId!),
    enabled: !!groupId,
  });
}

/** Fetch all labels. */
export function useLabels() {
  return useQuery({
    queryKey: ["labels"],
    queryFn: () => getLabels(),
  });
}

/** Create a new label. */
export function useCreateLabel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; color?: string | null }) => createLabel(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["labels"] });
      toast.success("Label created");
    },
    onError: (err: Error) => {
      toast.error(err.message ?? "Failed to create label");
    },
  });
}

/**
 * Assign a label to a group (toggle-like — OPTIMISTIC: appends the label to
 * the cached group immediately, using the already-cached `["labels"]` list to
 * resolve the label's name/color; rolls back on error).
 */
export function useAssignGroupLabel() {
  return useOptimisticMutation<
    unknown,
    { groupId: string; labelId: string },
    ListSnapshot
  >({
    mutationFn: ({ groupId, labelId }) => assignGroupLabel(groupId, labelId),
    applyOptimisticUpdate: ({ groupId, labelId }, queryClient) => {
      const label = queryClient
        .getQueryData<ApiResponse<Label[]>>(["labels"])
        ?.data.find((l) => l.id === labelId);
      return snapshotAndUpdateLists<Group>(queryClient, ["groups"], (groups) =>
        groups.map((g) =>
          g.id === groupId && label && !g.labels.some((l) => l.id === labelId)
            ? { ...g, labels: [...g.labels, label] }
            : g,
        ),
      );
    },
    rollback: (snapshot, queryClient) => rollbackLists(queryClient, snapshot),
    invalidateQueryKeys: [["groups"]],
    onError: (err) => toast.error(err.message ?? "Failed to assign label"),
  });
}

/** Remove a label from a group (mirrors {@link useAssignGroupLabel}). */
export function useRemoveGroupLabel() {
  return useOptimisticListMutation<
    unknown,
    { groupId: string; labelId: string },
    Group
  >({
    mutationFn: ({ groupId, labelId }) => removeGroupLabel(groupId, labelId),
    listKeyPrefix: ["groups"],
    updateItems: (groups, { groupId, labelId }) =>
      groups.map((g) =>
        g.id === groupId ? { ...g, labels: g.labels.filter((l) => l.id !== labelId) } : g,
      ),
    onError: (err) => toast.error(err.message ?? "Failed to remove label"),
  });
}

/** Fetch upcoming dates within a given number of days. */
/** @public knip mis-traces this import (live consumer exists); remove tag when bu-9jvhm fixes the tracing gap. */
export function useUpcomingDates(days?: number) {
  return useQuery({
    queryKey: ["upcoming-dates", days],
    queryFn: () => getUpcomingDates(days),
  });
}

/**
 * Fetch contacts overdue on their Dunbar tier cadence.
 * Wraps GET /api/relationship/contacts/overdue?days=N
 */
export function useOverdueContacts(days?: number) {
  return useQuery({
    queryKey: ["overdue-contacts", days],
    queryFn: () => getOverdueContacts(days),
    staleTime: 60_000,
  });
}
