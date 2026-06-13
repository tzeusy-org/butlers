/**
 * TanStack Query hooks for priority contacts.
 *
 * Priority senders are NOT ingestion rules — they live in
 * public.priority_contacts, which is the table the Gmail policy evaluator
 * reads at runtime (connectors/gmail_policy.py). These hooks talk to
 * GET/POST/DELETE /api/ingestion/priority-contacts so the dashboard view and
 * mutations reflect the actual runtime source of truth.
 *
 * Query key strategy:
 * - priorityContactKeys.all                 -> broad invalidation anchor
 * - priorityContactKeys.list(params?)       -> list with optional butler filter
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addPriorityContact,
  getPriorityContacts,
  removePriorityContact,
} from "@/api/index.ts";
import type {
  PriorityContactAddRequest,
  PriorityContactListParams,
} from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const priorityContactKeys = {
  all: ["priority-contacts"] as const,
  list: (params?: PriorityContactListParams) =>
    [...priorityContactKeys.all, "list", params] as const,
} as const;

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/** Fetch priority contacts, optionally filtered by butler. */
export function usePriorityContacts(
  params?: PriorityContactListParams,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: priorityContactKeys.list(params),
    queryFn: () => getPriorityContacts(params),
    staleTime: 60_000,
    enabled: options?.enabled !== false,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/** Add a priority contact. Invalidates the list cache on success. */
export function useAddPriorityContact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: PriorityContactAddRequest) => addPriorityContact(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: priorityContactKeys.all });
    },
  });
}

/** Remove a priority contact. Invalidates the list cache on success. */
export function useRemovePriorityContact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ contactId, butler }: { contactId: string; butler: string }) =>
      removePriorityContact(contactId, butler),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: priorityContactKeys.all });
    },
  });
}
