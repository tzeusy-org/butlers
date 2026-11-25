/**
 * Comparison query for a parked entity merge's approval review
 * (REQ-dashboard-approvals-902). Eligibility is keyed to the exact stored call:
 * (action, tool, digest, source, target). See components/approvals/entity-merge-review.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { compareRelationshipEntities } from "@/api/index.ts";
import type { CompareEntitiesResponse, EntityMergeReview } from "@/api/types";

/** Query key binding a comparison to one exact stored merge call. */
export function mergeReviewKey(actionId: string, review: EntityMergeReview) {
  return [
    "approvals",
    "merge-review",
    actionId,
    review.displayed_call.tool_name,
    review.displayed_call.args_digest,
    review.source_entity_id,
    review.target_entity_id,
  ] as const;
}

/** A well-formed typed merge review, or null. Anything else stays Open only. */
export function validMergeReview(value: unknown): EntityMergeReview | null {
  const review = value as EntityMergeReview | null | undefined;
  if (
    !review
    || typeof review.source_entity_id !== "string"
    || typeof review.target_entity_id !== "string"
    || review.source_entity_id === review.target_entity_id
    || typeof review.displayed_call?.tool_name !== "string"
    || !/^[0-9a-f]{64}$/.test(review.displayed_call.args_digest ?? "")
  ) {
    return null;
  }
  return review;
}

/**
 * Load the structural comparison for exactly the stored source → target pair.
 * The response must name the same two IDs in the same order; anything else is
 * an error, never a substitute review.
 */
export function useEntityMergeComparison(
  actionId: string,
  review: EntityMergeReview | null,
): UseQueryResult<CompareEntitiesResponse> {
  return useQuery({
    queryKey: review ? mergeReviewKey(actionId, review) : ["approvals", "merge-review", "none"],
    queryFn: async () => {
      const diff = await compareRelationshipEntities({
        entity_a: review!.source_entity_id,
        entity_b: review!.target_entity_id,
      });
      if (
        diff.a?.entity?.id !== review!.source_entity_id
        || diff.b?.entity?.id !== review!.target_entity_id
      ) {
        throw new Error("The comparison did not describe this stored pair.");
      }
      return diff;
    },
    enabled: !!review,
    retry: false,
    // Always re-read on open: the review shows current state, not a cached snapshot.
    staleTime: 0,
    refetchOnWindowFocus: false,
  });
}
