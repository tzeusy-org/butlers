/**
 * plex-layout — pure layout math for the /entities Plex (owner ego-graph).
 *
 * Two layout modes:
 *   - Owner mode: contacts arranged on concentric Dunbar tier rings around
 *     the owner (radial distance = tier, angular spread within the ring).
 *   - Neighbour mode: a re-centered entity's neighbours fanned into angular
 *     sectors, one sector per relational predicate, radius by weight rank.
 *
 * No React, no DOM: everything here is deterministic geometry so it can be
 * unit-tested and so PlexPage.tsx only exports components
 * (react-refresh/only-export-components).
 */

import type { DunbarEntry, NeighboursResponse } from "@/api/types";
import {
  TIER_RING_COLORS,
  TIERS,
  type Tier,
} from "@/components/memory/concentric-circles-constants";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Tiers rendered as node rings in owner mode. Tier 1500 renders as a count. */
export const PLEX_NODE_TIERS = [5, 15, 50, 150, 500] as const;
export type PlexNodeTier = (typeof PLEX_NODE_TIERS)[number];

/** Ring radii as fractions of the canvas radius, per rendered tier. */
export const PLEX_RING_FRACTIONS: Record<PlexNodeTier, number> = {
  5: 0.3,
  15: 0.47,
  50: 0.64,
  150: 0.78,
  500: 0.89,
};

/**
 * Angular notch (radians) kept clear on either side of 12 o'clock so the
 * ring capacity labels never collide with contact nodes.
 */
export const PLEX_TOP_NOTCH = 0.35;

/** The dashed periphery ring where tier 1500 is summarized, not drawn. */
export const PLEX_PERIPHERY_FRACTION = 0.97;

/** EntityMark size (px) per tier: intimacy reads as presence. */
export const PLEX_MARK_SIZES: Record<PlexNodeTier, number> = {
  5: 34,
  15: 28,
  50: 22,
  150: 20,
  500: 18,
};

/** Staleness thresholds (days since last interaction / observation). */
export const STALE_SOFT_DAYS = 60;
export const STALE_HARD_DAYS = 180;

export type Staleness = "fresh" | "soft" | "hard" | "unknown";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/** Whole days elapsed since an ISO timestamp; null when the input is null. */
export function daysSince(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  return Math.max(0, Math.floor((Date.now() - then) / 86_400_000));
}

/** Bucket a days-since value into the staleness bands the plex renders. */
export function stalenessOf(days: number | null): Staleness {
  if (days === null) return "unknown";
  if (days >= STALE_HARD_DAYS) return "hard";
  if (days >= STALE_SOFT_DAYS) return "soft";
  return "fresh";
}

/**
 * Polar-to-cartesian around a center. Angle 0 points up (12 o'clock) and
 * increases clockwise, which keeps sector labels readable.
 */
export function polar(
  cx: number,
  cy: number,
  r: number,
  angle: number,
): { x: number; y: number } {
  return {
    x: cx + r * Math.sin(angle),
    y: cy - r * Math.cos(angle),
  };
}

/** Human-readable predicate label (deterministic, no prose). */
export function prettyPredicate(predicate: string): string {
  return predicate.replaceAll("-", " ").replaceAll("_", " ");
}

export { TIER_RING_COLORS, TIERS };
export type { Tier };

// ---------------------------------------------------------------------------
// Owner mode
// ---------------------------------------------------------------------------

export interface OwnerPlexNode {
  entityId: string;
  name: string;
  tier: PlexNodeTier;
  /** Angle in radians (0 = up, clockwise). */
  angle: number;
  /** Radius as a fraction of the canvas radius. */
  radiusFrac: number;
  /** EntityMark size in px. */
  size: number;
  /** Days since last interaction; null when never observed. */
  staleDays: number | null;
  staleness: Staleness;
  pinned: boolean;
}

export interface OwnerPlexLayout {
  nodes: OwnerPlexNode[];
  /** Non-owner contact count per tier (all six, including 1500). */
  tierCounts: Record<Tier, number>;
}

/**
 * Lay out the owner ego-graph: every non-owner contact in tiers 5..500 gets a
 * node on its tier ring; tier 1500 is counted but not drawn. Within a ring,
 * contacts are sorted by score (descending) and spread evenly, with a fixed
 * per-tier angular offset so adjacent rings do not align into spokes.
 */
