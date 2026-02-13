## ADDED Requirements

### Requirement: Tenant boundary and caller identity for memory tools

All memory tools (registered by the memory module on hosting butler MCP servers) SHALL resolve `tenant_id` from authenticated request context. Non-admin callers SHALL NOT select arbitrary tenant IDs via tool arguments.

#### Scenario: Non-admin tenant override rejected
- **WHEN** a non-admin caller attempts to pass a tenant selector in tool args
- **THEN** the request SHALL be rejected with a validation or authorization error

#### Scenario: Admin cross-tenant access requires explicit elevation
- **WHEN** an admin caller invokes a tool with explicit cross-tenant intent
- **THEN** the operation SHALL be allowed only with elevated authorization context

### Requirement: memory_store_episode tool

The hosting butler MCP server SHALL expose a `memory_store_episode(content, butler, session_id?, importance?)` tool when memory module is enabled. The tool SHALL generate an embedding, populate the search vector, set `expires_at` to now + configured TTL (default 7 days), and return the episode ID.

#### Scenario: Store episode with defaults
- **WHEN** `memory_store_episode(content="User asked about recipes", butler="general")` is called
- **THEN** an episode SHALL be created with the given content, butler, importance=5.0, and expires_at 7 days from now
- **AND** the episode ID SHALL be returned

#### Scenario: Store episode with custom importance
- **WHEN** `memory_store_episode(content="User revealed severe allergy", butler="health", importance=9.0)` is called
- **THEN** an episode SHALL be created with importance=9.0

### Requirement: memory_store_fact tool

The hosting butler MCP server SHALL expose a `memory_store_fact(subject, predicate, content, importance?, permanence?, scope?, tags?)` tool when memory module is enabled. The tool SHALL generate an embedding, populate the search vector, map permanence to decay_rate, check for subject-predicate conflicts (triggering supersession if found), and return the fact ID.

#### Scenario: Store fact with supersession
- **WHEN** `memory_store_fact(subject="user", predicate="favorite_color", content="blue")` is called
- **AND** an active fact with subject="user" and predicate="favorite_color" exists
- **THEN** the existing fact SHALL be superseded
- **AND** the new fact ID SHALL be returned

#### Scenario: Store fact with permanence mapping
- **WHEN** `memory_store_fact(subject="user", predicate="name", content="John", permanence="permanent")` is called
- **THEN** a fact SHALL be created with decay_rate=0.0

### Requirement: memory_store_rule tool

The hosting butler MCP server SHALL expose a `memory_store_rule(content, scope?, tags?)` tool when memory module is enabled that stores a new rule as a candidate with confidence=0.5. The tool SHALL generate an embedding, populate the search vector, and return the rule ID.

#### Scenario: Store new rule
- **WHEN** `memory_store_rule(content="Always confirm before sending messages", scope="global")` is called
- **THEN** a rule SHALL be created with maturity='candidate', confidence=0.5
- **AND** the rule ID SHALL be returned

### Requirement: memory_search tool

The hosting butler MCP server SHALL expose a `memory_search(query, types?, scope?, mode?, limit?, min_confidence?)` tool when memory module is enabled. `types` defaults to all three types. `mode` defaults to 'hybrid'. `limit` defaults to 20. The tool SHALL return scored results with type, ID, content, score, and confidence.

#### Scenario: Search across all types
- **WHEN** `memory_search(query="diet preferences")` is called
- **THEN** results SHALL include matching episodes, facts, and rules within the caller tenant boundary

#### Scenario: Search filtered to facts only
- **WHEN** `memory_search(query="diet", types=["fact"])` is called
- **THEN** results SHALL only include facts

### Requirement: memory_recall tool

The hosting butler MCP server SHALL expose a `memory_recall(topic, scope?, limit?)` tool when memory module is enabled that performs composite-scored retrieval of facts and rules (not episodes). This is the primary tool CC instances SHALL use. The tool SHALL bump reference counts on returned results.

#### Scenario: Recall returns composite-scored results
- **WHEN** `memory_recall(topic="user dietary needs", scope="health")` is called
- **THEN** results SHALL be facts and rules scored by the composite formula (relevance, importance, recency, confidence)
- **AND** results SHALL be scoped to caller tenant + (`global`, `health`)

### Requirement: memory_get tool

The hosting butler MCP server SHALL expose a `memory_get(type, id)` tool when memory module is enabled that retrieves a specific memory by type ('episode', 'fact', 'rule') and UUID. The tool SHALL bump reference count and return the full record.

#### Scenario: Get specific fact
- **WHEN** `memory_get(type="fact", id="<uuid>")` is called with a valid fact ID
- **THEN** the full fact record SHALL be returned
- **AND** its reference_count SHALL be incremented

