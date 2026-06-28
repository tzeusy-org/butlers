# Model Catalog

## Purpose

The model catalog is the canonical registry of available model configurations for dynamic model routing. It defines named model aliases with runtime adapter types, model identifiers, extra CLI arguments, complexity tier assignments, and priority ordering. Per-butler overrides allow customization layered on top of global defaults. The `resolve_model()` function selects the appropriate model at spawn time.

## Requirements

### Requirement: Model Catalog Schema
The system SHALL maintain a `public.model_catalog` table as the canonical registry of available model configurations. Each entry defines a named model alias, its runtime adapter type, the actual model identifier, optional extra CLI arguments, a complexity tier assignment, an enabled flag, and a priority for tie-breaking.

#### Scenario: Catalog entry structure
- **WHEN** a model catalog entry is created
- **THEN** it contains: `id` (UUID PK), `alias` (text, UNIQUE), `runtime_type` (text, NOT NULL), `model_id` (text, NOT NULL), `extra_args` (JSONB, default `[]`), `complexity_tier` (text, NOT NULL), `enabled` (boolean, default true), `priority` (int, default 0), `session_timeout_s` (int, NOT NULL, default 1800), `last_verified_at` (timestamptz, nullable), `last_verified_latency_ms` (int, nullable), `last_verified_ok` (bool, nullable), `created_at` (timestamptz), `updated_at` (timestamptz)
- **AND** `session_timeout_s` was added by migration `core_073` when the per-session timeout moved off `runtime_config` onto the catalog
- **AND** the `last_verified_*` columns were added by migration `core_093` and back the verification filter used during resolution (see Model Resolution); `last_verified_ok` is a single nullable boolean (NULL = never verified, `true` = last probe passed, `false` = last probe failed), not a multi-valued connection-state column

#### Scenario: Alias uniqueness
- **WHEN** a catalog entry is created with an alias that already exists
- **THEN** the insert is rejected with a unique constraint violation

#### Scenario: Valid complexity tiers
- **WHEN** a catalog entry specifies a `complexity_tier`
- **THEN** the value MUST be one of the canonical six: `reasoning`, `workhorse`, `cheap`, `specialty`, `local`, `legacy` (enforced by the `chk_model_catalog_complexity_tier` CHECK constraint)
- **AND** any other value is rejected with a constraint violation
- **AND** the legacy six-value vocabulary (`trivial`, `medium`, `high`, `extra_high`, `discretion`, `self_healing`) was renamed to the canonical six in migration `core_093` (`trivial` to `cheap`, `medium` to `workhorse`, `high` and `extra_high` to `reasoning`, `discretion` and `self_healing` to `specialty`)
- **AND** the `specialty` tier carries both the lightweight latency-sensitive evaluations (formerly `discretion`, e.g. connector noise filtering that runs outside the butler session spawner) and the healing agent sessions (formerly `self_healing`)
- **AND** the `local` tier is reserved for self-hosted models (e.g. Ollama via OpenCode)

#### Scenario: Valid runtime types
- **WHEN** a catalog entry specifies a `runtime_type`
- **THEN** the value MUST correspond to a registered runtime adapter (e.g. `claude`, `codex`, `gemini`, `opencode`)

#### Scenario: Extra args format
- **WHEN** `extra_args` is provided
- **THEN** it MUST be a JSON array of strings, where each string is a single CLI token (e.g. `["--config", "model_reasoning_effort=high"]`)

### Requirement: Model Alias Concept
A model alias is a named configuration combining a base model with optional extra runtime arguments. Aliases SHALL be rows in the model catalog - there is no separate alias table. The alias serves as the human-readable identifier while `model_id` is the actual model string passed to the runtime adapter.

#### Scenario: Alias with extra args
- **WHEN** a catalog entry has alias `gpt-5.4-high`, model_id `gpt-5.4`, and extra_args `["--config", "model_reasoning_effort=high"]`
- **THEN** the spawner passes `model_id` as the model parameter and merges `extra_args` into the runtime invocation args

#### Scenario: Alias without extra args
- **WHEN** a catalog entry has alias `claude-opus` and empty extra_args `[]`
- **THEN** the spawner passes `model_id` as the model parameter with no additional args from the catalog

