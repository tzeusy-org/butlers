# Model Catalog — Self-Healing Tier

## MODIFIED Requirements

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
