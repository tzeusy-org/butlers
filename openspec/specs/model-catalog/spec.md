# Model Catalog

## Purpose

The model catalog is the canonical registry of available model configurations for dynamic model routing. It defines named model aliases with runtime adapter types, model identifiers, extra CLI arguments, complexity tier assignments, and priority ordering. Per-butler overrides allow customization layered on top of global defaults. The `resolve_model()` function selects the appropriate model at spawn time.

## ADDED Requirements

### Requirement: Model Catalog Schema
The system SHALL maintain a `shared.model_catalog` table as the canonical registry of available model configurations. Each entry defines a named model alias, its runtime adapter type, the actual model identifier, optional extra CLI arguments, a complexity tier assignment, an enabled flag, and a priority for tie-breaking.

#### Scenario: Catalog entry structure
- **WHEN** a model catalog entry is created
- **THEN** it contains: `id` (UUID PK), `alias` (text, UNIQUE), `runtime_type` (text, NOT NULL), `model_id` (text, NOT NULL), `extra_args` (JSONB, default `[]`), `complexity_tier` (text, NOT NULL), `enabled` (boolean, default true), `priority` (int, default 0), `created_at` (timestamptz), `updated_at` (timestamptz)

#### Scenario: Alias uniqueness
- **WHEN** a catalog entry is created with an alias that already exists
- **THEN** the insert is rejected with a unique constraint violation

#### Scenario: Valid complexity tiers
- **WHEN** a catalog entry specifies a `complexity_tier`
- **THEN** the value MUST be one of: `trivial`, `medium`, `high`, `extra_high`, `discretion`, `self_healing`
- **AND** any other value is rejected with a validation error
- **AND** the `discretion` tier is reserved for lightweight, latency-sensitive evaluations (e.g. noise filtering) that run outside the butler session spawner
- **AND** the `self_healing` tier is reserved for healing agent sessions that investigate and propose fixes for butler errors

#### Scenario: Valid runtime types
- **WHEN** a catalog entry specifies a `runtime_type`
- **THEN** the value MUST correspond to a registered runtime adapter (e.g. `claude`, `codex`, `gemini`, `opencode`)

#### Scenario: Extra args format
- **WHEN** `extra_args` is provided
- **THEN** it MUST be a JSON array of strings, where each string is a single CLI token (e.g. `["--config", "model_reasoning_effort=high"]`)

### Requirement: Model Alias Concept
A model alias is a named configuration combining a base model with optional extra runtime arguments. Aliases are rows in the model catalog — there is no separate alias table. The alias serves as the human-readable identifier while `model_id` is the actual model string passed to the runtime adapter.

#### Scenario: Alias with extra args
- **WHEN** a catalog entry has alias `gpt-5.4-high`, model_id `gpt-5.4`, and extra_args `["--config", "model_reasoning_effort=high"]`
- **THEN** the spawner passes `model_id` as the model parameter and merges `extra_args` into the runtime invocation args

#### Scenario: Alias without extra args
- **WHEN** a catalog entry has alias `claude-opus` and empty extra_args `[]`
- **THEN** the spawner passes `model_id` as the model parameter with no additional args from the catalog

### Requirement: Butler Model Overrides Schema
The system SHALL maintain a `shared.butler_model_overrides` table for per-butler customization layered on top of the global catalog. Overrides are sparse — most butlers use global defaults.

#### Scenario: Override entry structure
- **WHEN** a butler model override is created
- **THEN** it contains: `id` (UUID PK), `butler_name` (text, NOT NULL), `catalog_entry_id` (UUID, FK to model_catalog.id, nullable), `enabled` (boolean, nullable), `priority` (int, nullable), `complexity_tier` (text, nullable)
- **AND** a UNIQUE constraint on `(butler_name, catalog_entry_id)` prevents duplicate overrides

#### Scenario: Override disables a global entry
- **WHEN** an override for butler `finance` sets `enabled = false` for catalog entry `gpt-5.4-high`
- **THEN** the `gpt-5.4-high` model is excluded from resolution for the `finance` butler
- **AND** other butlers still see `gpt-5.4-high` as enabled

#### Scenario: Override remaps complexity tier
- **WHEN** an override for butler `switchboard` sets `complexity_tier = 'trivial'` for a catalog entry normally at `medium`
- **THEN** the `switchboard` butler treats that model as available at the `trivial` tier

