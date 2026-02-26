/**
 * TanStack Query hooks for the relationship / CRM API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  confirmContact,
  createContactInfo,
  deleteContact,
  deleteContactInfo,
  getContact,
  getContactFeed,
  getContactGifts,
  getContactInteractions,
  getContactLoans,
  getContactNotes,
  getContacts,
  getGroups,
  getLabels,
  getOwnerSetupStatus,
  getPendingContacts,
  mergeContact,
  patchContact,
  patchContactInfo,
  revealContactSecret,
  getUpcomingDates,
} from "@/api/index.ts";
import type { ContactMergeRequest, ContactPatchRequest, CreateContactInfoRequest, PatchContactInfoRequest, ContactParams, GroupParams } from "@/api/index.ts";

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

/** Fetch notes for a contact. */
export function useContactNotes(contactId: string | undefined) {
  return useQuery({
    queryKey: ["contact-notes", contactId],
    queryFn: () => getContactNotes(contactId!),
    enabled: !!contactId,
  });
}

/** Fetch interactions for a contact. */
export function useContactInteractions(contactId: string | undefined) {
  return useQuery({
    queryKey: ["contact-interactions", contactId],
    queryFn: () => getContactInteractions(contactId!),
    enabled: !!contactId,
  });
}

/** Fetch gifts for a contact. */
export function useContactGifts(contactId: string | undefined) {
  return useQuery({
    queryKey: ["contact-gifts", contactId],
    queryFn: () => getContactGifts(contactId!),
    enabled: !!contactId,
  });
}

/** Fetch loans for a contact. */
export function useContactLoans(contactId: string | undefined) {
  return useQuery({
    queryKey: ["contact-loans", contactId],
    queryFn: () => getContactLoans(contactId!),
    enabled: !!contactId,
  });
}

/** Fetch the activity feed for a contact. */
export function useContactFeed(contactId: string | undefined) {
  return useQuery({
    queryKey: ["contact-feed", contactId],
    queryFn: () => getContactFeed(contactId!),
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

/** Fetch owner setup status. */
export function useOwnerSetupStatus() {
  return useQuery({
    queryKey: ["owner-setup-status"],
    queryFn: () => getOwnerSetupStatus(),
  });
}

/** Reveal a secured contact_info entry value. */
export function useRevealContactSecret() {
  return useMutation({
    mutationFn: ({ contactId, infoId }: { contactId: string; infoId: string }) =>
      revealContactSecret(contactId, infoId),
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
      void queryClient.invalidateQueries({ queryKey: ["owner-setup-status"] });
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

/** Delete a contact_info entry. */
export function useDeleteContactInfo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ contactId, infoId }: { contactId: string; infoId: string }) =>
      deleteContactInfo(contactId, infoId),
    onSuccess: (_, { contactId }) => {
      void queryClient.invalidateQueries({ queryKey: ["contact", contactId] });
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
      void queryClient.invalidateQueries({ queryKey: ["owner-setup-status"] });
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
