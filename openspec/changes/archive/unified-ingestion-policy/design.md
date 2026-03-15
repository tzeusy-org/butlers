## Context

The ingestion pipeline currently has two independent filtering systems that overlap in capability but differ in semantics, pipeline position, and UX:

1. **Triage rules** — `triage_rules` table, global scope, post-ingest/pre-LLM. First-match-wins evaluation with rich actions (skip, metadata_only, route_to, etc.). CRUD via `/triage-rules/*`. Frontend: main table on Filters tab. Evaluator: `roster/switchboard/tools/triage/evaluator.py`, cached via `TriageRuleCache` (60s refresh).

2. **Source filters** — `source_filters` + `connector_source_filters` tables, per-connector scope, pre-ingest. Blacklist/whitelist composition with binary block/allow. CRUD via `/source-filters/*` + assignment via `/connectors/*/filters`. Frontend: buried in "Manage Filters" sheet + ConnectorFiltersDialog. Evaluator: `src/butlers/connectors/source_filter.py`, TTL-cached (300s refresh).

Both handle `sender_domain` and `sender_address` matching. A user wanting to block a domain must choose between two paths. The `/ingestion?tab=filters` page shows both systems but with no clear relationship between them.

Thread affinity routing is orthogonal and preserved unchanged (runs before any rule evaluation in the post-ingest pipeline).

## Goals / Non-Goals

**Goals:**
- Single `ingestion_rules` table replacing all three existing tables
- One evaluator, one cache, one API, one UI
- Preserve the pipeline position distinction: connector-scoped rules block **before any LLM sees the data**; global rules route/skip/queue **after ingest but before LLM**
- Zero data loss: migrate existing triage rules and source filters into unified table
- Simpler composition model: first-match-wins everywhere (drop blacklist/whitelist)

**Non-Goals:**
- Changing thread affinity routing (preserved as-is)
- Per-message content filtering (body/subject) — remains out of scope
- Gmail label filtering — orthogonal, stays in its own panel
- Real-time rule push to connectors (TTL polling is sufficient)
- Per-rule match counters in the dashboard (Prometheus only)
- Changing the connector ↔ Switchboard ingest protocol

## Decisions

### D1: Single table with `scope` column (not separate tables per pipeline stage)

**Choice:** One `ingestion_rules` table with a `scope TEXT NOT NULL` field. Values: `'global'` or `'connector:<connector_type>:<endpoint_identity>'`.

**Alternatives considered:**
- *Keep two tables with unified API facade:* Simpler migration but perpetuates the split at the data layer; queries that span both scopes require UNION; cache loading more complex.
- *Scope as a FK to a scopes table:* Over-normalized; scope is a simple discriminator, not an entity.

**Rationale:** A single table means one index, one cache load query, one set of CRUD endpoints, and one set of constraints. The `scope` column is a simple discriminator that the evaluator partitions on at load time. Connector-scoped rules are loaded by the connector evaluator; global rules are loaded by the triage evaluator. Both read from the same table.

### D2: `scope` format — `global` vs `connector:<type>:<identity>`

**Choice:** Free-text `scope` column with a CHECK constraint: `scope = 'global' OR scope LIKE 'connector:%'`. The connector scope encodes both connector_type and endpoint_identity in a single string (e.g., `connector:gmail:gmail:user:dev`).

**Alternatives considered:**
- *Separate `scope_type` + `scope_target` columns:* More normalized but adds joins/filters complexity for no real benefit. The compound string is human-readable and directly usable as a cache key.
- *Enum for scope:* Would require a migration every time a new connector is registered.

**Rationale:** The compound string is the natural cache partition key. Connectors already know their `connector_type:endpoint_identity` tuple. Extracting `connector_type` from the scope string is trivial (split on `:`, take index 1) for compatibility checks.

### D3: Connector-scoped rules only support `action = 'block'`

**Choice:** Rules with `scope LIKE 'connector:%'` are constrained to `action = 'block'`. The CHECK constraint enforces this at the DB level.

**Alternatives considered:**
- *Allow all actions at connector scope:* Connectors don't have Switchboard context (butler registry, queue state) needed to execute `route_to` or `low_priority_queue`. Allowing these actions would require connectors to understand routing, violating their single responsibility.
- *Allow `skip` at connector scope (equivalent to block):* `skip` and `block` would be semantically identical at connector scope. Having both adds confusion. `block` is the clearer name for "drop before ingest."