#### Scenario: Get nonexistent memory
- **WHEN** `memory_get(type="fact", id="<nonexistent-uuid>")` is called
- **THEN** the tool SHALL return an error indicating the memory was not found

### Requirement: memory_confirm tool

The hosting butler MCP server SHALL expose a `memory_confirm(type, id)` tool when memory module is enabled that resets `last_confirmed_at` to the current timestamp for a fact or rule. This restores confidence to its original level by resetting the decay clock.

#### Scenario: Confirm fact resets decay clock
- **WHEN** `memory_confirm(type="fact", id="<uuid>")` is called
- **THEN** the fact's `last_confirmed_at` SHALL be set to now
- **AND** its effective confidence SHALL equal its stored `confidence` value (no decay)

#### Scenario: Confirm episode rejected
- **WHEN** `memory_confirm(type="episode", id="<uuid>")` is called
- **THEN** the tool SHALL return an error (episodes do not have confidence decay)

### Requirement: memory_mark_helpful tool

The hosting butler MCP server SHALL expose a `memory_mark_helpful(rule_id)` tool when memory module is enabled that increments a rule's `success_count` and `applied_count`, recalculates `effectiveness_score`, updates `last_applied_at`, and evaluates maturity promotion.

#### Scenario: Mark rule helpful increments counts
- **WHEN** `memory_mark_helpful(rule_id="<uuid>")` is called on a rule with success_count=4
- **THEN** its success_count SHALL be 5 and applied_count SHALL be incremented

### Requirement: memory_mark_harmful tool

The hosting butler MCP server SHALL expose a `memory_mark_harmful(rule_id, reason?)` tool when memory module is enabled that increments a rule's `harmful_count` and `applied_count`, recalculates `effectiveness_score` (with 4x harmful weight), updates `last_applied_at`, and evaluates maturity demotion or anti-pattern inversion.

#### Scenario: Mark rule harmful with reason
- **WHEN** `memory_mark_harmful(rule_id="<uuid>", reason="caused incorrect response")` is called
- **THEN** its harmful_count SHALL be incremented
- **AND** effectiveness_score SHALL be recalculated as `success / (success + 4 × harmful + 0.01)`

### Requirement: memory_forget tool

The hosting butler MCP server SHALL expose a `memory_forget(type, id)` tool when memory module is enabled that soft-deletes a memory while preserving auditability and dashboard visibility. Facts SHALL use canonical validity `retracted` (legacy `forgotten` is accepted only as alias). Rules and episodes SHALL use retrieval-excluded tombstone semantics per schema.

#### Scenario: Forget a fact
- **WHEN** `memory_forget(type="fact", id="<uuid>")` is called
- **THEN** the fact's validity SHALL be `retracted`
- **AND** subsequent memory_recall calls SHALL NOT return this fact

#### Scenario: Forget operation emits audit event
- **WHEN** `memory_forget(type="fact", id="<uuid>")` is called
- **THEN** a `memory_events` row SHALL be appended for the forget transition

### Requirement: memory_stats tool

The hosting butler MCP server SHALL expose a `memory_stats(scope?)` tool when memory module is enabled that returns counts and health indicators: total/active/fading/expired facts, total/candidate/established/proven rules, total/unconsolidated/expired episodes, and episode backlog age.

#### Scenario: Stats with scope
- **WHEN** `memory_stats(scope="health")` is called
- **THEN** counts SHALL reflect only memories scoped to 'global' and 'health'

### Requirement: memory_context tool

The hosting butler MCP server SHALL expose a `memory_context(trigger_prompt, butler, token_budget?)` tool when memory module is enabled that builds a formatted memory block for CC system prompt injection. The tool SHALL embed the trigger prompt, query top-scored facts and rules scoped to the butler, and format them within the token budget (default 3000 tokens). Output SHALL be ordered by score (highest first) with facts, rules, and recent episodes in separate sections. Token budgeting SHALL use a deterministic tokenizer (not character-count approximation), with deterministic tie-breakers (`score DESC`, `created_at DESC`, `id ASC`) and configurable section quotas.

#### Scenario: Context within token budget
- **WHEN** `memory_context(trigger_prompt="Help user with diet", butler="health", token_budget=3000)` is called
- **THEN** the returned text block SHALL be at most 3000 tokens
- **AND** SHALL contain sections for facts, rules, and recent episodes

#### Scenario: Context prioritizes highest-scored memories
- **WHEN** the token budget cannot fit all relevant memories
- **THEN** the lowest-scored memories SHALL be omitted first

#### Scenario: Context output remains deterministic under score ties
- **WHEN** two memories have equal score for the same request
- **THEN** ordering SHALL follow the deterministic tie-breakers (`created_at DESC`, then `id ASC`)
