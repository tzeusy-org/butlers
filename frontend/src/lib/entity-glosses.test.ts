/**
 * Tests for entity-glosses.ts -- canned gloss lookup [bu-wi06b]
 *
 * Verifies:
 * - getEntityGloss returns a non-empty string for every (tier, state, category)
 *   tuple (full Cartesian product coverage: 6 × 4 × 8 = 192 combinations).
 * - Category-specific overrides are returned when present.
 * - Base glosses are returned when no override exists.
 * - Return values never include em-dashes (per Brief tone guidance).
 * - No combination throws or returns undefined.
 *
 * Type-level exhaustiveness is enforced by TypeScript at compile time:
 * GLOSSES_BASE must satisfy Record<DunbarTier, Record<EntityState, string>>.
 * If a (tier, state) pair is missing, `tsc -b` fails at the assignment in
 * entity-glosses.ts. This test file does not duplicate that check -- it tests
 * runtime behavior only.
 */

import { describe, expect, it } from "vitest"
import {
  getEntityGloss,
  DUNBAR_TIER_VALUES,
  ENTITY_STATE_VALUES,
  ENTITY_TYPE_VALUES,
} from "./entity-glosses"
import type { DunbarTier, EntityState, EntityType } from "./entity-glosses"

// ---------------------------------------------------------------------------
// Dimension sets — imported from canonical source; no duplication.
// ---------------------------------------------------------------------------

const ALL_TIERS: DunbarTier[] = [...DUNBAR_TIER_VALUES]
const ALL_STATES: EntityState[] = [...ENTITY_STATE_VALUES]
const ALL_CATEGORIES: EntityType[] = [...ENTITY_TYPE_VALUES]

// ---------------------------------------------------------------------------
// Full Cartesian product: 192 combinations.
// ---------------------------------------------------------------------------

describe("getEntityGloss -- full Cartesian product (6 × 4 × 8 = 192 combos)", () => {
  for (const tier of ALL_TIERS) {
    for (const state of ALL_STATES) {
      for (const category of ALL_CATEGORIES) {
        it(`returns a non-empty string for (${tier}, ${state}, ${category})`, () => {
          const gloss = getEntityGloss({ tier, state, category })
          expect(typeof gloss).toBe("string")
          expect(gloss.length).toBeGreaterThan(0)
        })
      }
    }
  }
})

// ---------------------------------------------------------------------------
// Override behavior: known overrides return distinct copy.
// ---------------------------------------------------------------------------

describe("getEntityGloss -- category-specific overrides", () => {
  it("returns organization-specific gloss for (5, healthy, organization)", () => {
    const gloss = getEntityGloss({ tier: 5, state: "healthy", category: "organization" })
    expect(gloss).toContain("institutional")
  })

  it("returns place-specific gloss for (5, healthy, place)", () => {
    const gloss = getEntityGloss({ tier: 5, state: "healthy", category: "place" })
    expect(gloss).toContain("location")
  })

  it("returns event-specific gloss for (5, healthy, event)", () => {
    const gloss = getEntityGloss({ tier: 5, state: "healthy", category: "event" })
    expect(gloss).toContain("event")
  })

  it("returns product-specific gloss for (150, healthy, product)", () => {
    const gloss = getEntityGloss({ tier: 150, state: "healthy", category: "product" })
    expect(gloss).toContain("product")
  })

  it("returns account-specific gloss for (150, healthy, account)", () => {
    const gloss = getEntityGloss({ tier: 150, state: "healthy", category: "account" })
    expect(gloss).toContain("account")
  })
})

// ---------------------------------------------------------------------------
// Base gloss fallback: categories without an override fall back to base.
// ---------------------------------------------------------------------------

describe("getEntityGloss -- base gloss fallback", () => {
  it("(5, healthy, person) falls back to base support-clique gloss", () => {
    const gloss = getEntityGloss({ tier: 5, state: "healthy", category: "person" })
    expect(gloss).toContain("Support clique")
  })

  it("(5, healthy, group) falls back to base gloss (no group override at tier 5)", () => {
    const person = getEntityGloss({ tier: 5, state: "healthy", category: "person" })
    const group = getEntityGloss({ tier: 5, state: "healthy", category: "group" })
    // Both should fall back to the same base gloss
    expect(group).toBe(person)
  })

  it("(15, stale, other) falls back to base sympathy-group stale gloss", () => {
    const gloss = getEntityGloss({ tier: 15, state: "stale", category: "other" })
    expect(gloss).toContain("Sympathy group")
    expect(gloss.length).toBeGreaterThan(0)
  })

  it("(1500, healthy, person) falls back to recognizable base gloss", () => {
    const gloss = getEntityGloss({ tier: 1500, state: "healthy", category: "person" })
    expect(gloss).toContain("Recognizable")
  })
})

// ---------------------------------------------------------------------------
// Tone guardrails: no em-dashes (per Brief tone guidance).
// ---------------------------------------------------------------------------

describe("getEntityGloss -- tone guardrails", () => {
  it("no combination returns a string containing an em-dash", () => {
    for (const tier of ALL_TIERS) {
      for (const state of ALL_STATES) {
        for (const category of ALL_CATEGORIES) {
          const gloss = getEntityGloss({ tier, state, category })
          expect(gloss, `em-dash found in (${tier}, ${state}, ${category})`).not.toContain(
            "—",
          )
        }
      }
    }
  })
})

// ---------------------------------------------------------------------------
// Anti-temptation guardrail: verify this module contains no async exports
// (a quick structural check that LLM calls were not introduced).
// ---------------------------------------------------------------------------

describe("entity-glosses anti-temptation guardrail", () => {
  it("getEntityGloss is a synchronous function (returns string, not Promise)", () => {
    const result = getEntityGloss({ tier: 50, state: "healthy", category: "person" })
    // If this were async it would return a Promise, not a string
    expect(typeof result).toBe("string")
    expect(result).not.toBeInstanceOf(Promise)
  })
})
