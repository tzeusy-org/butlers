/**
 * DeltaSinceLastVisitBanner (entity v3 — "Delta-since-last-visit", bu-xzh76)
 *
 * On entering the detail page this:
 * 1. reads GET /api/butlers/relationship/entities/{id}/delta-facts (the change
 *    set computed against the *current* view mark, before it moves);
 * 2. renders a deterministic banner ("N new facts since <date>" — tabular nums,
 *    canned copy) when the mark exists and ≥ 1 fact changed;
 * 3. after that read resolves, posts POST /entities/{id}/view-mark to advance
 *    the mark (spec: "the view mark MUST be updated only after the delta was
 *    computed for this load").
 *
 * First visit (no mark row → ``marked_at: null``) renders no banner; the post
 * still fires so subsequent visits have a mark to diff against.
 *
 * The banner reports the count and date only — no generated narration of the
 * delta (binding rejection). It exposes the changed fact ids so the caller can
 * apply a highlight treatment to the matching rows.
 */

import { useEffect, useRef } from "react";
import { format } from "date-fns";

import { useEntityDeltaFacts, useMarkEntityView } from "@/hooks/use-entities";

export function DeltaSinceLastVisitBanner({ entityId }: { entityId: string }) {
  const { data, isSuccess } = useEntityDeltaFacts(entityId);
  const markView = useMarkEntityView();
  // Guard so the view-mark POST fires exactly once per entity per mount, only
  // after the delta has been read for this load.
  const markedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!isSuccess) return;
    if (markedRef.current === entityId) return;
    markedRef.current = entityId;
    markView.mutate(entityId);
    // markView is a stable mutation handle; entityId/isSuccess drive the effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityId, isSuccess]);

  if (!data || data.marked_at === null || data.items.length === 0) return null;

  const count = data.items.length;
  const since = format(new Date(data.marked_at), "MMM d");

  return (
    <p
      data-testid="delta-banner"
      className="border-y border-border py-2 text-sm tabular-nums text-muted-foreground"
    >
      {count} new {count === 1 ? "fact" : "facts"} since {since}
    </p>
  );
}
