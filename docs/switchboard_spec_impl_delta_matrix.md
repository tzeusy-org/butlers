# Switchboard Spec-to-Implementation Delta Matrix

Status: Draft (implementation gap analysis)
Issue: `butlers-9aq.1`
Normative source: `docs/roles/switchboard_butler.md` (2026-02-13 role contract)
Analyzed implementation baseline: `agent/butlers-9aq.1` at commit `a02332b`

## Status Legend
- `compliant`: Current implementation materially satisfies the clause.
- `partial`: Some behavior exists, but contract requirements are incomplete.
- `missing`: No meaningful implementation of the required contract.

## Section-by-Section Matrix

| Spec Section | Requirement (abridged) | Current code paths | Status | Required delta (schema/API/runtime) | Migration + test impact | Epic child beads |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Switchboard is single ingress/orchestration control plane | `src/butlers/daemon.py` (`_wire_pipelines`), `src/butlers/modules/telegram.py`, `src/butlers/modules/email.py`, `src/butlers/modules/pipeline.py` | partial | Introduce canonical ingress API boundary and route envelopes so all ingress paths converge through one contract | Add canonical ingest integration tests across Telegram/Email/API/MCP | `butlers-9aq.3` |
| 2 | Design goals (durable context, async ingest, safety, lifecycle clarity, bounded retention) | Routing/classification in `roster/switchboard/tools/routing/*`, lifecycle reactions in `src/butlers/modules/telegram.py`, persistence in `roster/switchboard/migrations/005_create_message_inbox_table.py` | partial | Close gaps on context durability, async admission control, retention partitioning, and safety/versioning | Cross-cutting migrations + conformance test expansion | `butlers-9aq.2`..`butlers-9aq.14` |
| 2.1 | Base-contract overrides (`notify.v1` terminates at Switchboard; no recursive self-notify) | `src/butlers/daemon.py` (`notify` core tool), `roster/switchboard/tools/notification/deliver.py` | missing | Replace ad-hoc `notify(channel,message,recipient)` flow with versioned `notify.v1` envelope handling and messenger-bounded dispatch | New envelope schema tests + routing policy tests | `butlers-9aq.11` |
| 3 | Scope boundaries | Current tooling primarily routes only; domain logic still mostly separated by downstream butlers | partial | Enforce boundaries via policy (reject non-messenger channel tools, keep specialist persistence isolated) | Add registry/policy validation tests | `butlers-9aq.10`, `butlers-9aq.11` |
| 4 | Mandatory canonical request context (`request_id`, source identities, propagation rules) | `src/butlers/modules/pipeline.py` (`_build_source_metadata`), `roster/switchboard/tools/routing/dispatch.py`, `roster/switchboard/tools/routing/route.py` | missing | Add typed `ingest.v1`/`route.v1` models and immutable request-context propagation across fanout | Schema additions in ingress storage + contract tests for required fields/version checks | `butlers-9aq.2`, `butlers-9aq.3` |
| 5 | Ingestion retention contract (month-partitioned lifecycle store, 1-month hot retention) | `roster/switchboard/migrations/005_create_message_inbox_table.py`, `src/butlers/modules/telegram.py` (`process_update` inserts `message_inbox`) | partial | Redesign `message_inbox` as month-partitioned canonical short-lived store; automate partition create/drop; fill missing payload fields | Migration chain for partitioned schema + migration regression tests | `butlers-9aq.9` |
| 6 | Pluggable LLM runtime family for routing | Runtime adapter registry in `src/butlers/core/runtimes/*`; config in `src/butlers/config.py` | partial | Add/align runtime family with spec (including `opencode`) and explicit routing-tier policy surface | Runtime adapter/unit tests for supported families | (follow-up under `butlers-9aq.5`/`butlers-9aq.13`) |
| 6.1 | Prompt-injection safety controls + schema-constrained output + fallback to general | `roster/switchboard/tools/routing/classify.py` (`encoded_message`, explicit untrusted-data instruction, `_parse_classification`) | compliant | Keep current controls; extend to versioned decomposition schema validation | Add adversarial payload tests for schema-version paths | `butlers-9aq.5` |
| 6.2 | Decomposition semantics (segment metadata, route context injection, trigger lineage) | `classify.py`, `dispatch.py`, `route.py` fallback to `trigger` | partial | Add required segment metadata (`offsets/span/rationale`), explicit `subrequest_id`/`segment_id`, versioned fanout-plan metadata | Expand decomposition schema + fanout metadata tests | `butlers-9aq.2`, `butlers-9aq.7` |
| 6.3 | Consume downstream `route_response.v1` envelopes with validation/normalization | `roster/switchboard/tools/routing/route.py` returns raw `result.data`/errors as strings | missing | Implement strict `route_response.v1` parser, timeout synthesis, transport-failure normalization, persisted raw+normalized payload | Add response-envelope validation tests and timeout/transport failure integration tests | `butlers-9aq.6` |
| 7 | Asynchronous ingestion contract (non-blocking accept, bounded admission, durable lifecycle, idempotency) | `src/butlers/modules/telegram.py` (`_poll_loop` sequential), `src/butlers/modules/pipeline.py` (inline classify+route) | missing | Build decoupled ingest acceptance + async worker queue with admission policy and durable handoff | New queue/admission schema (or durable work table) + concurrency/idempotency tests | `butlers-9aq.3`, `butlers-9aq.4`, `butlers-9aq.8` |
| 8 | Interactive lifecycle contract (`PROGRESS`, `PARSED`, `ERRORED` + user-facing error message) | Telegram reaction lifecycle in `src/butlers/modules/telegram.py` (`:eye`, `:done`, `:space invader`) | partial | Add canonical lifecycle state machine persistence and explicit user-visible error replies with actionable context | Add lifecycle transition + user-error delivery tests | `butlers-9aq.12` |
| 9 | Registry ownership contract (metadata richness + runtime reflection) | `roster/switchboard/tools/registry/registry.py`, prompt consumption in `classify.py` | partial | Extend registry schema with trigger conditions, required info, capability declarations, route contract min/max, liveness eligibility | Migration for `butler_registry` extensions + planner validation tests | `butlers-9aq.10` |
| 10.1 | Delivery semantics and messenger ownership | `src/butlers/daemon.py` (`notify`), `roster/switchboard/tools/notification/deliver.py` | missing | Route outbound intents to `messenger_butler` via `notify.v1` envelope; enforce bypass rejection | Policy tests for reject/quarantine of non-messenger channel surfaces | `butlers-9aq.11` |
| 10.2 | Ingress dedupe/idempotency keys by channel | No canonical dedupe path in ingress modules | missing | Add channel-aware dedupe keys and ingress decision logging (`accepted`/`deduped`) | Add dedupe storage/indexes + replay tests for Telegram/Email/API | `butlers-9aq.4` |
| 10.3 | Timeout, retry, circuit-breaker policies | `route.py` has no policy tier timeout/retry/circuit state machine | missing | Introduce per-target timeout + bounded retry/backoff + circuit states | Policy config + resilience tests (retryable vs non-retryable) | `butlers-9aq.8` |
| 10.4 | Backpressure/admission control | No bounded ingress queue/admission outcome model | missing | Add bounded admission, overflow strategy, and fairness policy | Add overload behavior tests and observability assertions | `butlers-9aq.8` |
| 10.5 | Request/route schema versioning | No `schema_version` on ingress/route payloads | missing | Add versioned contracts (`ingest.v1`, `route.v1`, decomposition schema versions) | Migration for version metadata columns + version compatibility tests | `butlers-9aq.2` |
| 10.6 | Stable error taxonomy + terminal mapping | Free-form error strings in routing/pipeline results | partial | Standardize error classes (`validation_error`, `timeout`, etc.) and map unknowns deterministically | Add error-class normalization tests | `butlers-9aq.6`, `butlers-9aq.12` |
| 10.7 | Registry lifecycle/staleness rules | `butler_registry.last_seen_at` updated on successful route (`route.py`) | partial | Add TTL-based eligibility states (`active/stale/quarantined`) and enforce selection policy | Schema change for eligibility status + stale-target routing tests | `butlers-9aq.10` |
| 10.8 | SLO/SLI + error-budget contract | No explicit SLO definitions or error-budget enforcement in runtime docs/config | missing | Define SLI/SLO surfaces and add configurable alert/error-budget policy hooks | Docs + metrics tests and operations config coverage | `butlers-9aq.13` |
| 10.9 | Ordering/causality contract | Limited implicit order in per-loop processing; no explicit causal metadata model | partial | Add per-thread causal sequencing metadata and persisted lineage for reconstruction | Add ordering/concurrency tests with concurrent ingress threads | `butlers-9aq.7`, `butlers-9aq.9` |
| 10.10 | Channel-facing tool ownership (`messenger_butler` only) | Channel tools currently exposed by Telegram/Email modules (`src/butlers/modules/telegram.py`, `src/butlers/modules/email.py`) | missing | Enforce registry/policy gate rejecting channel-facing surfaces outside messenger role | Add policy validation tests at startup/registry refresh | `butlers-9aq.11`, `butlers-9aq.10` |
| 11 | Persistence surfaces (long-term + short-lived lifecycle) | Long-term tables in `sw_001..sw_004`; `message_inbox` in `sw_005`; lifecycle writes in Telegram/pipeline | partial | Keep existing durable tables; redesign short-lived lifecycle store to partitioned canonical envelope with final state + schema version | `message_inbox` migration redesign + lifecycle persistence integration tests | `butlers-9aq.9` |
| 12.1 | OTel metrics + traces per accepted message | Trace spans exist (`switchboard.route`, `switchboard.deliver`, core tool spans); metrics absent | partial | Add first-class switchboard metric instruments and root lifecycle tracing | Add telemetry verification tests (span names + metrics emission) | `butlers-9aq.13` |
| 12.2 | Metrics contract (`butlers.switchboard.*`, low-cardinality attrs) | No switchboard metrics namespace currently implemented | missing | Implement required counters/histograms/gauges and enforce attribute cardinality policy | Add metrics contract tests and regression guardrails | `butlers-9aq.13` |
| 12.3 | Trace contract (root + child spans, required attrs, propagation) | Partial span coverage; trace propagation via `_trace_context` in `route.py` | partial | Add `butlers.switchboard.message` root span and required child spans/attributes including `request.id` lineage | Add trace topology tests with attribute assertions | `butlers-9aq.13` |
| 12.4 | Structured logs and request correlation | Structured logs include source/target/latency; no consistent `request_id` correlation key | partial | Add request-id based correlation across logs/traces/persistence records | Extend logging schema + integration tests for end-to-end reconstruction | `butlers-9aq.13` |
| 13 | Safety/reliability invariants | Fallback-to-general + per-target failure isolation exist (`classify.py`, `dispatch.py`) | partial | Enforce immutable context propagation, terminal lifecycle completion guarantees, and strict tool execution boundaries | Add invariant-focused conformance tests | `butlers-9aq.5`, `butlers-9aq.6`, `butlers-9aq.12` |
| 14 | Change-control rules (docs+migrations+tests must move together) | No enforceable process guardrail in codebase | missing | Add contribution/check workflow requiring contract/doc + migration + integration-test coherence | Add CI lint/check rules and contributor docs | `butlers-9aq.14` |
| 15.1 | Ambiguity resolution policy | General fallback exists, but no confidence-thresholded ambiguity policy | partial | Add confidence scoring and explicit ambiguity tagging in lifecycle records | Add ambiguity-threshold tests and lifecycle tagging assertions | `butlers-9aq.5` |
| 15.2 | Routing precedence rules | No explicit hard-policy layer before LLM output besides known-butler check | missing | Implement deterministic policy pipeline (allow/deny + eligibility) ahead of target selection | Add policy precedence tests | `butlers-9aq.5`, `butlers-9aq.10` |
| 15.3 | Conflict arbitration for downstream conflicts | `aggregate_responses` synthesizes text but no conflict detection/arbitration model | missing | Add aggregation conflict model + deterministic arbitration policy | Add mixed-output conflict tests | `butlers-9aq.7` |
| 15.4 | Fanout dependency model (`parallel`/`ordered`/`conditional`) | `dispatch_decomposed` currently serial only | missing | Implement fanout plan modes with explicit join/abort metadata | Add fanout execution-mode tests and persisted-mode assertions | `butlers-9aq.7` |
| 15.5 | Partial-success response policy | Aggregator can mention unavailable responses; no stable user-facing policy contract | partial | Add deterministic partial-success response envelope + terminal-state rules | Add partial-success contract tests | `butlers-9aq.7`, `butlers-9aq.12` |
| 15.6 | Dead-letter and replay contract | No dead-letter surface or replay workflow | missing | Add dead-letter storage + replay tooling preserving lineage/idempotency | New schema + operator replay tests | `butlers-9aq.14` |
| 15.7 | Per-request budget contract | No wall-clock/tool/cost budget enforcement at route-plan level | missing | Add per-request budget policy and exhaustion terminal mapping | Add budget-exhaustion tests by policy tier | `butlers-9aq.8`, `butlers-9aq.14` |
| 15.8 | Source/urgency policy differentiation | No deterministic source/urgency policy tiering | missing | Add policy tier selection and observable mismatches/fallbacks | Add tier-selection tests | `butlers-9aq.3`, `butlers-9aq.8` |
| 15.9 | Capability compatibility pre-checks | Basic existence checks only; no argument-shape contract validation | partial | Validate planned subroute capability + argument schema before dispatch | Add compatibility validation tests | `butlers-9aq.6`, `butlers-9aq.10` |
| 15.10 | Prompt/model rollout policy | Model/prompt versions not recorded per request; no canary/rollback hooks | missing | Add prompt/model version tagging and rollout-control metadata | Add version-tag persistence tests | `butlers-9aq.13` |
| 15.11 | Quality drift monitoring | No drift metrics/threshold alerts for routing quality | missing | Add routing quality telemetry dimensions and drift-alert policy | Add observability tests for drift dimensions | `butlers-9aq.13` |
| 15.12 | Human override/operator controls | No manual reroute/cancel/replay/force-complete control surface | missing | Add audited operator control-plane endpoints/tools | Add operator action audit tests | `butlers-9aq.14` |
| 17.1 | Canonical ingestion boundary (API-first, adapters call same handler) | Telegram/Email modules call pipeline directly; no shared canonical ingest handler | missing | Build canonical ingest handler and route all adapters through it | Add multi-source integration tests proving common ingest path | `butlers-9aq.3` |
| 17.2 | Source connector types + ingest granularity | Telegram polling + email polling exist; direct API/MCP boundary not unified | partial | Add push-webhook/direct API connectors to canonical boundary and one-record-per-event semantics | Add connector conformance tests | `butlers-9aq.3` |
| 17.3 | Canonical `ingest.v1` event shape | No `ingest.v1` envelope model today | missing | Introduce strict ingest schema model and validators | Add schema contract tests + parser tests | `butlers-9aq.2` |
| 17.4 | Canonical `route.v1` downstream envelope shape | No `route.v1` envelope dispatch today; trigger fallback uses ad-hoc args/context string | missing | Replace ad-hoc route args with typed `route.v1` envelope | Add dispatch contract tests and backward-compat tests | `butlers-9aq.2`, `butlers-9aq.6` |
| 17.5 | Ingestion API semantics (`202`, dedupe at ingest, decoupled execution, private-by-default) | No dedicated ingest API endpoint with `202` semantics and dedupe behavior | missing | Add canonical ingest endpoint and acceptance semantics with idempotent reference return | Add API tests for accepted vs deduped behavior + auth/rate-limit policy tests | `butlers-9aq.3`, `butlers-9aq.4` |

