## MODIFIED Requirements

### Requirement: Model Catalog Schema
The system SHALL maintain a `public.model_catalog` table as the canonical registry of available model configurations. Each entry defines a named model alias, its runtime adapter type, the actual model identifier, optional extra CLI arguments, a complexity tier assignment, an enabled flag, and a priority for tie-breaking.

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

#### Scenario: No candidates fails closed
- **WHEN** `resolve_model()` finds no enabled qualifying entries in any tier
- **THEN** the function returns `None`
- **AND** a live Spawner with a database pool returns a pre-invocation `ModelResolutionError: no_eligible_catalog_entries` because catalog-keyed permission, budget, breaker, and provenance gates cannot run without an entry
- **AND** only explicit pool-free direct-adapter harnesses may invoke `DEFAULT_RUNTIME_TYPE` with no model after the adapter baseline satisfies the dispatch intent
- **AND** the caller SHALL NOT pair a hard-coded model from one provider with another provider's runtime
- **AND** when the pool-free direct runtime cannot prove every required capability, the caller returns a pre-invocation `ModelResolutionError` without launching an adapter

#### Scenario: Priority tie-breaking prefers evidence, falls back to round-robin
- **WHEN** multiple enabled entries exist for the same butler+tier at the same effective priority
- **THEN** the resolver SHALL compute an evidence-based routing score for each tied candidate from recent `public.model_dispatch_attempts` history (success rate, p95 `duration_ms`, and a reference per-call USD cost -- `butlers.core.model_routing.compute_routing_score`)
- **AND** WHEN at least two tied candidates have `_EVIDENCE_MIN_SAMPLES` (5) or more qualifying (`success`/`runtime_failure`) attempts in the trailing evidence window, the resolver SHALL select the candidate with the highest score
- **AND** WHEN fewer than two tied candidates meet that evidence threshold (a new catalog, sparse history, or all-tied scores), the resolver SHALL fall back to the original per-`(butler_name, complexity_tier)` round-robin counter in `public.model_round_robin_counters`, ordering candidates by `created_at ASC, id ASC` and selecting index `counter % total`
- **AND** the counter is incremented atomically only when a winning tier exists (empty-tier fallthrough attempts never increment any counter), regardless of which selection path is used
- **AND** a candidate's score is never fabricated below the evidence threshold: `compute_routing_score` returns `score=None` and callers MUST treat that as "no opinion", not a low score

#### Scenario: Verification filter
- **WHEN** the resolver evaluates candidate rows
- **THEN** rows with `last_verified_ok = false` are excluded (`mc.last_verified_ok IS DISTINCT FROM false`); rows never verified (`NULL`) or verified-ok (`true`) qualify
- **AND** `last_verified_ok` is a single nullable boolean recording the outcome of the most recent verification probe: `NULL` = never verified, `true` = last probe passed, `false` = last probe failed. There is no multi-valued connection-state column.
- **AND** the boolean is set by the model-settings verification endpoint (see `dashboard-model-settings`, which persists `last_verified_at`, `last_verified_latency_ms`, `last_verified_ok`, and `last_verified_error`), not by the resolver; the resolver only reads it.
- **AND** the `enabled` flag is independent of verification: resolution requires effective `enabled = true` AND `last_verified_ok IS DISTINCT FROM false` AND the dispatch-outcome circuit breaker not open (see Dispatch-Outcome Circuit Breaker), so an operator may disable a verified-ok model (excluded) or keep a never-verified model enabled (qualifies).

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
- **AND** the Spawner SHALL admit only candidates recorded as fit-eligible for the original dispatch intent; a candidate excluded for a required capability is recorded as a non-invoked suppressed attempt and skipped
- **AND** it SHALL return the next highest-priority enabled model in that same tier

#### Scenario: Discretion quota skip uses the same effective tier
- **WHEN** the discretion dispatcher selects a catalog entry and its pre-invocation
  `check_token_quota()` result is `allowed=False`
- **THEN** it SHALL treat that catalog entry as a per-entry availability skip, without
  invoking its adapter
- **AND** it SHALL exclude the skipped `catalog_entry_id` and seek the next candidate only
  in the already selected effective complexity tier
- **AND** it SHALL consume one slot from the dispatcher's existing bounded same-tier
  failover-attempt budget
- **AND** it SHALL emit bounded operational provenance limited to the catalog model,
  effective tier, quota-window state, bounded attempt count, and a stable quota-skip reason;
  it SHALL NOT add prompt, system-prompt, caller identity, or Spawner session provenance

#### Scenario: Discretion same-tier quota exhaustion is terminal
- **WHEN** quota skips and/or eligible runtime failures consume every candidate in the
  discretion dispatcher's effective complexity tier, or consume its bounded attempt budget
- **THEN** the dispatcher SHALL raise `RuntimeError` tagged
  `same_tier_failover_exhausted`
- **AND** it SHALL NOT retry a candidate from a different effective complexity tier

#### Scenario: Initial tier fallthrough remains separate
- **WHEN** initial model resolution finds no candidate in the requested tier
- **THEN** the existing canonical tier fallthrough behavior MAY select a candidate from
  the next eligible tier
- **AND** any subsequent failover attempts SHALL remain restricted to the effective tier
  that produced that selected candidate

#### Scenario: Verification filter applies to failover candidates
- **WHEN** a next-candidate query evaluates model catalog rows
- **THEN** disabled rows (effective `enabled = false`), rows that failed their last verification
  (`last_verified_ok = false`), and rows whose dispatch-outcome circuit breaker is open SHALL NOT
  be returned as failover candidates
- **AND** the eligibility test is exactly the same contract used by the primary resolver: effective
  `enabled = true` AND `last_verified_ok IS DISTINCT FROM false` AND breaker not open. There is no
  separate connection-state machine (no distinct error / offline / deprecated / rate-limited /
  anomaly states); `last_verified_ok`, `enabled`, and breaker state are the canonical and only
  eligibility signals.

#### Scenario: Priority tie-breaking via round-robin
- **WHEN** multiple enabled entries exist for the same butler+tier at the same effective priority
- **THEN** the initial resolver load-balances across them using a per-`(butler_name, complexity_tier)` round-robin counter in `public.model_round_robin_counters`, ordering candidates by `created_at ASC, id ASC` and selecting index `counter % total`
- **AND** the counter is incremented atomically only when a winning tier exists (empty-tier fallthrough attempts never increment any counter)