### Requirement: Butler Model Overrides Schema
The system SHALL maintain a `public.butler_model_overrides` table for per-butler customization layered on top of the global catalog. Overrides are sparse - most butlers use global defaults.

#### Scenario: Override entry structure
- **WHEN** a butler model override is created
- **THEN** it contains: `id` (UUID PK), `butler_name` (text, NOT NULL), `catalog_entry_id` (UUID, FK to model_catalog.id ON DELETE CASCADE, NOT NULL), `enabled` (boolean, NOT NULL, default true), `priority` (int, nullable), `complexity_tier` (text, nullable), `source` (text, nullable)
- **AND** a UNIQUE constraint on `(butler_name, catalog_entry_id)` prevents duplicate overrides
- **AND** the `source` column tags the override's origin (e.g. `e2e-benchmark` for benchmark pinning) for identification and cleanup
- **AND** note: `enabled` is NOT NULL (default true), so an override row always carries an explicit enabled value; only `priority` and `complexity_tier` are nullable and inherit the global value via COALESCE when NULL

#### Scenario: Override disables a global entry
- **WHEN** an override for butler `finance` sets `enabled = false` for catalog entry `gpt-5.4-high`
- **THEN** the `gpt-5.4-high` model is excluded from resolution for the `finance` butler
- **AND** other butlers still see `gpt-5.4-high` as enabled

#### Scenario: Override remaps complexity tier
- **WHEN** an override for butler `switchboard` sets `complexity_tier = 'cheap'` for a catalog entry normally at `workhorse`
- **THEN** the `switchboard` butler treats that model as available at the `cheap` tier

#### Scenario: Override changes priority
- **WHEN** an override for butler `general` sets `priority = 50` for a catalog entry with global priority `0`
- **THEN** resolution for `general` uses priority `50` (making it more preferred, since higher priority wins) while other butlers use `0`

#### Scenario: NULL override fields inherit global values
- **WHEN** an override has `priority = NULL` or `complexity_tier = NULL`
- **THEN** the corresponding global catalog value is used (`COALESCE(bmo.field, mc.field)` semantics)
- **AND** because `enabled` is NOT NULL, `COALESCE(bmo.enabled, mc.enabled)` always resolves to the override's own `enabled` value (the override cannot inherit the global enabled flag)

### Requirement: Model Resolution
The system SHALL provide model resolution functions that select catalog entries at spawn time by querying the catalog with butler-specific overrides applied. The primary `resolve_model(pool, butler_name, complexity_tier)` function selects the appropriate model configuration for initial spawn, `resolve_model_with_effective_tier()` additionally returns the effective tier that produced the candidate, and `next_same_tier_candidate()` supports same-tier failover. Higher `priority` is more preferred (the resolver selects the MAX effective priority in the winning tier).

#### Scenario: Resolution with global defaults only
- **WHEN** `resolve_model(pool, "finance", "workhorse")` is called and no overrides exist for `finance`
- **THEN** the function returns the enabled global catalog entry for tier `workhorse` with the HIGHEST `priority` value
- **AND** the return value is a tuple of `(runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s)`

#### Scenario: Resolution with butler overrides
- **WHEN** `resolve_model(pool, "switchboard", "cheap")` is called and an override remaps a `workhorse` entry to `cheap` for `switchboard`
- **THEN** the remapped entry is included in the candidate set for `cheap`

#### Scenario: Resolution with disabled override
- **WHEN** `resolve_model(pool, "health", "reasoning")` is called and an override disables the preferred `reasoning` entry for `health`
- **THEN** the disabled entry is excluded and the next-highest-priority `reasoning` entry is selected

#### Scenario: Tier fallthrough when requested tier empty
- **WHEN** `resolve_model(pool, butler_name, complexity_tier)` finds no qualifying entry in the requested tier
- **THEN** the resolver falls through to the next tier in canonical order (`reasoning` > `workhorse` > `cheap` > `specialty` > `local` > `legacy`) and selects the first qualifying candidate found
- **AND** any subsequent same-tier failover is restricted to the effective tier that produced that selected candidate

