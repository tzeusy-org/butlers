## Why

Two independent filtering systems — **triage rules** (`triage_rules` table, post-ingest pre-LLM) and **source filters** (`source_filters` + `connector_source_filters`, per-connector pre-ingest) — create a confusing dual-model experience. Both handle `sender_domain` and `sender_address` matching but at different pipeline stages with different semantics, different UIs, different APIs, and different composition rules (first-match-wins vs blacklist/whitelist). A user wanting to block `spammer.com` must choose between two paths that do nearly the same thing. The `/ingestion?tab=filters` page compounds this by showing triage rules as the main content and burying source filters behind a "Manage Filters" sheet.

Unifying into a single **Ingestion Policy** model gives users one mental model, one rules table, and one API — while preserving the critical pipeline distinction: **rules that block messages intervene before any LLM sees the data**, dropping them at the connector level before Switchboard submission.

## What Changes

- **NEW** — Single `ingestion_rules` table replacing `triage_rules`, `source_filters`, and `connector_source_filters`
- **NEW** — Unified `IngestionPolicyEvaluator` replacing both `SourceFilterEvaluator` and the triage rule evaluator
- **NEW** — Unified REST API for ingestion rule CRUD, replacing both source filter and triage rule endpoints
- **BREAKING** — Remove `source_filters` and `connector_source_filters` tables (superseded)
- **BREAKING** — Remove `triage_rules` table (superseded)
- **BREAKING** — Remove source filter CRUD/assignment API endpoints (`/source-filters/*`, `/connectors/*/filters`)
- **BREAKING** — Remove triage rule API endpoints (`/triage-rules/*`)
- **BREAKING** — Remove `SourceFilterEvaluator` module (`src/butlers/connectors/source_filter.py`)
- **Modified** — Frontend Filters tab becomes a single rules table with scope and action columns, replacing both the triage rules table and the ManageSourceFiltersPanel
- **Modified** — Connector detail page shows rules scoped to that connector (replacing ConnectorFiltersDialog)
- **Modified** — All connectors use unified evaluator instead of `SourceFilterEvaluator`
- **Preserved** — Thread affinity routing (unchanged, still runs before rule evaluation)
- **Preserved** — Gmail label filter panel (orthogonal, not part of this change)

### Rule model (summary)

| Field | Description |
|-------|-------------|
| `scope` | `global` (post-ingest, pre-LLM) or `connector:<type>:<identity>` (pre-ingest, at connector) |
| `rule_type` | `sender_domain`, `sender_address`, `header_condition`, `mime_type`, `chat_id`, `channel_id`, `substring` |
| `condition` | JSONB — schema determined by `rule_type` |
| `action` | `block`, `skip`, `metadata_only`, `low_priority_queue`, `route_to:<butler>`, `pass_through` |
| `priority` | Integer, lower = evaluated first. First match wins. |

### Pipeline positions

```
Connector receives message
  ↓
Connector-scoped rules evaluated (scope = connector:gmail:gmail:user:dev)
  → action=block: message DROPPED, never enters the system, no LLM sees it
  → action=skip/metadata_only/route_to: still dropped at connector (connector rules only support block)
  → no match or pass_through: continue
  ↓
Switchboard ingest + dedup
  ↓
Thread affinity check (preserved, unchanged)
  ↓
Global rules evaluated (scope = global)
  → action=skip: drop silently
  → action=metadata_only: store metadata, skip LLM
  → action=route_to:<butler>: deterministic route, skip LLM
  → action=low_priority_queue: route to low-priority queue
  → no match: pass_through to LLM classification
  ↓
LLM classification (only for unmatched messages)
```

**Key design choice:** Connector-scoped rules only support `block` as their action (they cannot route — routing requires Switchboard context). Global rules support the full action set. The UI enforces this constraint.

### Composition model

**First-match-wins** everywhere (replacing the blacklist/whitelist composition of source filters). Rules are evaluated in `priority ASC, created_at ASC, id ASC` order. First matching rule's action is applied. No match = `pass_through`.

