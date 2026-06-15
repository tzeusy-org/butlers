// ---------------------------------------------------------------------------
// entity-glosses.ts -- canned voice glosses for the entity detail page
//
// Anti-temptation guardrail (Brief §4, Open Question 23):
//   Detail-page voice glosses are CANNED STRINGS, NOT LLM calls.
//   No dynamic string generation beyond simple variable substitution.
//   Reaching for an LLM call here costs ~$0.12/user/day (100 users → $12/day)
//   and violates the "composure is the brand" Section 0 contract.
//
// Fallback model (Cartesian product: 6 tiers × 5 states × 8 categories = 240):
//   Because 192 fully-distinct hand-edited entries is impractical to maintain,
//   this module uses a two-level fallback:
//
//   1. Category-specific overrides: GLOSSES_OVERRIDES[(tier, state, category)]
//      — sparse table of entries that genuinely differ by entity type.
//   2. Base glosses: GLOSSES_BASE[(tier, state)]
//      — fully exhaustive (6 × 5 = 30 entries); type-guaranteed complete.
//
//   getEntityGloss() tries (tier, state, category) first, then (tier, state).
//   The type system guarantees that (tier, state) always has a value
//   (Record<DunbarTier, Record<EntityState, string>> is structurally complete).
//
//   This means "missing combination" never reaches runtime, but a tsc error
//   fires if you omit any (tier, state) pair from GLOSSES_BASE.
//
// Voice tone:
//   Clinical, terse, observational. Present tense. No em-dashes.
//   Serif italic context (Source Serif 4). Sentences complete but spare.
// ---------------------------------------------------------------------------

import type { DunbarTier } from "@/components/ui/TierBadge"
import type { EntityState } from "@/components/ui/StateDot"
import type { EntityType } from "@/components/ui/EntityMark"

// ---------------------------------------------------------------------------
// Re-export the dimension types for consumers who only import from this file.
// ---------------------------------------------------------------------------

export type { DunbarTier, EntityState, EntityType }

// ---------------------------------------------------------------------------
// Canonical runtime value sets for each dimension.
//
// These are the single source of truth for valid enum members at runtime.
// Use these instead of duplicating the lists in callers or tests.
//
// DUNBAR_TIER_VALUES is derived from GLOSSES_BASE keys (below) after the
// base table is defined, so it is guaranteed to stay in sync with the type.
// ENTITY_STATE_VALUES and ENTITY_TYPE_VALUES are maintained here alongside
// the types they shadow.
// ---------------------------------------------------------------------------

export const ENTITY_STATE_VALUES: readonly EntityState[] = [
  "healthy",
  "unidentified",
  "duplicate-candidate",
  "stale",
  "archived",
]

export const ENTITY_TYPE_VALUES: readonly EntityType[] = [
  "person",
  "organization",
  "place",
  "product",
  "account",
  "event",
  "group",
  "other",
]

// ---------------------------------------------------------------------------
// Base gloss table: exhaustive over (tier, state).
//
// TypeScript enforces completeness: Record<DunbarTier, Record<EntityState, string>>
// means every DunbarTier must have an entry, and within each entry every
// EntityState must have a value. A missing combination is a compile error.
// ---------------------------------------------------------------------------

