/**
 * LatestInteractionsBlock (entity v3 — "Latest interactions per channel", bu-19u8r)
 *
 * A first-class quick-refresh section (both modes) showing the single most
 * recent touch per channel/kind. Per the spec it reads *through* the existing
 * endpoints — message-thread summaries (one row per channel) and the activity
 * timeline (interaction-kind rows) — and renders one row per channel, most
 * recent first. Store consolidation is explicitly out of scope.
 *
 * Each row shows:
 *   - the kind/channel label;
 *   - the stored deterministic summary (snippet / fact content) — NEVER generated
 *     prose at render time;
 *   - `occurred_at` with a staleness treatment (the read-time freshness axis,
 *     derived from the timestamp via the shared staleness thresholds);
 *   - the source (`src`) mark.
 *
 * The block hides itself when the entity has no interactions across any channel.
 */

import {
  ProvenanceMarks,
  StalenessBand,
  stalenessBandForTimestamp,
} from "@/components/ui/Provenance";
import { Row } from "@/components/ui/Row";
import { Time } from "@/components/ui/time";
import { useEntityMessageThreads, useEntityTimeline } from "@/hooks/use-entities";
import { deriveLatestTouches, type LatestTouch } from "@/lib/latest-touches";

function LatestInteractionRow({ touch }: { touch: LatestTouch }) {
  const occurred = touch.occurredAt ? new Date(touch.occurredAt) : null;

  return (
    <Row
      density="scan"
      data-testid={`latest-interaction-row-${touch.key}`}
      meta={
        <div className="flex items-center gap-2 text-xs tabular-nums text-muted-foreground">
          {occurred ? <Time value={occurred} mode="relative" /> : <span>—</span>}
          <StalenessBand band={stalenessBandForTimestamp(touch.occurredAt)} />
        </div>
      }
    >
      <div className="min-w-0 space-y-0.5">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {touch.label}
          </span>
          {touch.src != null && touch.src !== "" && (
            <ProvenanceMarks src={touch.src} />
          )}
        </div>
        {touch.summary && (
          <p className="line-clamp-2 text-sm leading-snug">{touch.summary}</p>
        )}
      </div>
    </Row>
  );
}

export function LatestInteractionsBlock({ entityId }: { entityId: string }) {
  const { data: threads, isLoading: threadsLoading } =
    useEntityMessageThreads(entityId);
  const { data: timeline, isLoading: timelineLoading } =
    useEntityTimeline(entityId);

  if (threadsLoading || timelineLoading) return null;

  const touches = deriveLatestTouches(threads ?? [], timeline ?? []);
  if (touches.length === 0) return null;

  return (
    <section data-testid="latest-interactions-block" className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold">Latest interactions</h2>
        <span className="text-xs tabular-nums text-muted-foreground">
          {touches.length} {touches.length === 1 ? "channel" : "channels"}
        </span>
      </div>
      <div className="divide-y divide-border border-y">
        {touches.map((touch) => (
          <LatestInteractionRow key={touch.key} touch={touch} />
        ))}
      </div>
    </section>
  );
}
