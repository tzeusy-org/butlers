## Why

Butlers currently make decisions (booking flights, preparing meals, sending messages) without a centralized way to know the user's standing preferences. Preferences like "window seat", "no cilantro", or "reply formally to work contacts" are scattered across episodic memory and require the LLM to re-discover them each session. By formalizing preferences as facts within the existing SPO model, every butler can query them cheaply before acting — reducing repeated questions, wrong defaults, and wasted LLM cycles.

## What Changes

- Define a `preferences:` predicate namespace convention for the `predicate_registry` (e.g., `preferences:flight_seat`, `preferences:dietary_restriction`, `preferences:communication_style`).
- Seed standard preference predicates per butler domain (travel, health, finance, relationship, home, general) into `predicate_registry`.
- Preferences are stored as regular facts: subject = owner entity, predicate = `preferences:*`, content = the preference value, permanence = `stable` or `permanent` (low/zero decay).
- The `memory_context` tool's "Profile Facts" section already fetches owner-entity facts — preference facts surface there automatically via existing importance-based ranking.
- Provide a convenience MCP tool `set_preference` that wraps `store_fact` with preference-appropriate defaults (permanence, scope, entity resolution to owner).
- Provide a `get_preferences` query tool for butlers to fetch all active preferences, optionally filtered by domain scope or predicate pattern.
- Document the preference predicate convention and standard predicates so butlers and future extractors use consistent naming.

## Capabilities

### New Capabilities
- `user-preferences`: Preference storage, retrieval, and predicate conventions built on the SPO fact model. Covers the `preferences:` namespace, standard predicate seeds, convenience MCP tools (`set_preference`, `get_preferences`), and integration with the existing `memory_context` Profile Facts injection.

### Modified Capabilities
<!-- No existing spec-level requirements are changing. Preferences integrate via the existing
     fact storage, predicate_registry, and memory_context machinery without altering their contracts. -->

## Impact

- **predicate_registry table**: New rows seeded for `preferences:*` predicates across all butler domains.
- **Memory module MCP tools**: Two new tools (`set_preference`, `get_preferences`) added to the memory module's tool surface.
- **Butler system prompts**: Butlers should be instructed (via CLAUDE.md / manifesto updates) to check preferences before making domain-specific decisions.
- **No schema changes**: Uses existing `facts` table as-is — no new columns or tables.
- **No breaking changes**: Purely additive; existing fact storage and retrieval unaffected.
