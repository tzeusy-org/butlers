/**
 * Standing-reply template drafts: owner-only, inactive and without authority.
 * The routes shipped with bu-jv1moa.10; this editor is bu-jv1moa.42. There is
 * deliberately no activation hook, since activation belongs to bu-jv1moa.28.
 */

import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import {
  createReplyTemplateDraft,
  editReplyTemplateDraft,
  listReplyTemplateDrafts,
  previewReplyTemplateDraft,
  revokeReplyTemplateDraft,
} from "@/api/index.ts";
import type {
  StandingReplyTemplateDraftCreateRequest,
  StandingReplyTemplateDraftEditRequest,
  StandingReplyTemplatePreviewRequest,
} from "@/api/index.ts";

export const replyTemplateDraftKeys = {
  all: ["approvals", "reply-template-drafts"] as const,
};

export function useReplyTemplateDrafts() {
  return useInfiniteQuery({
    queryKey: replyTemplateDraftKeys.all,
    queryFn: ({ pageParam }) => listReplyTemplateDrafts(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
}

function useInvalidateDrafts() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: replyTemplateDraftKeys.all });
}

export function useCreateReplyTemplateDraft() {
  const invalidate = useInvalidateDrafts();
  return useMutation({
    mutationFn: (request: StandingReplyTemplateDraftCreateRequest) =>
      createReplyTemplateDraft(request),
    onSettled: invalidate,
  });
}

export function useEditReplyTemplateDraft() {
  const invalidate = useInvalidateDrafts();
  return useMutation({
    mutationFn: ({ id, request }: { id: string; request: StandingReplyTemplateDraftEditRequest }) =>
      editReplyTemplateDraft(id, request),
    // Settled, not success: a 409 stale_revision must reload the newer revision too.
    onSettled: invalidate,
  });
}

export function useRevokeReplyTemplateDraft() {
  const invalidate = useInvalidateDrafts();
  return useMutation({
    mutationFn: ({ id, expectedRevision }: { id: string; expectedRevision: number }) =>
      revokeReplyTemplateDraft(id, expectedRevision),
    onSettled: invalidate,
  });
}

/** A preview reads only; it never changes a draft, so nothing is invalidated. */
export function usePreviewReplyTemplateDraft() {
  return useMutation({
    mutationFn: ({ id, request }: { id: string; request: StandingReplyTemplatePreviewRequest }) =>
      previewReplyTemplateDraft(id, request),
  });
}