This is simpler to reason about than the blacklist-first/whitelist-second composition model. A user who wants whitelist semantics creates a set of allow rules at low priority and a catch-all `block` rule at higher priority.

## Capabilities

### New Capabilities

- `ingestion-policy`: Unified ingestion rule data model, evaluation engine, composition rules, REST API, and pipeline integration points. Replaces `source-filter-registry`, `connector-source-filter-enforcement`, and triage rule sections of `butler-switchboard`.

### Modified Capabilities

- `connector-base-spec`: Pre-ingest filter gate changes from `SourceFilterEvaluator` to unified `IngestionPolicyEvaluator` with connector-scoped rules
- `connector-gmail`: Uses unified evaluator; valid rule_types for connector scope: `sender_domain`, `sender_address`, `substring`
- `connector-telegram-bot`: Uses unified evaluator; valid rule_type for connector scope: `chat_id`
- `butler-switchboard`: Triage rule evaluation, caching, and CRUD replaced by unified ingestion policy. Thread affinity preserved unchanged. Triage observability metrics renamed/consolidated.
- `dashboard-butler-management`: Filters page becomes unified rules table; ConnectorFiltersDialog replaced by scoped view of same rules

## Impact

### Code (remove)
- `src/butlers/connectors/source_filter.py` — entire module deleted
- `roster/switchboard/tools/triage/evaluator.py` — replaced by unified evaluator
- `roster/switchboard/tools/triage/cache.py` — replaced by unified cache
- `roster/switchboard/tools/triage/telemetry.py` — consolidated into unified metrics
- `roster/switchboard/migrations/026_create_source_filters.py` — superseded by new migration
- `roster/switchboard/migrations/017_create_triage_rules.py` — superseded by new migration
- `frontend/src/components/ingestion/ConnectorFiltersDialog.tsx` — replaced
- `frontend/src/components/ingestion/ManageSourceFiltersPanel.tsx` — replaced
- `frontend/src/hooks/use-source-filters.ts` — replaced
- `frontend/src/hooks/use-triage.ts` — replaced

### Code (modify)
- `roster/switchboard/api/router.py` — remove source filter + triage rule endpoints, add unified endpoints
- `roster/switchboard/api/models.py` — remove old models, add unified models
- `src/butlers/connectors/gmail.py` — switch to unified evaluator
- `src/butlers/connectors/telegram_bot.py` — switch to unified evaluator
- `src/butlers/connectors/telegram_user_client.py` — switch to unified evaluator
- `src/butlers/connectors/discord_user.py` — switch to unified evaluator
- `roster/switchboard/tools/ingestion/ingest.py` — use unified evaluator for global rules
- `frontend/src/components/switchboard/FiltersTab.tsx` — rewrite as unified rules table
- `frontend/src/pages/ConnectorDetailPage.tsx` — show connector-scoped rules inline

### Database
- New migration: create `ingestion_rules` table, migrate data from `triage_rules` + `source_filters`/`connector_source_filters`, drop old tables
- Existing data preserved via migration (triage rules → global scope; source filters → connector scope with action=block)

### APIs (breaking)
- All `/source-filters/*` endpoints removed
- All `/connectors/*/filters` endpoints removed
- All `/triage-rules/*` endpoints removed
- New `/ingestion-rules/*` endpoints replace all of the above

### OpenSpec changes (superseded)
- `connector-source-filters` change: specs `source-filter-registry`, `connector-source-filter-enforcement`, `dashboard-connector-filter-ui` are superseded by this change's `ingestion-policy` spec. Delta specs for `connector-base-spec`, `connector-gmail`, `connector-telegram-bot` in that change are superseded by this change's delta specs.

### Tests
- `tests/api/test_switchboard_source_filters.py` — rewrite for unified API
- `tests/api/test_switchboard_connector_filters.py` — rewrite for unified API
- `tests/connectors/test_source_filter.py` — rewrite for unified evaluator
- Triage rule tests — rewrite for unified API and evaluator
