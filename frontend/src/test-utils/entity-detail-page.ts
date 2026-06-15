/**
 * Shared test fixtures and helpers for the EntityDetailPage test family.
 *
 * Centralises constants and utilities that are duplicated across
 * EntityDetailPage.test.tsx, EntityDetailPage.workbench.test.tsx,
 * EntityDetailPage.merge-review.test.tsx, EntityDetailPage.forget.test.tsx,
 * and EntityDetailPage.provenance-reveal.test.tsx.
 *
 * IMPORTANT: vi.mock() factory bodies cannot be shared here because Vitest
 * hoists them to the top of each test file at compile-time.  Only plain
 * data constants and pure helper functions live here.
 */

import { vi } from "vitest";

import type { EntityDetail } from "@/api/types";

// ---------------------------------------------------------------------------
// Canonical entity fixture (entity-001, "Test Person")
// Used by workbench and merge-review test files.
// ---------------------------------------------------------------------------

export const ENTITY: EntityDetail = {
  id: "entity-001",
  canonical_name: "Test Person",
  entity_type: "person",
  aliases: [],
  roles: [],
  fact_count: 0,
  linked_contact_id: null,
  linked_contact_name: null,
  unidentified: false,
  source_butler: null,
  source_scope: null,
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
  dunbar_tier: null,
  dunbar_score: null,
  archived: false,
  metadata: {},
  recent_facts: [],
  recent_facts_total: 0,
  recent_facts_offset: 0,
  recent_facts_limit: 20,
  recent_facts_has_more: false,
  entity_info: [],
};

// ---------------------------------------------------------------------------
// Relationship-entity queue fixtures
// Used by workbench and merge-review test files.
// ---------------------------------------------------------------------------

export const DUP_QUEUE = {
  data: {
    items: [
      {
        entity_id: "entity-001",
        canonical_name: "Test Person",
        entity_type: "person",
        bucket: "duplicate-candidate",
        evidence: { predicate: "has-email", shared_value: "x@y.com", peer_entity_ids: ["peer-002"] },
        last_seen: null,
      },
    ],
    total: 1,
    limit: 100,
    offset: 0,
  },
};

export const EMPTY_QUEUE = { data: { items: [], total: 0, limit: 100, offset: 0 } };

// ---------------------------------------------------------------------------
// localStorage mock factory
// Used by EntityDetailPage.test.tsx and EntityDetailPage.forget.test.tsx
// which run without jsdom's real localStorage (renderToStaticMarkup / Node env).
// ---------------------------------------------------------------------------

export type LocalStorageMock = {
  getItem: ReturnType<typeof vi.fn>;
  setItem: ReturnType<typeof vi.fn>;
  removeItem: ReturnType<typeof vi.fn>;
  clear: ReturnType<typeof vi.fn>;
};

/**
 * Returns a fresh in-memory localStorage-shaped mock.
 *
 * Call `Object.defineProperty(globalThis, "localStorage", { value: makeLocalStorageMock(), writable: true })`
 * at module level to wire it up.  Each file that needs this should call
 * makeLocalStorageMock() once and hold the reference to assert against.
 */
export function makeLocalStorageMock(): LocalStorageMock {
  let store: Record<string, string | null> = {};
  return {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store[key] = value;
    }),
    removeItem: vi.fn((key: string) => {
      delete store[key];
    }),
    clear: vi.fn(() => {
      store = {};
    }),
  };
}
