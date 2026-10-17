# Consolidation throughput decisions: 2026-10-15

Status: reviewed for publication under bu-de1hrl. The four present declines were accepted after review of the source-bound dev measurement. This decision preserves shipped behavior and introduces no new threshold, runtime implementation or cleanup authority.

## Evidence identity and limits

One authorized read-only dev capture used repeatable-read READ ONLY at 2026-10-15T21:11:40.182552+00:00. The session cohort is started_at in [2026-10-08T21:11:40.182552+00:00,2026-10-15T21:11:40.182552+00:00), trigger_source exactly schedule:consolidation. The accepted aggregate receipt SHA256 is 50fa907b175c8ee077f1a180213684465298f344e4306395437b1f48dda06c23; the approved query/collector-plan seal is 965abc9c29a921f9c59886e7d417f50f0e1a388e5a40b1f00881dafeddcf8c74. No content, raw errors, logs, credentials, tenant labels, pod identifiers or hostnames are included here.

The dev image reports source 36f0e0eb080c. All 19 inspected runtime/config files (six implementation files and all 13 roster configurations) matched the corresponding actual Git 7759a9da4f55a8aa9ed17674fe054033594191fe file bytes before and after capture. This proves the inspected source subset, not whole-image equality with 7759. Schema preflight returned 281 approved column rows over 41 readable non-RLS relations. All 63 query/scope results were OK. Owner schema heads include core_265, and ordinary memory-owning schemas report mem_014; the private chronicler_mem relation's column/access contract was observed, but its own migration-version table was outside the approved migration-head query scope. Full migration ancestry is not established by column compatibility alone.

Eligibility reproduces the inspected current claim WHERE predicate, including retry_failed=false for the private chronicler_mem pool and true for the other nine pools. Counts exclude active leases and include due failed work below five attempts where allowed. No expires_at exclusion is invented. These are exact snapshot predicate counts, not a prediction of a concurrent FOR UPDATE SKIP LOCKED claim.

## Current backlog

| Memory schema | Total episodes | Predicate eligible | Oldest eligible age |
| --- | ---: | ---: | --- |
| chronicler_mem | 14 | 0 | none |
| education | 9 | 0 | none |
| finance | 41 | 0 | none |
| general | 91 | 0 | none |
| health | 118 | 0 | none |
| home | 0 | 0 | none |
| lifestyle | 60 | 0 | none |
| relationship | 19 | 0 | none |
| switchboard | 126 | 0 | none |
| travel | 51 | 0 | none |

All 529 observed episodes were consolidated. Pending, failed, dead-letter, unconsolidated-boolean, active-lease and expired-lease counts were zero in every relation. Each nonempty relation had one observed tenant/source group, exposed only as numeric ordinals in the private receipt. Oldest eligible age is NULL for an empty eligible population, not zero. Fifteen consolidated episodes had expired expires_at; this is an observation and authorizes no cleanup.

## Exact observed session/attempt token evidence

All 323 cohort sessions were complete and successful. Each had one observed exact measured attempt with a unique measured ledger row; observed missing/duplicate/unmeasurable ledger, fallback/resume, failure, quota-skip, suppressed and exhausted counts were zero. This describes persisted observations, not proof that best-effort producers captured every paid event.

| Owner schema | Sessions | Uncached input | Output | Cache read | Cache write | Largest observed session bucket sum |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| chronicler | 14 | 488836 | 3679 | 166400 | 0 | 47447 |
| concierge | 0 | none | none | none | none | none |
| education | 9 | 219574 | 2529 | 106496 | 0 | 36809 |
| finance | 39 | 1182116 | 4249 | 464256 | 0 | 42793 |
| general | 51 | 1325530 | 12082 | 644096 | 0 | 76721 |
| health | 22 | 501789 | 4909 | 263168 | 0 | 42944 |
| home | 0 | none | none | none | none | none |
| lifestyle | 58 | 1404365 | 4478 | 698880 | 0 | 36968 |
| messenger | 0 | none | none | none | none | none |
| qa | 0 | none | none | none | none | none |
| relationship | 19 | 507024 | 1690 | 224768 | 0 | 39140 |
| switchboard | 71 | 1667540 | 11329 | 841728 | 0 | 37527 |
| travel | 40 | 1150807 | 12405 | 476672 | 0 | 43048 |

The exact observed attempt-ledger vector totals uncached input 8,447,581; output 57,350; cache read 3,886,464; measured cache write 0. These buckets have different billing semantics. Their unweighted count sum 12,391,395 and largest per-session sum 76,721 are descriptive token counts, never dollar estimates or proposed limits. No percentile is formed by averaging schema percentiles. Maximum recorded session duration was 127,638 ms; it is not a pipeline duration or proposed timeout.

All 323 session rows have a NULL cache-write bucket. Those NULLs remain unknown; the independent exact measured attempt ledger records 0 cache-write tokens. The two sources are not added or substituted silently. The four empty owner cohorts retain NULL token SUM/distribution fields and contain no observed sessions; none is treated as a measured zero-cost run.

public.consolidation_runs reports 323 best-effort successful-group rows, 514 processed episodes and zero reported group errors in the window. These rows have no exact session/invocation join. The numerical equality to 323 cohort sessions does not establish row correspondence. Whole-pipeline invocation cost is UNMEASURABLE_no_persisted_exact_join. Dollar cost is UNMEASURABLE_no_authoritative_billing_evidence. No nearest-time join, current-pricing guess or NULL-to-zero inference is used.