#### Scenario: No candidates fallback
- **WHEN** `resolve_model()` finds no enabled qualifying entries in any tier
- **THEN** the function returns `None`
- **AND** the caller (spawner) falls back to the module-private `_FALLBACK_MODEL_ID` constant in `butlers.core.spawner` (see `core-spawner` - Catalog empty fallback)

#### Scenario: Priority tie-breaking via round-robin
- **WHEN** multiple enabled entries exist for the same butler+tier at the same effective priority
- **THEN** the initial resolver load-balances across them using a per-`(butler_name, complexity_tier)` round-robin counter in `public.model_round_robin_counters`, ordering candidates by `created_at ASC, id ASC` and selecting index `counter % total`
- **AND** the counter is incremented atomically only when a winning tier exists (empty-tier fallthrough attempts never increment any counter)

#### Scenario: Verification filter
- **WHEN** the resolver evaluates candidate rows
- **THEN** rows with `last_verified_ok = false` are excluded (`mc.last_verified_ok IS DISTINCT FROM false`); rows never verified (`NULL`) or verified-ok (`true`) qualify
- **AND** `last_verified_ok` is a single nullable boolean recording the outcome of the most recent verification probe: `NULL` = never verified, `true` = last probe passed, `false` = last probe failed. There is no multi-valued connection-state column.
- **AND** the boolean is set by the model-settings verification endpoint (see `dashboard-model-settings`, which persists `last_verified_at`, `last_verified_latency_ms`, and `last_verified_ok`), not by the resolver; the resolver only reads it.
- **AND** the `enabled` flag is independent of verification: resolution requires BOTH effective `enabled = true` AND `last_verified_ok IS DISTINCT FROM false`, so an operator may disable a verified-ok model (excluded) or keep a never-verified model enabled (qualifies).

#### Scenario: Return type includes catalog_entry_id and session_timeout_s
- **WHEN** `resolve_model()` returns a match
- **THEN** the return type is `tuple[str, str, list[str], UUID, int]` (`(runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s)`)
- **AND** `catalog_entry_id` is the UUID primary key of the matched `public.model_catalog` row
- **AND** `session_timeout_s` is the per-session runtime timeout from that catalog row

#### Scenario: Next eligible same-tier candidate
- **WHEN** the spawner requests the next eligible model after an attempted
  `catalog_entry_id` fails or is skipped
- **THEN** the resolver SHALL search only the exact effective complexity tier that
  produced the original candidate
- **AND** it SHALL apply global catalog values plus butler override COALESCE semantics
- **AND** it SHALL exclude all previously attempted or skipped `catalog_entry_id` values
- **AND** it SHALL return the next highest-priority enabled model in that same tier

#### Scenario: Initial tier fallthrough remains separate
- **WHEN** initial model resolution finds no candidate in the requested tier
- **THEN** the existing canonical tier fallthrough behavior MAY select a candidate from
  the next eligible tier
- **AND** any subsequent failover attempts SHALL remain restricted to the effective tier
  that produced that selected candidate

#### Scenario: Verification filter applies to failover candidates
- **WHEN** a next-candidate query evaluates model catalog rows
- **THEN** disabled rows (effective `enabled = false`) and rows that failed their last verification
  (`last_verified_ok = false`) SHALL NOT be returned as failover candidates
- **AND** the eligibility test is exactly the same boolean-plus-`enabled` contract used by the
  primary resolver: effective `enabled = true` AND `last_verified_ok IS DISTINCT FROM false`. There
  is no separate connection-state machine (no distinct error / offline / deprecated / rate-limited /
  anomaly states); `last_verified_ok` plus `enabled` is the canonical and only eligibility signal.

#### Scenario: Deterministic fallback ordering
- **WHEN** multiple non-attempted candidates remain in the effective tier
- **THEN** fallback ordering SHALL be deterministic by effective priority descending,
  then `created_at ASC`, then `id ASC`
- **AND** the resolver SHALL NOT return an already-attempted candidate

### Requirement: Seed Data Migration
The system SHALL seed the model catalog with sensible defaults on first migration (`core_004`), reading the entries from the `model_catalog_defaults.toml` bootstrap file at the repo root. The TOML file is used ONLY for initial database setup; edits to it do NOT affect an existing database (use the Settings UI or API to change the catalog after first setup).