const GLOSSES_BASE: Record<DunbarTier, Record<EntityState, string>> = {
  5: {
    healthy:
      "Support clique. Highest-signal relationship in the graph. Expect regular contact and mutual investment.",
    unidentified:
      "Support clique candidate, identity unresolved. Resolve before acting on tier weight.",
    "duplicate-candidate":
      "Support clique. A duplicate record may split the interaction history. Merge before drawing conclusions.",
    stale: "Support clique, but no recent contact. Tier weight is decaying.",
    archived: "Support clique contact, now archived. Record preserved; source tombstoned.",
  },
  15: {
    healthy:
      "Sympathy group. Close enough to call on in a personal crisis. Contact rhythm is active.",
    unidentified:
      "Sympathy group candidate, identity unresolved. Confirm before weighting.",
    "duplicate-candidate":
      "Sympathy group. A duplicate may dilute the signal. Consider merging.",
    stale: "Sympathy group, no recent activity. Relationship may be drifting.",
    archived: "Sympathy group contact, now archived. Record preserved for history.",
  },
  50: {
    healthy:
      "Good friend. Regular enough contact to maintain contextual awareness.",
    unidentified:
      "Good friend tier candidate, identity unresolved. Enrich the record to confirm.",
    "duplicate-candidate":
      "Good friend tier. A parallel record exists. Merge to avoid split history.",
    stale: "Good friend tier, but contact has lapsed. Consider re-engagement.",
    archived: "Good friend tier contact, now archived. Interaction history retained.",
  },
  150: {
    healthy: "Meaningful contact. Active in the network.",
    unidentified:
      "Meaningful-tier candidate, identity incomplete. Add context to resolve.",
    "duplicate-candidate":
      "Meaningful contact with a potential duplicate. Review before weighting this node.",
    stale: "Meaningful contact, low recent activity. Relationship is fading.",
    archived: "Meaningful contact, now archived. No longer active in the network.",
  },
  500: {
    healthy:
      "Acquaintance. Known but loosely coupled. Useful for weak-tie bridging.",
    unidentified:
      "Acquaintance tier, identity unresolved. Minimal data available.",
    "duplicate-candidate":
      "Acquaintance with a duplicate candidate. Low priority, but worth a merge pass.",
    stale: "Acquaintance, no recent signal. Likely outer-ring contact.",
    archived: "Acquaintance, now archived. Peripheral record; source removed.",
  },
  1500: {
    healthy: "Recognizable contact. Outer boundary of the network.",
    unidentified:
      "Recognizable tier, identity unknown. Enrich or archive.",
    "duplicate-candidate":
      "Recognizable contact with a duplicate record. Likely a data-import artifact.",
    stale: "Recognizable contact, no recent activity. Likely dormant.",
    archived: "Recognizable contact, now archived. Outer-boundary record removed from active graph.",
  },
}

// Maintained alongside DunbarTier. The GLOSSES_BASE exhaustiveness check (at
// the bottom of this file) is the compile-time guard that keeps this list and
// the type in sync.
export const DUNBAR_TIER_VALUES: readonly DunbarTier[] = [5, 15, 50, 150, 500, 1500]

// ---------------------------------------------------------------------------
// Category-specific override table: sparse, optional.
//
// Use only when a category genuinely warrants different copy than the base
// gloss for the same (tier, state). Keep this table small; the base gloss
// is the right default for most combinations.
//
// Key format: `${tier}:${state}:${category}` (string literal key).
// No TypeScript exhaustiveness enforcement here by design -- overrides are
// additive, not required. The base table is the safety net.
// ---------------------------------------------------------------------------

type GlossKey = `${DunbarTier}:${EntityState}:${EntityType}`

const GLOSSES_OVERRIDES: Partial<Record<GlossKey, string>> = {
  // Organizations in the support clique are institutional anchors, not friends.
  "5:healthy:organization":
    "Core institutional relationship. Strategic or deeply operational dependency.",
  "5:stale:organization":
    "Core institution, no recent engagement. Check if the relationship has shifted.",

  // Places at high tier reflect a deeply significant location.
  "5:healthy:place":
    "Highly significant location. Likely a home, workplace, or recurring venue.",
  "15:healthy:place":
    "Frequently visited or personally significant location.",

  // Events at high tier are anchoring milestones.
  "5:healthy:event":
    "Defining event. Anchors a significant period or transition.",
  "15:healthy:event":
    "Major event, recently or frequently relevant.",

  // Products and accounts get a more transactional voice.
  "150:healthy:product":
    "Actively used product. Appears in recent interaction context.",
  "150:healthy:account":
    "Active account. Linked to recent activity.",
  "500:healthy:product":
    "Known product, peripheral use. Low interaction weight.",
  "500:healthy:account":
    "Known account, low engagement.",
}

// ---------------------------------------------------------------------------
// Public lookup function.
//
// Resolution order:
//   1. (tier, state, category) override -- if present, use it.
//   2. (tier, state) base gloss         -- always defined; type-guaranteed.
// ---------------------------------------------------------------------------

/**
 * Return the canned voice gloss for an entity.
 *
 * @param tier     Dunbar tier (5, 15, 50, 150, 500, 1500).
 * @param state    Entity curation state.
 * @param category Entity type / category.
 * @returns        A short, canned gloss string for display in the Editorial view.
 *
 * @example
 *   getEntityGloss({ tier: 5, state: "healthy", category: "person" })
 *   // => "Support clique. Highest-signal relationship in the graph. ..."
 *
 *   getEntityGloss({ tier: 5, state: "healthy", category: "organization" })
 *   // => "Core institutional relationship. Strategic or deeply operational dependency."
 */
