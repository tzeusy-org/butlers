import type { Fact } from "../../../src/api/types";

/** Complete synthetic fact; each owner retains its scenario-specific fields. */
export function makeFact(overrides: Partial<Fact> = {}): Fact {
  return { source_episode_status: null, superseded_by: null, content_authority: null, authority_entity_id: null, authority_entity_name: null, tenant_id: "owner", request_id: null, retention_class: "operational", sensitivity: "normal", valid_at: null, invalid_at: null, idempotency_key: null,
    id: "synthetic-fact", subject: "owner", predicate: "prefers", content: "Synthetic preference",
    importance: 5, confidence: 0.9, decay_rate: 0, permanence: "stable",
    source_butler: null, source_episode_id: null, session_id: null, supersedes_id: null,
    entity_id: null, entity_name: null, object_entity_id: null, object_entity_name: null,
    validity: "active", scope: "owner", reference_count: 0,
    created_at: "2026-01-01T00:00:00Z", last_referenced_at: null, last_confirmed_at: null,
    tags: [], metadata: {}, ...overrides,
  };
}
