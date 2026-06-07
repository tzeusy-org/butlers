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
  getOverdueContacts,
  getPendingContacts,
  getUnlinkedContacts,
  linkEntity,
  mergeContact,
  patchContact,
  patchContactInfo,
  getUpcomingDates,
} from "@/api/index.ts";
import type {
  ContactMergeRequest,
  ContactPatchRequest,
  CreateAndLinkEntityRequest,
  CreateContactInfoRequest,
  LinkEntityRequest,
  PatchContactInfoRequest,
  ContactParams,
  GroupParams,
} from "@/api/index.ts";

/** Fetch a paginated list of contacts. */
export function useContacts(params?: ContactParams) {
  return useQuery({
    queryKey: ["contacts", params],
    queryFn: () => getContacts(params),
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
  });
}

/** Fetch all labels. */
export function useLabels() {
  return useQuery({
    queryKey: ["labels"],
    queryFn: () => getLabels(),
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

/** Merge a pending contact into an existing contact. */
export function useMergeContact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ contactId, request }: { contactId: string; request: ContactMergeRequest }) =>
      mergeContact(contactId, request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["pending-contacts"] });
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
      toast.success("Contacts merged successfully");
    },
    onError: (err) => {
      toast.error(`Merge failed: ${err instanceof Error ? err.message : "Unknown error"}`);
    },
  });
}

/** Confirm a pending contact as a new known contact. */
export function useConfirmContact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (contactId: string) => confirmContact(contactId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["pending-contacts"] });
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
      toast.success("Contact confirmed");
    },
    onError: (err) => {
      toast.error(`Confirm failed: ${err instanceof Error ? err.message : "Unknown error"}`);
    },
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

/** Archive a contact (soft-delete, sync won't re-create). */
export function useArchiveContact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (contactId: string) => archiveContact(contactId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
      void queryClient.invalidateQueries({ queryKey: ["unlinked-contacts"] });
    },
  });
}

/** Restore an archived contact. */
export function useUnarchiveContact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (contactId: string) => unarchiveContact(contactId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
    },
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
