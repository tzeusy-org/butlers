# Switchboard Throughput Scaling Strategy

Status: Normative (Target State)
Last updated: 2026-02-22
Primary owner: Switchboard/Core

Depends on:
- `docs/switchboard/pre_classification_triage.md` (`butlers-0bz3.3`)
- `docs/switchboard/thread_affinity_routing.md` (`butlers-0bz3.4`)
- `docs/connectors/email_ingestion_policy.md` (`butlers-0bz3.5`)
- `docs/architecture/concurrency.md` (async dispatch and concurrent sessions)

## 1. Problem Statement and Throughput Target

Switchboard currently treats every accepted email as classification work, while
classification is constrained by the switchboard runtime session model. Personal
inboxes regularly contain high-noise traffic (promotions, newsletters,
automation) and can arrive in bursts.

Throughput target for v1 personal-email scale:
- Daily volume: up to 120 emails/day (busy personal inbox)
- Classification SLO: `<30s` p95 for messages that still require LLM
  classification
- Burst SLO: no multi-minute queue for a 20-message burst

This spec defines why horizontal throughput scaling is not the v1 answer and
how a layered single-instance strategy achieves target throughput.

## 2. Horizontal Scaling for Throughput: Not Feasible in v1

Running multiple active Switchboard instances to increase throughput is
architecturally infeasible without introducing distributed systems complexity
that is disproportionate to personal-email workloads.

### 2.1 Blockers

| Blocker | Current behavior | Why active-active fails |
|---|---|---|
| Dedup race on idempotency keys | Dedup is local to each process execution path and DB write timing | Two instances can concurrently accept/process the same logical envelope without a distributed lock lease, causing duplicate routing |
| In-memory buffer queue isolation | Hot-path queue is process-local (`asyncio.Queue`) | Messages queued in instance A are invisible to instance B; balancing requires a broker or shared queue coordinator |
| Registry write contention | Routing/health metadata updates converge on shared `butler_registry` state | Parallel writers increase conflict/retry complexity and can degrade routing decisions under contention |
| Process-local routing context | Routing session context is held in process memory | Multi-instance routing requires explicit distributed context propagation and consistency guarantees |

### 2.2 Why this is not solved in v1

To make active-active safe for throughput, v1 would need all of:
- Distributed lock service for dedupe ownership.
- Shared queue fabric (or equivalent broker) replacing process-local queueing.
- Strongly defined cross-instance coordination semantics for routing context and
  registry updates.

That is a different architecture class and exceeds personal-scale operational
requirements.

### 2.3 Valid multi-instance topology in v1

Only active-standby is valid in v1:
- One active Switchboard instance owns routing/classification.
- One standby instance is cold/warm and takes over on active failure.
- Goal is availability, not throughput multiplication.

## 3. Aggregate Throughput Strategy (Single Active Switchboard)

Throughput is achieved by reducing LLM-required workload first, then increasing
classification service rate for the remaining workload.

### 3.1 Five-layer strategy

| Layer | Mechanism | Primary effect | Estimated impact |
|---|---|---|---|
| 1 | Pre-classification triage (`butlers-0bz3.3`) | Deterministic route/skip/metadata decisions before LLM | Removes `50-70%` of potential LLM calls |
| 2 | Thread affinity (`butlers-0bz3.4`) | Routes thread replies to prior target without LLM | Removes about `20%` of post-triage LLM calls |
| 3 | Tiered ingestion (`butlers-0bz3.5`) | Connector drops/skims low-value traffic before classification | Reduces upstream messages entering classification path (environment-dependent, typically `10-25%`) |
| 4 | Async route dispatch (`concurrency.md` 4.4) | Switchboard no longer waits on target butler completion | Switchboard lock hold drops from about `45s` to about `15s` (`3x` service-rate gain on LLM-required messages) |
| 5 | Concurrent switchboard sessions (`concurrency.md` 4.2) | Multiple classifications in parallel (`max_concurrent_sessions=3`) | Additional `3x` throughput gain on LLM-required messages |

### 3.2 120-email/day reference budget

Conservative sequence using sibling-spec assumptions:

1. Baseline: 120 emails/day reach Switchboard.
2. Layer 1 (triage): `120 -> 36-60` LLM-required messages/day.
3. Layer 2 (thread affinity): `36-60 -> 29-48` LLM-required/day.
4. Layer 3 (tiered ingestion): further reduction is expected before
   classification (volume profile dependent).
5. Layers 4+5: service rate for remaining LLM messages increases by about `9x`
   vs synchronous single-session baseline (`3x` from lock-time reduction times
   `3x` from concurrency).

For a 20-message burst, combined layers typically leave only `2-7` LLM
classifications queued, which keeps queueing in tens of seconds rather than
minutes.

## 4. Throughput Model

### 4.1 Definitions

Let:
- `V` = raw emails/day submitted by connectors
- `p_tier` = fraction removed from full classification by tiered ingestion
  before LLM path (`0.0-0.25` typical)
- `p_triage` = pre-classification triage hit rate (`0.50-0.70` typical)
- `p_affinity` = thread-affinity hit rate applied to post-triage remainder
  (`~0.20` typical)
