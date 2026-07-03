/**
 * TanStack Query hooks for the relationship / CRM API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  archiveContact,
  unarchiveContact,
  confirmContact,
  createAndLinkEntity,
  createContactInfo,
  deleteContact,
  deleteContactInfo,
  getContact,
  getContacts,
  getContactInteractions,
  getEntitySuggestions,
  getGroups,
  getLabels,
  createLabel,
  assignGroupLabel,
  removeGroupLabel,
  getOverdueContacts,
  getPendingContacts,
  getUnlinkedContacts,
  linkEntity,
  patchContact,
  patchContactInfo,
  getUpcomingDates,
} from "@/api/index.ts";
import type {
  ApiResponse,
  ContactDetail,
  ContactPatchRequest,
  ContactSummary,
  CreateAndLinkEntityRequest,
  CreateContactInfoRequest,
  Group,
  Label,
  LinkEntityRequest,
  PatchContactInfoRequest,
  ContactParams,
  GroupParams,
} from "@/api/index.ts";
import {
  type ListSnapshot,
  rollbackLists,
  snapshotAndUpdateLists,
  useOptimisticListMutation,
  useOptimisticMutation,
} from "@/hooks/use-optimistic-mutation.ts";

/** Fetch a paginated list of contacts. */
export function useContacts(params?: ContactParams) {
  return useQuery({
    queryKey: ["contacts", params],
    queryFn: () => getContacts(params),
    // Never-blank list (JARVIS audit move 10): keep the previous filter's
    // rows visible while the new combination fetches.
    placeholderData: (prev) => prev,
  });
}

/** Fetch full detail for a single contact. */
export function useContact(contactId: string | undefined) {
  return useQuery({
    queryKey: ["contact", contactId],
    queryFn: () => getContact(contactId!),
    enabled: !!contactId,
  });
}

/** Fetch a paginated list of groups. */
export function useGroups(params?: GroupParams) {
  return useQuery({
    queryKey: ["groups", params],
    queryFn: () => getGroups(params),
    placeholderData: (prev) => prev,
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
export function useUpcomingDates(days?: number) {
  return useQuery({
    queryKey: ["upcoming-dates", days],
    queryFn: () => getUpcomingDates(days),
  });
}

/** Fetch pending contacts awaiting identity resolution. */
export function usePendingContacts() {
  return useQuery({
    queryKey: ["pending-contacts"],
    queryFn: () => getPendingContacts(),
  });
}


/** Patch a contact's fields. */
export function usePatchContact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ contactId, request }: { contactId: string; request: ContactPatchRequest }) =>
      patchContact(contactId, request),
    onSuccess: (_, { contactId }) => {
      void queryClient.invalidateQueries({ queryKey: ["contact", contactId] });
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
    },
  });
}

/**
 * Confirm a pending contact as a new known contact (ack — OPTIMISTIC: drops
 * it from the pending queue immediately; the promoted contact itself still
 * comes from the `["contacts"]` invalidate-driven refetch since we don't
 * have its full record client-side to insert directly).
 */
export function useConfirmContact() {
  return useOptimisticListMutation<unknown, string, ContactDetail>({
    mutationFn: (contactId: string) => confirmContact(contactId),
    listKeyPrefix: ["pending-contacts"],
    updateItems: (pending, contactId) => pending.filter((c) => c.id !== contactId),
    invalidateQueryKeys: [["pending-contacts"], ["contacts"]],
    onSuccess: () => toast.success("Contact confirmed"),
    onError: (err) => toast.error(`Confirm failed: ${err.message}`),
  });
}

/** Add a contact_info entry to a contact. */
export function useCreateContactInfo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      contactId,
      request,
    }: {
      contactId: string;
      request: CreateContactInfoRequest;
    }) => createContactInfo(contactId, request),
    onSuccess: (_, { contactId }) => {
      void queryClient.invalidateQueries({ queryKey: ["contact", contactId] });
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
    },
  });
}

/** Hard-delete a contact. */
export function useDeleteContact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (contactId: string) => deleteContact(contactId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
    },
  });
}

