/**
 * Resolving an overlap from the Needs-you queue (bu-5ii4gj.11.2): one offered option, posted to the
 * resolve route bound to the digest the owner saw and the window it was scanned in. Keep both is
 * recorded on the server (R2a), so a dismiss survives a reload; the receipt text here only says what
 * the server answered.
 */

import { useCallback, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client.ts";
import type { ConflictIssue, ConflictResolveResponse, ResolutionOption } from "@/api/types.ts";
import { optionLabel } from "@/components/calendar/conflict-option-label.ts";
import { useResolveCalendarConflict } from "@/hooks/use-calendar-workspace";

/**
 * The option kinds the resolve route on this stack accepts. A key for any other kind would post
 * a request the server refuses with a 422, so the queue binds none. Widened to the move, decline
 * and tentative kinds when R3 (the route's other kinds) lands underneath.
 */
export const SUPPORTED_KINDS: ReadonlySet<ResolutionOption["kind"]> = new Set(["keep_both"]);

/** The offered options the route accepts, in the order offered (digits number these only). */
export function supportedOptions(issue: ConflictIssue): ResolutionOption[] {
  return (issue.options ?? []).filter((option) => SUPPORTED_KINDS.has(option.kind));
}

/** What the server answered, in the owner's words. */
export function conflictReceiptText(result: ConflictResolveResponse): string {
  switch (result.status) {
    case "acknowledged":
    case "already_acknowledged":
      return "Kept both. This stays quiet until one of them changes.";
    case "parked":
      return "Move sent for your approval.";
    case "responded":
      if (result.response_outcome === "rejected") {
        return result.response_reason === "response_approval_expired"
          ? "That response card expired. Nothing was sent."
          : "You already rejected this response. Nothing was sent.";
      }
      return result.response_outcome === "pending_approval"
        ? "Response sent for your approval."
        : "Response recorded on your calendar.";
  }
}

export interface ConflictResolution {
  resolve: (issue: ConflictIssue, option: ResolutionOption) => void;
  isResolving: boolean;
  /** Receipt text by the overlap's `pair_id`, for the answers given in this session. */
  receipts: ReadonlyMap<string, string>;
}

export function useConflictResolution(scanWindow: { start: string; end: string }): ConflictResolution {
  const mutation = useResolveCalendarConflict();
  const [receipts, setReceipts] = useState<ReadonlyMap<string, string>>(new Map());
  const { start, end } = scanWindow;
  const mutate = mutation.mutate;

  const resolve = useCallback(
    (issue: ConflictIssue, option: ResolutionOption) => {
      const digest = issue.issue_digest;
      const pairId = issue.pair_id;
      // An issue the server did not bind a digest and id to cannot be resolved from here.
      if (!digest || !pairId) return;
      mutate(
        { pair_id: pairId, option_id: option.id, issue_digest: digest, start, end },
        {
          onSuccess: (response) => {
            const text = conflictReceiptText(response.data);
            setReceipts((previous) => new Map(previous).set(pairId, text));
            if (response.data.response_outcome === "rejected") toast.info(text);
            else toast.success(text);
          },
          onError: (error) =>
            toast.error(
              error instanceof ApiError && error.status === 409
                ? "That overlap changed. Review it again."
                : `Could not ${optionLabel(issue, option).toLowerCase()}.`,
            ),
        },
      );
    },
    [mutate, start, end],
  );

  return { resolve, isResolving: mutation.isPending, receipts };
}
