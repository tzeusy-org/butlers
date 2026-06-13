import type { NeighbourEntry } from "@/api/index.ts";

/** Event dispatched to open the entity-first Cmd-K finder (bu-xfjwk). */
export const OPEN_ENTITY_FINDER_EVENT = "open-entity-finder";

/** Dispatch the event that opens the EntityFinder overlay. */
export function dispatchOpenEntityFinder() {
  window.dispatchEvent(new CustomEvent(OPEN_ENTITY_FINDER_EVENT));
}

// ---------------------------------------------------------------------------
// Empty-query owner-pinned set aggregation (entity-v3, bu-rru9g)
//
// Flatten the owner's predicate→neighbours map into a single ranked list:
//   - dedupe by entity_id (an entity reachable via multiple predicates appears
//     once),
//   - sum COALESCE(weight, 1) across all of its edges,
//   - sort descending by summed weight,
//   - take the top N,
//   - exclude the owner entity itself.
// ---------------------------------------------------------------------------

export interface PinnedNeighbour {
  entity_id: string;
  canonical_name: string;
  entity_type: string;
  weight: number;
}

export function aggregateOwnerPinned(
  neighbours: Record<string, NeighbourEntry[]> | undefined,
  ownerId: string | null | undefined,
  limit = 8,
): PinnedNeighbour[] {
  if (!neighbours) return [];
  const byEntity = new Map<string, PinnedNeighbour>();

  for (const entries of Object.values(neighbours)) {
    for (const entry of entries) {
      if (ownerId != null && entry.entity_id === ownerId) continue;
      // COALESCE(weight, 1): a missing edge weight counts as 1.
      const edgeWeight = entry.weight ?? 1;
      const existing = byEntity.get(entry.entity_id);
      if (existing) {
        existing.weight += edgeWeight;
      } else {
        byEntity.set(entry.entity_id, {
          entity_id: entry.entity_id,
          canonical_name: entry.canonical_name || entry.entity_id,
          // The neighbours payload carries no entity_type; the empty-query set
          // is a person-centric inner circle, so default to "person" for the
          // mark glyph. Search results (with a real type) replace this set the
          // moment the owner types.
          entity_type: "person",
          weight: edgeWeight,
        });
      }
    }
  }

  return Array.from(byEntity.values())
    .sort((a, b) => b.weight - a.weight)
    .slice(0, limit);
}
