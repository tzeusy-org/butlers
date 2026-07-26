## MODIFIED Requirements

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
- **AND** it SHALL return the next highest-priority enabled model in that same tier

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
