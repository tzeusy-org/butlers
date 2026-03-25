## Context

The Butlers memory module already stores semantic knowledge as SPO (Subject-Predicate-Object) facts in PostgreSQL, with a `predicate_registry` table for consistent predicate naming, a `permanence` system controlling decay rates, and a `memory_context` tool that injects owner-entity "Profile Facts" into every spawned LLM session. User preferences are a natural specialization of this existing machinery — they are simply facts with a well-known predicate namespace, high permanence, and high importance so they surface reliably in runtime context.

Today, butlers learn preferences ad hoc from episodic consolidation (e.g., "user mentioned preferring window seats"). These get stored as generic facts with standard permanence and decay. There is no convention for predicate naming, no guaranteed surfacing, and no way for a butler to quickly query "all preferences relevant to my domain" before acting.

## Goals / Non-Goals

**Goals:**
- Define a `preferences:` predicate namespace convention with standard predicates per butler domain.
- Seed preference predicates into `predicate_registry` via a memory module migration.
- Provide a `set_preference` MCP tool that wraps `store_fact` with preference-appropriate defaults (owner entity, `stable` permanence, high importance, correct scope).
- Provide a `get_preferences` MCP tool for domain-scoped preference lookup (all active preference facts, optionally filtered by scope or predicate pattern).
- Ensure preferences naturally surface in `memory_context` Profile Facts with no changes to that code path.

**Non-Goals:**
- No new database tables or columns — preferences use the existing `facts` table.
- No UI for preference management (dashboard work is out of scope for this change).
- No automatic preference extraction from episodes — that is a future consolidation enhancement.
- No cross-butler preference synchronization beyond what the `shared.memory_catalog` already provides.
- No preference conflict resolution (if a user sets contradictory preferences, the latest supersedes via normal SPO supersession).

## Decisions

### D1: Namespace convention — `preferences:` prefix in predicate name

Preference predicates use the format `preferences:<domain>_<name>`, e.g., `preferences:travel_flight_seat`, `preferences:health_dietary_restriction`, `preferences:general_communication_style`.

**Rationale:** The colon separator matches the existing predicate taxonomy pattern (health, finance, relationship domains already use flat names but domain-scope isolation). The `preferences:` prefix makes predicates instantly recognizable as user preferences vs. observed facts, and allows `LIKE 'preferences:%'` queries without scanning all predicates. The domain segment after `preferences:` groups related preferences for per-butler retrieval.

**Alternatives considered:**
- *Flat predicates like `pref_flight_seat`*: Less readable, harder to query as a group, mixes with existing predicate namespace.
- *Separate `preference_type` metadata field*: Requires metadata-level filtering instead of simple predicate queries; slower.
- *Dedicated scope value `preferences` instead of predicate prefix*: Loses the ability to associate preferences with their domain scope (a travel preference should be `scope='travel'`, not `scope='preferences'`).

### D2: Scope follows the butler domain, not a new "preferences" scope

Preference facts use the same `scope` values as their domain: `travel`, `health`, `finance`, `relationship`, `home`, `global`. This means a travel butler querying `scope='travel'` gets both domain data facts and travel preferences together.

**Rationale:** Butlers already filter by scope. If preferences had their own scope, every butler would need to query two scopes. Domain-aligned scoping means `get_preferences(scope='travel')` is just `SELECT ... WHERE predicate LIKE 'preferences:%' AND scope = 'travel'`.

**Alternatives considered:**
- *Dedicated `preferences` scope*: Cleaner conceptually but doubles query complexity for every butler.

### D3: Permanence defaults to `stable` (decay_rate=0.002)

Preferences stored via `set_preference` default to `permanence='stable'`, which maps to `decay_rate=0.002` (very slow decay). Users can override to `permanent` (zero decay) for immutable preferences like dietary restrictions or accessibility needs.

**Rationale:** `stable` gives near-permanent retention while still allowing very old unconfirmed preferences to eventually fade. `permanent` is available for things that genuinely never change (e.g., food allergies). `standard` decays too fast for preferences that may not be re-confirmed for months.

### D4: Importance defaults to 8.0 for preference facts

Preference facts get a high default importance (8.0 out of 10) to ensure they rank highly in the `memory_context` Profile Facts section, which sorts by `importance DESC`.

**Rationale:** Profile Facts has a 30% budget allocation and fetches the top-50 owner facts by importance. Generic consolidated facts typically have importance 5.0. Setting preferences to 8.0 ensures they appear before most observational facts without displacing critical health/safety facts that might be manually set to 9-10.

### D5: `set_preference` tool auto-resolves owner entity

The `set_preference` tool resolves the owner entity ID internally (via the same bootstrap used by domain butlers: `SELECT entity_id FROM shared.contacts WHERE roles @> '["owner"]' LIMIT 1`). The caller just provides predicate, value, and optional scope.

**Rationale:** Preferences are always about the owner user. Requiring callers to pass `entity_id` adds friction and error potential. The owner entity is already cached at butler startup.

### D6: `get_preferences` returns structured preference list, not raw facts

`get_preferences` returns a simplified list: `[{predicate, value, scope, importance, permanence, updated_at}]`. It filters to `validity='active'` and `predicate LIKE 'preferences:%'`.

**Rationale:** Butlers querying preferences need a clean, actionable list — not full fact records with embeddings, decay rates, and supersession chains. The simplified format is cheaper to serialize and easier for LLMs to consume.

### D7: Predicate registry seeds via a single new migration

All standard preference predicates are seeded in one migration file (`seed_preference_predicates`), following the pattern of existing seed migrations (`009_health_predicates.py`, `010_finance_predicates.py`, `011_relationship_predicates.py`).

**Rationale:** One migration keeps the predicate registry consistent. New preference domains can add predicates in future migrations.

## Risks / Trade-offs

**[Risk: Predicate proliferation]** The `preferences:` namespace is open-ended; users/LLMs might create ad-hoc predicates outside the registry.
-> **Mitigation:** The registry is advisory (not enforced). Consolidation and extraction prompts will prefer registered predicates. Wild predicates still work but may not surface in domain-specific queries.

**[Risk: Profile Facts budget saturation]** If a user sets many preferences (50+), they could crowd out other important profile facts from the memory_context budget.
-> **Mitigation:** The 30% budget cap and importance-based ranking naturally throttle. Preferences at importance 8.0 will share space with other high-importance facts. If this becomes a problem, a future change can add a dedicated "Preferences" section to memory_context.

**[Risk: Supersession on repeated set_preference]** Calling `set_preference` with the same predicate replaces the old value (normal SPO supersession). Users might not realize the old value is gone.
-> **Mitigation:** This is desirable behavior — preferences represent current state. The superseded fact chain preserves history for audit if needed.

**[Trade-off: No dedicated memory_context section]** Preferences appear mixed into "Profile Facts" rather than a separate "Preferences" section.
-> **Accepted:** The current Profile Facts section is the right place — it already surfaces owner-entity facts. Adding a separate section is a future optimization if preference volume warrants it.

## Open Questions

- **Q1: Should there be a `delete_preference` tool or is `memory_forget` sufficient?** Leaning toward `memory_forget` being sufficient since it sets `validity='retracted'`, but a convenience alias might reduce friction.
- **Q2: Should the memory consolidation pipeline automatically promote preference-shaped episodes to preference facts?** This is explicitly a non-goal for this change but worth tracking as a follow-up.