export function layoutOwnerPlex(
  entries: DunbarEntry[],
  ownerEntityId: string | null,
): OwnerPlexLayout {
  const tierCounts = Object.fromEntries(TIERS.map((t) => [t, 0])) as Record<
    Tier,
    number
  >;
  const byTier = new Map<PlexNodeTier, DunbarEntry[]>(
    PLEX_NODE_TIERS.map((t) => [t, []]),
  );

  for (const entry of entries) {
    if (entry.entity_id === ownerEntityId) continue;
    const tier = entry.dunbar_tier as Tier;
    if (tier in tierCounts) tierCounts[tier] += 1;
    const bucket = byTier.get(tier as PlexNodeTier);
    if (bucket) bucket.push(entry);
  }

  const nodes: OwnerPlexNode[] = [];
  const span = 2 * Math.PI - 2 * PLEX_TOP_NOTCH;
  PLEX_NODE_TIERS.forEach((tier, tierIndex) => {
    const bucket = byTier.get(tier)!;
    bucket.sort((a, b) => b.dunbar_score - a.dunbar_score);
    const n = bucket.length;
    bucket.forEach((entry, i) => {
      const staleDays = daysSince(entry.last_interaction_at);
      // Fractional per-tier rotation keeps rings from forming radial spokes
      // while every node stays inside the notched span.
      const pos = (((i + tierIndex * 0.37) % n) + 0.5) / Math.max(1, n);
      nodes.push({
        entityId: entry.entity_id,
        name: entry.canonical_name,
        tier,
        angle: PLEX_TOP_NOTCH + pos * span,
        radiusFrac: PLEX_RING_FRACTIONS[tier],
        size: PLEX_MARK_SIZES[tier],
        staleDays,
        staleness: stalenessOf(staleDays),
        pinned: entry.dunbar_tier_override,
      });
    });
  });

  return { nodes, tierCounts };
}

// ---------------------------------------------------------------------------
// Neighbour mode
// ---------------------------------------------------------------------------

export interface NeighbourPlexNode {
  entityId: string;
  name: string;
  predicate: string;
  angle: number;
  radiusFrac: number;
  size: number;
  /** Assertion confidence, 0..1 — rendered as edge/label opacity. */
  conf: number;
  staleDays: number | null;
  staleness: Staleness;
  verified: boolean;
}

export interface NeighbourPlexSector {
  predicate: string;
  startAngle: number;
  endAngle: number;
  midAngle: number;
  /** Count of neighbours truncated away by ranked pagination. */
  remainder: number;
}

export interface NeighbourPlexLayout {
  nodes: NeighbourPlexNode[];
  sectors: NeighbourPlexSector[];
}

const SECTOR_GAP = 0.06; // radians of breathing room between sectors

/**
 * Lay out a re-centered entity's neighbours: predicates become angular
 * sectors sized by neighbour count (each padded so small sectors stay
 * legible); within a sector, neighbours are ranked by weight and alternate
 * between an inner and outer shell so labels do not collide.
 */
export function layoutNeighbourPlex(
  response: NeighboursResponse,
): NeighbourPlexLayout {
  const groups = Object.entries(response.neighbours)
    .filter(([, list]) => list.length > 0)
    .sort((a, b) => b[1].length - a[1].length);

  const totalCount = groups.reduce((sum, [, list]) => sum + list.length, 0);
  const nodes: NeighbourPlexNode[] = [];
  const sectors: NeighbourPlexSector[] = [];
  if (totalCount === 0) return { nodes, sectors };

  // Each sector gets a floor share plus a count-proportional share.
  const floor = (2 * Math.PI * 0.35) / groups.length;
  const proportional = 2 * Math.PI * 0.65;

  let cursor = 0;
  for (const [predicate, list] of groups) {
    const span = floor + (proportional * list.length) / totalCount;
    const start = cursor + SECTOR_GAP / 2;
    const end = cursor + span - SECTOR_GAP / 2;
    sectors.push({
      predicate,
      startAngle: start,
      endAngle: end,
      midAngle: (start + end) / 2,
      remainder: response.remainders[predicate] ?? 0,
    });

    // A peer can appear in both directions (forward + reverse) of the same
    // predicate; keep one row per entity (the heaviest) so nodes and React
    // keys stay unique within the sector.
    const seen = new Set<string>();
    const ranked = [...list]
      .sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0))
      .filter((entry) => {
        if (seen.has(entry.entity_id)) return false;
        seen.add(entry.entity_id);
        return true;
      });
    const n = ranked.length;
    ranked.forEach((entry, i) => {
      const staleDays = daysSince(entry.last_seen);
      nodes.push({
        entityId: entry.entity_id,
        name: entry.canonical_name,
        predicate,
        angle: start + ((i + 1) * (end - start)) / (n + 1),
        // Heaviest neighbours sit closest; alternate shells to avoid collisions.
        radiusFrac: 0.48 + 0.2 * (i % 2) + (i > 3 ? 0.08 : 0),
        size: 24,
        conf: entry.conf,
        staleDays,
        staleness: stalenessOf(staleDays),
        verified: entry.verified,
      });
    });

    cursor += span;
  }

  return { nodes, sectors };
}