#### Scenario: Override changes priority
- **WHEN** an override for butler `general` sets `priority = -1` for a catalog entry with global priority `0`
- **THEN** resolution for `general` uses priority `-1` (making it more preferred) while other butlers use `0`

#### Scenario: NULL override fields inherit global values
- **WHEN** an override has `enabled = NULL` or `priority = NULL` or `complexity_tier = NULL`
- **THEN** the corresponding global catalog value is used (COALESCE semantics)

### Requirement: Model Resolution
The system SHALL provide a `resolve_model(butler_name, complexity_tier)` function that selects the appropriate model configuration at spawn time by querying the catalog with butler-specific overrides applied.

#### Scenario: Resolution with global defaults only
- **WHEN** `resolve_model("finance", "medium")` is called and no overrides exist for `finance`
- **THEN** the function returns the enabled global catalog entry for tier `medium` with the lowest `priority` value
- **AND** the return value is a tuple of `(runtime_type, model_id, extra_args, catalog_entry_id)`

#### Scenario: Resolution with butler overrides
- **WHEN** `resolve_model("switchboard", "trivial")` is called and an override remaps a `medium` entry to `trivial` for `switchboard`
- **THEN** the remapped entry is included in the candidate set for `trivial`

#### Scenario: Resolution with disabled override
- **WHEN** `resolve_model("health", "high")` is called and an override disables the preferred `high` entry for `health`
- **THEN** the disabled entry is excluded and the next-priority `high` entry is selected

#### Scenario: No candidates fallback
- **WHEN** `resolve_model(butler_name, complexity_tier)` finds no enabled entries for the requested tier
- **THEN** the function returns `None`
- **AND** the caller (spawner) falls back to `butler.toml` `[butler.runtime].model`

#### Scenario: Priority tie-breaking
- **WHEN** multiple enabled entries exist for the same butler+tier with the same effective priority
- **THEN** the entry with the earliest `created_at` is selected (stable ordering)

#### Scenario: Return type includes catalog_entry_id
- **WHEN** `resolve_model()` returns a match
- **THEN** the return type is `tuple[str, str, list[str], UUID]` — `(runtime_type, model_id, extra_args, catalog_entry_id)`
- **AND** `catalog_entry_id` is the UUID primary key of the matched `shared.model_catalog` row

### Requirement: Seed Data Migration
The system SHALL seed the model catalog with sensible defaults on first migration, covering known runtime adapters and common alias patterns.

#### Scenario: Default seed entries
- **WHEN** the migration runs on a fresh database
- **THEN** the following entries are created:

| Alias | Runtime | Model ID | Extra Args | Tier | Priority |
|-------|---------|----------|------------|------|----------|
| `claude-haiku` | `claude` | `claude-haiku-4-5-20251001` | `[]` | `trivial` | 0 |
| `claude-sonnet` | `claude` | `claude-sonnet-4-6` | `[]` | `medium` | 0 |
| `claude-opus` | `claude` | `claude-opus-4-6` | `[]` | `high` | 0 |
| `gpt-5.1` | `codex` | `gpt-5.1` | `[]` | `medium` | 10 |
| `gpt-5.3-spark` | `codex` | `gpt-5.3-codex-spark` | `[]` | `trivial` | 0 |
| `gpt-5.4` | `codex` | `gpt-5.4` | `[]` | `high` | 10 |
| `gpt-5.4-high` | `codex` | `gpt-5.4` | `["--config", "model_reasoning_effort=high"]` | `extra_high` | 0 |
| `gemini-2.5-flash` | `gemini` | `gemini-2.5-flash` | `[]` | `trivial` | 10 |
| `gemini-2.5-pro` | `gemini` | `gemini-2.5-pro` | `[]` | `high` | 10 |
| `minimax-m2.5` | `opencode` | `minimax/MiniMax-M2.5` | `[]` | `medium` | 20 |
| `glm-5` | `opencode` | `zhipu/GLM-5` | `[]` | `medium` | 20 |
| `kimi-k2.5` | `opencode` | `moonshot/Kimi-K2.5` | `[]` | `high` | 20 |

| `discretion-qwen3.5-9b` | `opencode` | `ollama/qwen3.5:9b` | `[]` | `discretion` | 10 |
| `healing-sonnet` | `claude` | `claude-sonnet-4-6` | `[]` | `self_healing` | 10 |

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