**Rationale:** Connectors have one job when evaluating rules: should this message enter the system or not? Binary block/pass. Routing decisions belong to the global post-ingest evaluator which has full Switchboard context.

### D4: First-match-wins composition (drop blacklist/whitelist)

**Choice:** All rules evaluated in `priority ASC, created_at ASC, id ASC` order. First matching rule's action is applied. No match = implicit `pass_through`.

**Alternatives considered:**
- *Preserve blacklist/whitelist composition for connector-scoped rules:* Maintains backward compatibility but means two different composition models in one table, requiring the evaluator to branch on scope. Harder to reason about.
- *Layered evaluation (block layer → route layer):* Adds complexity with no clear benefit over priority ordering.

**Rationale:** First-match-wins with priority ordering is strictly more expressive than blacklist/whitelist and simpler to reason about. Users who want whitelist semantics create allow rules for permitted senders (action = `pass_through`, explicit) and a catch-all block rule at a higher priority number. The evaluator is a single loop — no mode branching.

**Migration note:** Existing source filters are converted as follows:
- Each blacklist pattern → one rule with `action = 'block'`, priority preserved from `connector_source_filters.priority`
- Each whitelist pattern → one rule with `action = 'pass_through'` (explicit allow), plus one catch-all `block` rule at priority = whitelist priority + 1000

### D5: Unified evaluator with scope-aware loading

**Choice:** Single `IngestionPolicyEvaluator` class in `src/butlers/ingestion_policy.py` that handles both connector-scoped and global evaluation. The evaluator is instantiated with a scope string and loads only rules matching that scope.

**Interface:**

```python
class IngestionPolicyEvaluator:
    def __init__(
        self,
        scope: str,                    # "global" or "connector:gmail:gmail:user:dev"
        db_pool: asyncpg.Pool | None,
        refresh_interval_s: float = 60,  # unify on 60s (was 300 for source filters, 60 for triage)
    ) -> None: ...

    async def ensure_loaded(self) -> None: ...

    def evaluate(self, envelope: IngestionEnvelope) -> PolicyDecision: ...
```

**Alternatives considered:**
- *Two evaluator classes sharing a base:* Adds indirection for no benefit. The evaluation logic is identical (iterate rules, match condition, return action). Only the scope filter in the SQL query differs.
- *Keep SourceFilterEvaluator for connector scope, use new evaluator for global:* Perpetuates the split at the code layer. Defeats the purpose of unification.

**Rationale:** The evaluation loop is the same: load rules for this scope, iterate in priority order, test each rule's condition against the envelope, return first match. The only difference is what scope is passed to the SQL WHERE clause. One class, one test suite, one set of edge cases to handle.

### D6: Unified envelope — `IngestionEnvelope`

**Choice:** A single envelope dataclass that carries all fields needed by any rule_type:

```python
@dataclass(frozen=True)
class IngestionEnvelope:
    sender_address: str       # normalized email or empty
    source_channel: str       # "email", "telegram", "discord"
    headers: dict[str, str]   # email headers (case-insensitive keys), empty for non-email
    mime_parts: list[str]     # MIME types, empty for non-email
    thread_id: str | None     # external thread ID
    raw_key: str              # raw key for substring/chat_id/channel_id matching
```

**Rationale:** Connectors populate only the fields relevant to their channel. The evaluator extracts the appropriate key per `rule_type` internally (same pattern as current `extract_gmail_filter_key` / `extract_telegram_filter_key`). The `raw_key` field carries the connector-specific opaque key (From header for Gmail, chat_id string for Telegram, channel_id string for Discord) for rule types that need it.

### D7: `rule_type` expanded — open TEXT, not enum

**Choice:** `rule_type TEXT NOT NULL` with no CHECK constraint. Valid types enforced at the API layer. This continues the design decision from source filters (D1 in the connector-source-filters change) where `source_key_type` was unconstrained TEXT.

**Known types at launch:**
- `sender_domain` — extract domain from sender_address, exact/suffix match
- `sender_address` — normalized email, exact match
- `header_condition` — email header presence/equals/contains
- `mime_type` — exact or wildcard MIME match
- `substring` — case-insensitive substring search in raw_key
- `chat_id` — exact string equality (Telegram)
- `channel_id` — exact string equality (Discord)