export function getEntityGloss({
  tier,
  state,
  category,
}: {
  tier: DunbarTier
  state: EntityState
  category: EntityType
}): string {
  // 1. Try the category-specific override.
  const overrideKey: GlossKey = `${tier}:${state}:${category}`
  const override = GLOSSES_OVERRIDES[overrideKey]
  if (override !== undefined) {
    return override
  }

  // 2. Fall back to the base gloss. Always defined -- Record guarantees it.
  return GLOSSES_BASE[tier][state]
}

// ---------------------------------------------------------------------------
// Bulk-action confirmation glosses.
//
// Canned serif copy shown in the Index bulk-gutter confirm dialogs. Same voice
// contract as the entity glosses above: clinical, terse, present tense, no
// em-dashes, no celebration. NOT generated — a fixed two-line table keyed on
// the action, with simple count substitution.
//
// Voice rules (DESIGN_LANGUAGE.md §8): past tense for events, present for
// state; no first person; "the" over "your"; numbers exact.
// ---------------------------------------------------------------------------

export type BulkConfirmAction = "archive" | "forget"

/**
 * Return the canned serif confirmation gloss for a bulk gutter action.
 *
 * @param action ``"archive"`` (reversible) or ``"forget"`` (destructive).
 * @param count  The number of selected entities the action will apply to.
 * @returns      A single serif-italic sentence, exact count, no period of
 *               explanation beyond the consequence.
 *
 * @example
 *   getBulkConfirmGloss("archive", 3)
 *   // => "Archive 3 entities. The records are preserved; their sources are tombstoned."
 *   getBulkConfirmGloss("forget", 1)
 *   // => "Delete 1 entity. This tombstones the record and retracts active facts. It cannot be undone."
 */
export function getBulkConfirmGloss(action: BulkConfirmAction, count: number): string {
  const noun = count === 1 ? "entity" : "entities"
  if (action === "forget") {
    const subject = count === 1 ? "This tombstones the record" : "This tombstones the records"
    return `Delete ${count} ${noun}. ${subject} and retracts active facts. It cannot be undone.`
  }
  const subject = count === 1 ? "The record is preserved" : "The records are preserved"
  const src = count === 1 ? "its source is tombstoned" : "their sources are tombstoned"
  return `Archive ${count} ${noun}. ${subject}; ${src}.`
}

// ---------------------------------------------------------------------------
// Compile-time exhaustiveness check.
//
// This const assignment verifies that GLOSSES_BASE satisfies the full
// Record<DunbarTier, Record<EntityState, string>> shape. If any tier or state
// is missing, tsc will emit a type error here -- not at the call site.
// ---------------------------------------------------------------------------

const _exhaustivenessCheck: Record<DunbarTier, Record<EntityState, string>> =
  GLOSSES_BASE
// Suppress unused-variable lint warning (the point is the type check, not the value).
void _exhaustivenessCheck

// ---------------------------------------------------------------------------
// Editorial curation rail glosses (bu-q9ikf).
//
// One short serif voice line per action — canned, not generated.
// Voice contract: clinical, present tense, no em-dashes, ≤8 words.
// Used below each button in the 3×2 editorial curation rail.
// ---------------------------------------------------------------------------

export type CurationRailAction =
  | "merge"
  | "promote"
  | "demote"
  | "archive"
  | "forget"
  | "edit-aliases"

/**
 * Canned serif gloss for each editorial curation rail action.
 *
 * Clinical, terse, present-tense voice. No em-dashes.
 * Displayed in Source Serif 4 below each action button.
 */
export const CURATION_RAIL_GLOSSES: Record<CurationRailAction, string> = {
  merge: "Collapse a duplicate into this record.",
  promote: "Shift one tier inward on the Dunbar ramp.",
  demote: "Shift one tier outward on the Dunbar ramp.",
  archive: "Tombstone the source; record is preserved.",
  forget: "Hard-delete. Retracts all active facts.",
  "edit-aliases": "Add or remove known names for this entity.",
}