#### Scenario: Default seed entries
- **WHEN** the `core_004` migration runs on a fresh database
- **THEN** the entries from `model_catalog_defaults.toml` are inserted. As of the current bootstrap file:

| Alias | Runtime | Model ID | Extra Args | Tier | Priority | Enabled |
|-------|---------|----------|------------|------|----------|---------|
| `claude-haiku` | `claude` | `claude-haiku-4-5-20251001` | `[]` | `cheap` | 10 | false |
| `claude-sonnet` | `claude` | `claude-sonnet-4-6` | `[]` | `workhorse` | 10 | false |
| `claude-opus` | `claude` | `claude-opus-4-6` | `[]` | `reasoning` | 10 | false |
| `gpt-5.4-mini` | `codex` | `gpt-5.4-mini` | `[]` | `cheap` | 25 | true |
| `gpt-5.3-spark` | `codex` | `gpt-5.3-codex-spark` | `[]` | `workhorse` | 20 | true |
| `gpt-5.4` | `codex` | `gpt-5.4` | `[]` | `workhorse` | 0 | true |
| `gpt-5.4-high` | `codex` | `gpt-5.4` | `["--config", "model_reasoning_effort=high"]` | `reasoning` | 0 | true |
| `gemini-2.5-flash` | `gemini` | `gemini-2.5-flash` | `[]` | `cheap` | 10 | false |
| `gemini-2.5-pro` | `gemini` | `gemini-2.5-pro` | `[]` | `workhorse` | 10 | false |
| `minimax-m2.5` | `opencode` | `opencode-go/minimax-m2.5` | `[]` | `workhorse` | 20 | true |
| `glm-5` | `opencode` | `opencode-go/glm-5` | `[]` | `workhorse` | 20 | true |
| `minimax-m2.7` | `opencode` | `opencode-go/minimax-m2.7` | `[]` | `workhorse` | 20 | true |
| `minimax-m2.7-highspeed` | `opencode` | `opencode-go/minimax-m2.7-highspeed` | `[]` | `workhorse` | 15 | true |
| `kimi-k2.5` | `opencode` | `opencode-go/kimi-k2.5` | `[]` | `reasoning` | 20 | true |
| `qwen3-coder-480b` | `opencode` | `opencode-go/qwen3-coder-480b` | `[]` | `reasoning` | 20 | true |
| `ollama-default` | `opencode` | `ollama/qwen2.5-coder:7b` | `[]` | `local` | 5 | false |
| `ollama-light` | `opencode` | `ollama/llama3.3:latest` | `[]` | `local` | 5 | false |
| `ollama-heavy` | `opencode` | `ollama/deepseek-r1:32b` | `[]` | `local` | 5 | false |
| `discretion-qwen3.5-9b` | `opencode` | `ollama/qwen3.5:9b` | `[]` | `specialty` | 10 | true |
| `healing-sonnet` | `claude` | `claude-sonnet-4-6` | `[]` | `specialty` | 10 | false |

- **AND** entries whose `complexity_tier` is not one of the canonical tiers are skipped at load time

#### Scenario: Seed is idempotent
- **WHEN** the migration runs on a database where seed entries already exist
- **THEN** existing entries are not duplicated (ON CONFLICT DO NOTHING on alias)

### Requirement: Adapter Token Reporting Contract
All runtime adapters SHALL return `input_tokens` and `output_tokens` in their usage dict from `invoke()`.

#### Scenario: Adapter reports token usage
- **WHEN** a runtime adapter completes an invocation
- **THEN** it returns a usage dict containing at minimum `{"input_tokens": int, "output_tokens": int}`

#### Scenario: Adapter cannot determine token counts
- **WHEN** a runtime adapter genuinely cannot determine token counts (e.g., CLI process does not expose them)
- **THEN** it returns `{}` or `None` for usage
- **AND** the ledger does not record a row for that invocation

#### Scenario: Known adapters to audit
- **WHEN** the adapter token reporting contract is enforced
- **THEN** the following adapters are verified: `claude`, `codex`, `gemini`, `opencode` (including ollama via opencode)
