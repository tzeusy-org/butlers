## MODIFIED Requirements

### Requirement: Model Resolution

The system SHALL provide model resolution functions that select catalog entries at spawn time by querying the catalog with butler-specific overrides applied. The primary `resolve_model(pool, butler_name, complexity_tier)` function selects the appropriate model configuration for initial spawn, `resolve_model_with_effective_tier()` additionally returns the effective tier that produced the candidate, and `next_same_tier_candidate()` supports same-tier failover. Higher `priority` is more preferred (the resolver selects the MAX effective priority in the winning tier). For a goal invocation the resolution key SHALL be the invocation role (`lead`, `child` or `monitor`) in place of a butler name, and the requested tier SHALL be the first tier of that role's escalation ladder or, for a child, the tier its brief requests; butler-name keys remain only for model use outside goals. When a lead calls `goal.escalate`, the goal runner SHALL resolve the replacement lead on the next higher tier of the role's ladder and SHALL refuse the escalation when no higher tier qualifies, because the ladder is finite.

Source: RFC 0038 D9, D16, D18
Scope: v1-mandatory

#### Scenario: Goal invocation resolves by role
- **WHEN** the goal runner prepares a child invocation whose brief requests tier `cheap`
- **THEN** the resolver is called with `child` as the resolution key and `cheap` as the requested tier
- **AND** no butler name is used as the resolution key for the invocation

#### Scenario: Escalation climbs a finite ladder
- **WHEN** a lead calls `goal.escalate`
- **THEN** the goal runner resolves the replacement lead only on a tier above the escalating lead's effective tier in the role's ladder
- **AND** when no higher tier qualifies the escalation is refused and the current lead keeps the goal
- **AND** resolution never falls through to a tier at or below the escalating lead's effective tier


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

## ADDED Requirements

### Requirement: Owner-fast is a bounded lead profile
The catalog and runner SHALL implement S5's versioned owner-fast profile, grant-intersected eager IDs, reserved capacity and correlated timing fields. This SHALL add no role, runtime, fallback or eligibility exception and SHALL preserve the complete existing Model Resolution requirement and scenarios.

ID: REQ-model-catalog-005
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S5; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: An eager profile names an ungranted capability
- **WHEN** an owner turn uses owner-fast
- **THEN** only the intersection is presented and no additional capability becomes listed or callable

#### Scenario: Owner and background work contend
- **WHEN** background work fills the eligible shared capacity and an owner ask arrives
- **THEN** one owner-origin slot is reserved inside total capacity five; waiting/recovery cannot leak or exceed slots

#### Scenario: A latency segment is absent
- **WHEN** a runtime/transport has no first-visible or first-word evidence
- **THEN** that segment is unknown, with available baseline timings retained and no streaming or M7 threshold pass claimed