**Rationale:** Adding a new connector type (e.g., WhatsApp with `phone_number`) requires no migration — just a new pattern matcher in the evaluator and an API validation update.

### D8: API design — `/ingestion-rules/*`

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/ingestion-rules` | List rules with optional filters: `scope`, `rule_type`, `action`, `enabled` |
| POST | `/ingestion-rules` | Create rule. Validates condition schema, action constraints per scope. |
| GET | `/ingestion-rules/{id}` | Get single rule |
| PATCH | `/ingestion-rules/{id}` | Partial update (condition, action, priority, enabled, scope) |
| DELETE | `/ingestion-rules/{id}` | Soft-delete (set `deleted_at`, `enabled=false`) |
| POST | `/ingestion-rules/test` | Dry-run: evaluate a test envelope against active rules |

**Scope-aware validation on create/update:**
- `scope = 'global'` → `action` must be one of: `skip`, `metadata_only`, `low_priority_queue`, `pass_through`, `route_to:<butler>`
- `scope LIKE 'connector:%'` → `action` must be `block`
- `rule_type` must be compatible with the scope's connector type (e.g., `chat_id` only valid for `connector:telegram-bot:*`)

**Cache invalidation:** Mutations (create/update/delete) call `evaluator.invalidate()` on the global cache. Connector caches refresh on their TTL cycle (they poll the same table).

### D9: Migration strategy — zero downtime, zero data loss

**Phase 1: Create new table + migrate data (single migration file)**

```sql
CREATE TABLE ingestion_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    condition JSONB NOT NULL,
    action TEXT NOT NULL,
    priority INTEGER NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    name TEXT,                           -- optional human label (carried from source_filters.name)
    description TEXT,                    -- optional (carried from source_filters.description)
    created_by TEXT NOT NULL DEFAULT 'migration',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,

    CONSTRAINT ingestion_rules_scope_check
        CHECK (scope = 'global' OR scope LIKE 'connector:%'),

    CONSTRAINT ingestion_rules_connector_action_check
        CHECK (scope = 'global' OR action = 'block'),

    CONSTRAINT ingestion_rules_priority_check
        CHECK (priority >= 0)
);

CREATE INDEX ix_ingestion_rules_scope_active
    ON ingestion_rules (scope, priority, created_at, id)
    WHERE enabled = TRUE AND deleted_at IS NULL;

CREATE INDEX ix_ingestion_rules_global_active
    ON ingestion_rules (priority, created_at, id)
    WHERE scope = 'global' AND enabled = TRUE AND deleted_at IS NULL;
```

**Data migration (in same migration):**

```sql
-- 1. Migrate triage_rules → ingestion_rules (scope = 'global')
INSERT INTO ingestion_rules (id, scope, rule_type, condition, action, priority, enabled, created_by, created_at, updated_at, deleted_at)
SELECT id, 'global', rule_type, condition, action, priority, enabled, created_by, created_at, updated_at, deleted_at
FROM triage_rules;

