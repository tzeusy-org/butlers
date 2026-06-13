/**
 * latest-touches — pure derivation for the entity v3 "Latest interactions per
 * channel" block (bu-19u8r).
 *
 * Collapses message-thread summaries + interaction-kind timeline rows into one
 * most-recent touch per channel, most recent first. Pure (no React, no hooks)
 * so it is trivially testable and recomputes deterministically at render time.
 */

import type { EntityTimelineItem, MessageThreadSummary } from "@/api/types";

/**
 * One normalized latest-touch row, channel-keyed. `key` groups touches so we
 * keep only the most recent per channel; `label` is the human channel name;
 * `summary` is the stored snippet/content; `src` is the attribution mark.
 */
export interface LatestTouch {
  key: string;
  label: string;
  summary: string | null;
  occurredAt: string | null;
  src: string | null;
}

/** Title-case a raw channel/kind token for display (e.g. "telegram" → "Telegram"). */
export function channelLabel(raw: string | null): string {
  if (!raw) return "Unknown";
  const cleaned = raw.replaceAll("_", " ").trim();
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

/**
 * Map a timeline interaction row to its channel key. Interaction predicates are
 * shaped `interaction_<kind>` (e.g. `interaction_call`); fall back to the bare
 * kind so every interaction lands in a channel bucket.
 */
function interactionChannel(item: EntityTimelineItem): string {
  if (item.predicate.startsWith("interaction_")) {
    return item.predicate.slice("interaction_".length);
  }
  return item.predicate || item.kind;
}

/** Epoch millis for a nullable timestamp; null/unparseable sorts oldest. */
function tsMillis(when: string | null): number {
  if (!when) return Number.NEGATIVE_INFINITY;
  const ms = new Date(when).getTime();
  return Number.isNaN(ms) ? Number.NEGATIVE_INFINITY : ms;
}

/**
 * Collapse message threads + interaction-kind timeline rows into one most-recent
 * touch per channel, sorted most-recent first.
 */
export function deriveLatestTouches(
  threads: MessageThreadSummary[],
  timeline: EntityTimelineItem[],
): LatestTouch[] {
  const latestByChannel = new Map<string, LatestTouch>();

  const consider = (touch: LatestTouch) => {
    const existing = latestByChannel.get(touch.key);
    if (!existing || tsMillis(touch.occurredAt) > tsMillis(existing.occurredAt)) {
      latestByChannel.set(touch.key, touch);
    }
  };

  for (const thread of threads) {
    const channel = thread.source_channel ?? "unknown";
    consider({
      key: `thread:${channel}`,
      label: channelLabel(thread.source_channel),
      summary: thread.last_snippet,
      occurredAt: thread.last_received_at,
      // Message threads do not carry an authoring butler; the channel is the
      // honest source attribution here.
      src: thread.source_channel,
    });
  }

  for (const item of timeline) {
    if (item.kind !== "interaction") continue;
    const channel = interactionChannel(item);
    consider({
      key: `interaction:${channel}`,
      label: channelLabel(channel),
      summary: item.content,
      occurredAt: item.valid_at,
      src: typeof item.metadata?.src === "string" ? item.metadata.src : null,
    });
  }

  return [...latestByChannel.values()].sort((a, b) => {
    // Compare, not subtract: two missing/invalid timestamps both map to
    // NEGATIVE_INFINITY, and subtracting those yields NaN — an unstable
    // comparator return that breaks the deterministic order this module promises.
    const aMs = tsMillis(a.occurredAt);
    const bMs = tsMillis(b.occurredAt);
    if (aMs === bMs) return 0;
    return bMs > aMs ? 1 : -1;
  });
}