## Four recorded decisions

### 1. batching: decline additional batching changes

Decision: deliberately not needed as an additional control for the present evidenced state. Batch processing is already shipped. The observed current predicate-eligible backlog is zero across all ten source-mapped memory schemas. Existing ordinary default 100 and scheduled backfill 500 are source constants, not newly selected thresholds. The seven-day cohort has 323 successful sessions. No measured pressure justifies another batching mechanism or retuning these settings.

Action: Retain shipped deterministic bounded claims and one session per nonempty tenant/source group; no batch-size or schedule change. No new threshold applies because no control is adopted.

Limits: One current snapshot is not a sustained-backlog history. Best-effort group audits cannot identify exact pipeline invocations, group-size maxima or per-pipeline cost. This decision does not claim permanent unnecessariness. Reconsideration requires fresh bounded observations showing persistent predicate-eligible work or specific batch-size/throughput pressure, plus exact invocation and token attribution before numerical tuning.

### 2. per-run cap: decline a new consolidation-specific cap

Decision: deliberately not needed as an additional control for the present evidenced state. Each nonempty group produces one session, so each known scheduled invocation has at most as many group sessions as its claimed episodes (ordinary default 100; existing backfill 500). The observed 323 sessions all completed successfully with 323 exact measured attempts and no observed quota skips, runtime failures, failover or exhausted outcomes. The observed maximum per-session unweighted token-bucket sum is 76,721; this is evidence, not a proposed cap. No evidence demonstrates a need for an additional control, and an arbitrary cap could interrupt valid group processing.

Action: Retain existing bounded episode claims and catalog/Spawner quota, failover and timeout authority; add no session/token/dollar cap. No new threshold applies because no control is adopted.

Limits: There is no persisted exact pipeline-invocation-to-session join and no authoritative monetary billing evidence. A session maximum is not an invocation cost bound. Best-effort persistence cannot prove all paid activity was recorded. This decision does not claim permanent unnecessariness. Reconsideration requires an evidenced runaway or budget failure and exact invocation-to-attempt/billing provenance sufficient to define the unit, threshold, refusal/partial-processing action and retry semantics.

### 3. pending-backlog deadman: decline new deadman automation

Decision: deliberately not needed as an additional control for the present evidenced state. The current observed ten-schema snapshot contains zero pending, failed, dead-letter or eligible episodes and zero active/expired leases; oldest eligible age is NULL because that population is empty. The observed seven-day session cohort has no open/failed sessions. There is no measured stuck population or age distribution from which to derive a useful new automatic threshold/action. Existing retry/lease handling remains authoritative.

Action: Add no alert-triggered runtime work, retry, lease clearing, drain, deletion, quarantine or automatic lifecycle change. No new threshold applies because no control is adopted.

Limits: The receipt does not include historical pending-age samples or prove future stalls impossible. A quiet snapshot alone does not establish a universal service-level threshold. This decision does not claim permanent unnecessariness. Reconsideration requires a separately authorized bounded sequence of eligible-backlog/lease-age observations demonstrating a real stall and an explicitly safe, idempotent action; any cleanup remains under its separate retention authority.

### 4. cheap-tier transport: decline a consolidation-specific forced transport/tier

Decision: deliberately not needed as an additional control for the present evidenced state. REQ-module-memory-005 expressly preserves catalog/Spawner authority. The observed 323 sessions succeeded, and the measured four-bucket attempt usage is available; no authoritative dollar comparison or artifact-quality comparison demonstrates a benefit that justifies a consolidation-only routing exception. Retaining the existing catalog contract is the useful present decision.

Action: Scheduled consolidation continues through the catalog-backed live Spawner without model, runtime or session-timeout overrides. No new threshold applies because no control is adopted.

Limits: The capture does not measure selected tier/model, vendor price, quality equivalence or monetary spend. It does not prove that the current catalog always selects any particular provider/tier, nor that a cheaper choice could never help. This decision does not claim permanent unnecessariness. Reconsideration requires a future catalog-compatible proposal with exact spend and artifact-quality evidence; an explicit spec amendment would precede any exception to the no-override contract.

## Contracts and implementation boundary

REQ-module-memory-005 retains complete existing tenant isolation, evidence validation, empty-claim no-spawn, dry-run, failure/retry and no-model/runtime/timeout-override scenarios. core-spawner's Dynamic Model Resolution at Spawn Time requirement remains authoritative. Ordinary default 100 and the existing 500 backfill are source constants, not universal batch caps or newly adopted thresholds. Group session counts are bounded by each invocation's actual finite claimed population; no new global cap is asserted.

No controls are adopted by this decision, so no implementation issue is required by the original conditional criterion. Any changed disposition requires its complete threshold/action spec and separate implementation bead before implementation. Related bu-kqnum.1 retains its distinct historic retention/cleanup gates; this decision neither closes them nor changes them.

The original issue completes only after the four decisions and spec delta are reviewed/published, strict OpenSpec validation has a real passing receipt, and bu-kqnum.1 notes point to the published outcome. This evidence document and a draft validator command alone are not those receipts.