## Sequencing and Dependency Notes (Child Beads)

### Phase 0: Contract and schema foundation
1. `butlers-9aq.2` — Define `ingest.v1` and `route.v1` models first; this is prerequisite for deterministic validation and all later persistence/routing work.
2. `butlers-9aq.10` — Extend registry contract/liveness metadata early so planner validation and routing eligibility have stable data sources.

### Phase 1: Canonical ingest and lifecycle data plane
1. `butlers-9aq.3` — Implement canonical ingest handler and route all connectors through it.
2. `butlers-9aq.9` — Redesign `message_inbox` partitioning/retention after canonical ingest fields are finalized.
3. `butlers-9aq.4` — Add ingress dedupe/idempotency keys on top of canonical ingest boundary.

### Phase 2: Routing safety and response correctness
1. `butlers-9aq.5` — Harden decomposition safety and strict output schema.
2. `butlers-9aq.6` — Implement `route_response.v1` consumption + error normalization.
3. `butlers-9aq.7` — Add explicit fanout dependency modes and partial-success/conflict arbitration.
4. `butlers-9aq.8` — Add timeout/retry/circuit breaker + admission control policies.

### Phase 3: Delivery and lifecycle UX semantics
1. `butlers-9aq.11` — Switchboard `notify.v1` termination + messenger ownership enforcement.
2. `butlers-9aq.12` — Canonical lifecycle state machine and user-visible failure responses.

### Phase 4: Operability and conformance closure
1. `butlers-9aq.13` — Metrics/tracing/log-correlation + SLO/SLI instrumentation.
2. `butlers-9aq.14` — Dead-letter/replay/operator controls + end-to-end conformance harness.

## Key Cross-Cutting Risks
- Route-envelope migration risk: moving from ad-hoc trigger args to `route.v1` impacts every routed butler entrypoint; preserve compatibility shim during migration window.
- Lifecycle persistence risk: partitioning redesign (`message_inbox`) must avoid write-path regressions under concurrent ingestion.
- Observability cardinality risk: request-level labels in metrics must remain prohibited while still enabling trace/log joinability via `request_id`.

