## MODIFIED Requirements

### Requirement: Model Catalog Schema
The system SHALL maintain a `public.model_catalog` table as the canonical registry of available model configurations. Each entry defines a named model alias, its runtime adapter type, the actual model identifier, optional extra CLI arguments, a complexity tier assignment, an enabled flag, and a priority for tie-breaking.

Entries SHALL also carry nullable allowance_account, an operator-supplied nonsecret account label; NULL SHALL retain the existing runtime_type fallback. Assigning a label SHALL NOT provision provider credentials or change quota/breaker semantics.

#### Scenario: Catalog entry structure
- **WHEN** a model catalog entry is created
- **THEN** it contains: `id` (UUID PK), `alias` (text, UNIQUE), `runtime_type` (text, NOT NULL), `model_id` (text, NOT NULL), `extra_args` (JSONB, default `[]`), `complexity_tier` (text, NOT NULL), `enabled` (boolean, default true), `priority` (int, default 0), `session_timeout_s` (int, NOT NULL, default 1800), `last_verified_at` (timestamptz, nullable), `last_verified_latency_ms` (int, nullable), `last_verified_ok` (bool, nullable), `last_verified_error` (text, nullable), `created_at` (timestamptz), `updated_at` (timestamptz)
- **AND** `session_timeout_s` was added by migration `core_073` when the per-session timeout moved off `runtime_config` onto the catalog
- **AND** the `last_verified_at` / `last_verified_latency_ms` / `last_verified_ok` columns back the verification filter used during resolution (see Model Resolution); `last_verified_ok` is a single nullable boolean (NULL = never verified, `true` = last probe passed, `false` = last probe failed), not a multi-valued connection-state column
- **AND** `last_verified_error` was added by migration `core_167` and stores the truncated exception text from the most recent failed verification (NULL when never verified or the last verification succeeded); it is display-only and does not participate in resolution eligibility

#### Scenario: Alias uniqueness
- **WHEN** a catalog entry is created with an alias that already exists
- **THEN** the insert is rejected with a unique constraint violation

#### Scenario: Valid complexity tiers
- **WHEN** a catalog entry specifies a `complexity_tier`
- **THEN** the value MUST be one of the canonical tiers defined by complexity-classification "Complexity Enum" (enforced by the `chk_model_catalog_complexity_tier` CHECK constraint)
- **AND** any other value is rejected with a constraint violation
- **AND** the `specialty` tier carries both the lightweight latency-sensitive evaluations (e.g. connector noise filtering that runs outside the butler session spawner) and the healing agent sessions
- **AND** the `local` tier is reserved for self-hosted models (e.g. Ollama via OpenCode)

#### Scenario: Valid runtime types
- **WHEN** a catalog entry specifies a `runtime_type`
- **THEN** the value MUST correspond to a registered runtime adapter (e.g. `claude`, `codex`, `gemini`, `opencode`, `api`)

#### Scenario: Extra args format
- **WHEN** `extra_args` is provided
- **THEN** it MUST be a JSON array of strings, where each string is a single CLI token (e.g. `["--config", "model_reasoning_effort=high"]`)

#### Scenario: Explicit account label and legacy default
- **WHEN** the existing catalog API creates a route with a valid allowance_account label or omits it
- **THEN** the label SHALL be persisted and returned, or the column SHALL be NULL when omitted or null
- **AND** NULL SHALL continue to use runtime_type as the allowance account key without guessing or backfilling credentials

#### Scenario: Same-runtime routes retain distinct account assignments
- **WHEN** two routes on one runtime_type are assigned different labels through the catalog API and one account is exhausted until a future reset
- **THEN** the exhausted account's routes SHALL be excluded and an otherwise eligible route on the other account SHALL still resolve
- **AND** identical labels SHALL intentionally retain the current account-key grouping across routes