-- 2. Migrate source_filters × connector_source_filters → ingestion_rules (scope = 'connector:...')
--    Each enabled assignment becomes one block rule per pattern (blacklist)
--    or one pass_through rule per pattern + catch-all block (whitelist)
-- (See migration script for full logic — handled in Python, not raw SQL)
```

**Phase 2: Switch evaluators** — deploy new evaluator code reading `ingestion_rules`.

**Phase 3: Drop old tables** — separate migration after Phase 2 is stable.

**Rollback:** Phase 1 is additive (new table alongside old). If Phase 2 fails, revert code to old evaluators; old tables are still present and populated.

### D10: Frontend — unified rules table

**Choice:** The `/ingestion?tab=filters` page shows a single rules table with columns:

| Priority | Scope | Condition | Action | Enabled | Actions |
|----------|-------|-----------|--------|---------|---------|

- **Scope** displays as badge: `Global` (default) or `gmail:user:dev` (connector-scoped, extracted from scope string)
- **Scope filter** dropdown above table: "All" / "Global only" / per-connector (auto-populated from distinct scopes in rules)
- **Condition** formatted as readable text (same as current triage rules table)
- **Action** color-coded badge: `block` (red), `skip` (gray), `route_to:finance` (blue), etc.
- **Rule editor drawer** extended with scope selector (Global / Connector dropdown → endpoint picker)
- **Connector detail page** shows same table filtered to `scope = 'connector:<type>:<identity>'`, with a "+ Add Rule" button that pre-fills scope

**Removed components:**
- `ManageSourceFiltersPanel` — no longer needed (named filters replaced by rules)
- `ConnectorFiltersDialog` — replaced by scoped rules table on connector detail page
- `use-source-filters.ts` hooks — replaced by unified hooks
- `use-triage.ts` hooks — replaced by unified hooks

### D11: Metrics consolidation

**Choice:** Merge triage and source filter metrics into a unified set:

| Metric | Labels | Replaces |
|--------|--------|----------|
| `butlers.ingestion.rule_matched` | `scope`, `rule_type`, `action`, `source_channel` | `triage.rule_matched` + `connector_source_filter_total{action=blocked}` |
| `butlers.ingestion.rule_pass_through` | `scope`, `source_channel`, `reason` | `triage.pass_through` + `connector_source_filter_total{action=allowed}` |
| `butlers.ingestion.evaluation_latency_ms` | `scope`, `result` | `triage.evaluation_latency_ms` (new: connector scope too) |

**Cardinality controls:** `scope` label bounded to `global` or `connector:<type>` (strip endpoint_identity). `action` stripped of butler name (`route_to:finance` → `route_to`).

### D12: Refresh interval — unify on 60 seconds

**Choice:** Both connector-scoped and global evaluators use 60s refresh (configurable via `INGESTION_POLICY_REFRESH_INTERVAL_S`).

**Rationale:** Source filters used 300s; triage cache used 60s. 60s is a reasonable balance — fast enough that rule changes take effect within a minute, slow enough to not hammer the DB. One env var instead of two.

## Risks / Trade-offs

**[Blacklist/whitelist users must restructure rules]** → Whitelist semantics are still achievable via explicit allow rules + catch-all block. The migration auto-generates this pattern. The UX is slightly more verbose (N+1 rules instead of one named filter with N patterns) but more transparent.

**[Migration complexity for whitelist source filters]** → A single whitelist filter with 5 patterns becomes 5 pass_through rules + 1 catch-all block rule. This fan-out could surprise users who had compact named filters. → Mitigation: migration logs a summary of conversions; dashboard shows a one-time banner explaining the change.

**[Connector-scoped rules only support `block`]** → Users who wanted connector-level `skip` or `route_to` must use global rules instead. → This was already the case (source filters were binary block/allow); no regression.

**[Single table could grow large]** → Most deployments will have <100 rules total. The scope-partitioned index ensures efficient loading. Not a concern at foreseeable scale.

**[Breaking API changes]** → All three old endpoint families removed. → Mitigation: the dashboard is the only consumer; no external API clients. Deploy frontend + backend atomically.

## Migration Plan

1. **Migration file** — create `ingestion_rules` table, insert migrated data from `triage_rules` + `source_filters`/`connector_source_filters`, verify row counts match expectations.
2. **Backend** — deploy new evaluator, API endpoints, and updated connector integration. Old endpoints return 410 Gone with redirect hint to new endpoints.
3. **Frontend** — deploy unified rules table, remove old components.
4. **Cleanup migration** — drop `triage_rules`, `source_filters`, `connector_source_filters` tables after 1 week soak period.
5. **Rollback** — if issues found post-deploy, revert backend/frontend to old evaluators. Old tables remain populated (Phase 1 migration is additive). Re-drop `ingestion_rules` if needed.

## Open Questions

- **Seed rules:** Should the 9 existing seed triage rules be re-inserted into `ingestion_rules` with well-known UUIDs, or should they be migrated alongside user-created rules? (Leaning toward: migrate them normally; the seed insertion logic in the old migration already ran.)
- **`name` field usage:** Source filters had mandatory `name`; triage rules did not. Should the unified table make `name` optional (nullable) or require it? (Leaning toward: optional. Existing triage rules get `name = NULL`; existing source filters keep their names. UI shows name if present, otherwise auto-generates from condition.)
- **`connector-source-filters` OpenSpec change:** Should its specs be formally archived/superseded, or just left as historical? (Leaning toward: add a `superseded-by: unified-ingestion-policy` note to its proposal.md.)