- `S` = switchboard classification lock-hold seconds per LLM-required message
- `C` = concurrent switchboard sessions (`max_concurrent_sessions`)

LLM-required messages/day:

`V_llm = V * (1 - p_tier) * (1 - p_triage) * (1 - p_affinity)`

Classification minutes/day consumed by switchboard:

`M_class = (V_llm * S) / (60 * C)`

With async dispatch + concurrency in this spec:
- `S = 15`
- `C = 3`

### 4.2 Worked examples (50, 120, 250 emails/day)

Assumptions for examples:
- `p_triage = 0.60`
- `p_affinity = 0.20`
- `p_tier` varies by inbox mix

| Volume/day (`V`) | `p_tier` | `V_llm` (messages/day) | `M_class` (switchboard min/day) | Outcome |
|---|---|---|---|---|
| 50 | 0.10 | 14.4 | 1.2 | Well within target; burst behavior dominates user-perceived latency |
| 120 | 0.15 | 32.6 | 2.7 | Meets busy-personal target with low classifier queue pressure |
| 250 | 0.20 | 64.0 | 5.3 | Still feasible for single active instance under personal-scale bursts |

Interpretation:
- Daily aggregate compute is not the limiting factor at personal scale.
- Burst shape (how many arrive at once) is the practical limiter for p95.

### 4.3 Burst queue-depth analysis

For burst size `B` (arriving near-simultaneously):

`B_llm = ceil(B * (1 - p_tier) * (1 - p_triage) * (1 - p_affinity))`

Worst-case queue wait for the last classification:

`W_last = floor((B_llm - 1) / C) * S`

p95 queue wait approximation:

`W_p95 = floor((ceil(0.95 * B_llm) - 1) / C) * S`

Example with `p_tier=0.15`, `p_triage=0.60`, `p_affinity=0.20`, `S=15`, `C=3`:

| Burst size (`B`) | `B_llm` | `W_p95` queue | Approx p95 classification latency (`W_p95 + S`) |
|---|---|---|---|
| 20 | 6 | 15s | 30s |
| 40 | 11 | 45s | 60s |
| 60 | 17 | 75s | 90s |

Conclusion:
- The strategy meets target for personal-scale bursts around 20 emails.
- Larger burst regimes violate the `<30s` p95 target and require v2 scaling
  mechanisms.

## 5. Capacity Ceiling

### 5.1 v1 practical ceiling

For this architecture and target SLO, practical ceiling is:
- Up to roughly 250 emails/day with burst patterns near the 20-message class.
- Above that, especially with repeated 40+ message bursts, p95 classification
  latency trends above 30 seconds.

### 5.2 Insufficient-at threshold

Treat the architecture as insufficient (for throughput SLO) when either holds:
- Observed p95 classification latency >30s for sustained windows, or
- Repeated bursts produce `B_llm > 6` (at current `S=15`, `C=3`) for most
  5-minute periods.

At that point, enable v2 escape hatches (Section 7).

## 6. Active-Standby HA Topology (Availability, Not Throughput)

Active-standby is the only supported multi-instance topology in v1.

### 6.1 Topology

- Active instance acquires and renews a PostgreSQL advisory-lock lease.
- Standby instance polls lease health and remains passive while lease is valid.
- On lease expiry, standby acquires lease and becomes active.

### 6.2 Operational behavior

- Failover target: about 30 seconds, bounded by lease TTL and scanner grace.
- No concurrent active processing: prevents dedupe/routing split-brain.
- Does not increase throughput capacity; only reduces downtime risk.

## 7. Future Escape Hatches (v2+)

If measured workload exceeds Section 5 thresholds, move to one of:

1. Partition-based scaling
- Multiple active Switchboard instances, each owning disjoint partitions
  (channel, account, or sender-group ownership).
- Requires deterministic partition mapping and ownership fencing.

2. Queue-based decoupling
- Introduce shared broker between connectors and Switchboard workers.
- Enables horizontal consumer pools with explicit ack/retry semantics.

These are deliberately out of scope for v1.

## 8. Required Metrics and Dashboard Signals

Switchboard observability MUST include:
- `messages_ingested_per_min`
- `messages_classified_per_min`
- `queue_depth` (hot path + recovered backlog)
- `triage_hit_rate`
- `thread_affinity_hit_rate`
- `tier_distribution` (`tier_1`, `tier_2`, `tier_3`)
- `classification_p95_latency_s`
- `llm_cost_per_message`

Dashboard placement:
- Surface these in `/butlers/switchboard` overview so operators can detect
  approach to capacity ceiling before SLO breach.

## 9. Acceptance Mapping

This spec satisfies `butlers-0bz3.9` by explicitly providing:
1. Horizontal scaling infeasibility with concrete architectural blockers.
2. Five-layer aggregate throughput strategy with per-layer impact estimates.
3. Worked throughput model at 50, 120, and 250 emails/day.
4. Worst-case burst queue-depth analysis.
5. Active-standby HA as the valid v1 multi-instance topology.
6. Capacity ceiling and insufficient-at criteria.
7. References to sibling specs (`butlers-0bz3.3`, `butlers-0bz3.4`,
   `butlers-0bz3.5`) as dependencies.