/**
 * Archive a contact (soft-delete, sync won't re-create — toggle-like and
 * reversible via {@link useUnarchiveContact}, so OPTIMISTIC: drops it from
 * every cached `["contacts", params]` view immediately; a view scoped to
 * `archived: true` picks it back up on the invalidate-driven refetch).
 */
export function useArchiveContact() {
  return useOptimisticListMutation<unknown, string, ContactSummary>({
    mutationFn: (contactId: string) => archiveContact(contactId),
    listKeyPrefix: ["contacts"],
    updateItems: (contacts, contactId) => contacts.filter((c) => c.id !== contactId),
    invalidateQueryKeys: [["contacts"], ["unlinked-contacts"], ["pending-contacts"]],
  });
}

/** Restore an archived contact (mirrors {@link useArchiveContact}). */
export function useUnarchiveContact() {
  return useOptimisticListMutation<unknown, string, ContactSummary>({
    mutationFn: (contactId: string) => unarchiveContact(contactId),
    listKeyPrefix: ["contacts"],
    updateItems: (contacts, contactId) => contacts.filter((c) => c.id !== contactId),
  });
}

/** Delete a contact_info entry. */
export function useDeleteContactInfo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ contactId, infoId }: { contactId: string; infoId: string }) =>
      deleteContactInfo(contactId, infoId),
    onSuccess: (_, { contactId }) => {
      void queryClient.invalidateQueries({ queryKey: ["contact", contactId] });
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
    },
  });
}

/** Update a contact_info entry. */
export function usePatchContactInfo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      contactId,
      infoId,
      request,
    }: {
      contactId: string;
      infoId: string;
      request: PatchContactInfoRequest;
    }) => patchContactInfo(contactId, infoId, request),
    onSuccess: (_, { contactId }) => {
      void queryClient.invalidateQueries({ queryKey: ["contact", contactId] });
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Unlinked contacts / entity disambiguation
// ---------------------------------------------------------------------------

/** Fetch paginated unlinked contacts with entity suggestions. */
export function useUnlinkedContacts(params?: { offset?: number; limit?: number; q?: string }) {
  return useQuery({
    queryKey: ["unlinked-contacts", params],
    queryFn: () => getUnlinkedContacts(params),
    placeholderData: (prev) => prev,
  });
}

/** Fetch on-demand entity suggestions for a contact. */
export function useEntitySuggestions(contactId: string | undefined, q?: string) {
  return useQuery({
    queryKey: ["entity-suggestions", contactId, q],
    queryFn: () => getEntitySuggestions(contactId!, q),
    enabled: !!contactId,
  });
}

/** Link an existing entity to a contact. */
export function useLinkEntity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ contactId, request }: { contactId: string; request: LinkEntityRequest }) =>
      linkEntity(contactId, request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["unlinked-contacts"] });
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
      toast.success("Entity linked successfully");
    },
    onError: (err) => {
      toast.error(`Link failed: ${err instanceof Error ? err.message : "Unknown error"}`);
    },
  });
}

/** Create a new entity from contact data and link it. */
export function useCreateAndLinkEntity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      contactId,
      request,
    }: {
      contactId: string;
      request: CreateAndLinkEntityRequest;
    }) => createAndLinkEntity(contactId, request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["unlinked-contacts"] });
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
      toast.success("Entity created and linked");
    },
    onError: (err) => {
      toast.error(`Create failed: ${err instanceof Error ? err.message : "Unknown error"}`);
    },
  });
}

// ---------------------------------------------------------------------------
// New hooks from bu-iuol4.22 backend endpoints
// ---------------------------------------------------------------------------

/**
 * Fetch chronological interaction thread for a contact.
 * Wraps GET /api/relationship/contacts/{contact_id}/interactions?limit=N
 */
export function useContactInteractions(contactId: string | undefined, limit?: number) {
  return useQuery({
    queryKey: ["contact-interactions", contactId, limit],
    queryFn: () => getContactInteractions(contactId!, limit),
    enabled: !!contactId,
    staleTime: 60_000,
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
