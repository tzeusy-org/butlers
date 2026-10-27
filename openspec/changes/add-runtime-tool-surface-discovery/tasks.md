## 1. Contract and Baseline Gates

- [x] 1.1 Record owner sign-off on the proposal, RFC 0027 decisions, v1 non-goals, and the `eager_filtered`/`auto` operational policy; approval was granted on 2026-08-30 for commit `3448804aaff7b163b4b81deb646db2c9f4ae1397`.
- [x] 1.1a Record the 2026-08-31 owner selection of Option B: canonical FastMCP listing remains complete; adapters render plan-bound searchable corpora, and verified native tuples search/load typed definitions on demand while host MCP cursors remain internal and non-model-visible.
- [ ] 1.2 Refresh the implementation branch from the approved baseline, re-run active-change overlap and body-overwrite checks, and rebuild any affected MODIFIED requirement against the then-current main spec.
- [ ] 1.3 Convert every `core-tool-discovery` requirement into cited test targets and keep code-mode, caller-authentication, generic gateway, and automatic binary-upgrade work outside this changeset.

## 2. Runtime Configuration and Receipt Storage

- [ ] 2.1 Add a fresh core migration for `runtime_config.tool_exposure_policy` with the conservative `eager_filtered` default, closed-value constraint, and forward/backward migration tests across fresh and existing per-butler schemas.
- [ ] 2.2 Extend runtime seed parsing, typed runtime configuration, cached accessor behavior, and cache invalidation for the hot policy; cover missing seed, invalid value, existing-row precedence, and concurrent seed paths.
- [ ] 2.3 Extend the runtime-config GET/PATCH models and routes with field-tier, accepted-value, hot-update, empty-patch, and restart-required regression tests.
- [ ] 2.4 Add the Management-tab exposure-policy control and frontend API types with tests proving accurate `auto` fallback copy, hot-save feedback, and no false restart notification.
- [ ] 2.5 Add a fresh core migration and storage support for bounded `tool_surface_attempts` receipts under `session_process_logs`; test ordered per-attempt preservation, cap enforcement, existing diagnostics, TTL cleanup, and cascade deletion.

## 3. Canonical Catalog and Search Corpus

- [ ] 3.1 Extend module tool metadata with canonical name, namespace, LLM-presentable flag, and eager/deferred load posture while preserving argument-sensitivity behavior and visible/eager defaults for unclassified existing module tools.
- [ ] 3.2 Build the immutable descriptor catalog only after core/module registration and approval wrapping, store model-visible definition data/canonical resolvers but no handler callable, and prove every invocation resolves through the final wrapped FastMCP registry.
- [ ] 3.3 Create and check in an exhaustive registered-tool presentation inventory; add fail-closed `auto` admission tests for missing, stale, duplicate, and nonexistent core/module classifications plus required-tool coverage for `notify` and other spec-mandated LLM tools.
- [ ] 3.4 Implement deterministic catalog projection into an immutable per-attempt canonical-name search corpus plus eager definitions/summaries; bind the artifact to attempt identity, catalog generation, enabled-module snapshot, exposure policy, and resolved compatibility key. Treat the allowlist as the corpus boundary, not the token-saving mechanism.
- [ ] 3.5 Prove canonical FastMCP `tools/list` remains complete and transport-equivalent over streamable HTTP/SSE while unchanged `tools/call`/handler authorization executes every adapter-rendered allowed name.
- [ ] 3.6 Snapshot module enabled state during planning so pre-plan disables disappear from the adapter artifact, while retaining the call-time guard for changes after planning; test re-enable on the next invocation without relying on list-changed support.
- [ ] 3.7 Add cross-runtime skill-projection tests proving every adapter resolves the canonical `.agents/skills` source and that loading a skill cannot change registration, LLM presentation, load posture, or approval behavior.

## 4. Per-Attempt Planning and Adapter Compatibility

- [ ] 4.1 Add repo-owned presentation profiles keyed by runtime type, executable artifact digest/identity/exact version, adapter-profile revision, normalized configuration dialect/digest, transport/protocol version, and exact provider/model IDs; record allowlist dialect, availability semantics, eager/native controllability, native deferral granularity, and native search result-limit/ordering behavior; test every mismatch and reject self-declared support.
- [ ] 4.2 Move semantic surface-plan, search-corpus boundary, and matching eager or native invocation-asset preparation inside each spawner attempt after runtime/model resolution; prepare native search/load only when `native_deferred` is selected, while preserving one-butler MCP isolation, copied environments, resume-handle isolation, and the logical-session attempt cap.
- [ ] 4.3 Implement `none`, `eager_filtered`, and `native_deferred` selection with QA/healing invariants, conservative defaults, hot-policy behavior, immutable in-flight plans, and per-failover recomputation tests.
- [ ] 4.4 Verify every registered tool-capable adapter uses a supported public host filter that prevents the complete MCP list from entering model context/search; repair it or fail closed as ineligible. Strict `eager_filtered` requires an eager-capable tuple.
- [ ] 4.5 Add Codex search-corpus rendering plus native Tool Search, result parsing, query-refinement, and on-demand typed-definition loading only behind a passing exact-tuple profile; keep removed/experimental flags from acting as evidence, treat mandatory host deferral as incompatible with strict `eager_filtered`, and reject `all_deferred` native profiles when any allowed tool requires eager loading.
- [ ] 4.6 Prove OpenCode's public host filter renders the eager adapter projection; keep native OpenCode unadmitted unless a non-Code-Mode contract passes, and evaluate any binary/config change separately.
- [ ] 4.7 Preserve canonical tool-name normalization and server-side call capture across eager and deferred modes, keeping discovery events out of domain tool-call budgets and degenerate-loop accounting.

## 5. Failure, Replay, and Observability

- [ ] 5.1 Implement `preparation_failed`, `native_transport_failed`, and `native_protocol_failed` handling with at most one eager fallback only when the same candidate has a separately verified eager-capable profile and merged MCP/non-MCP effect evidence is complete with both effect counts zero; otherwise no presentation replay occurs.
- [ ] 5.2 Add regressions proving valid plain-text and successful shell-only completions remain valid, while shell/file/app activity, partial MCP execution, unknown events, and adapter/parser ambiguity block replay; also cover failover to a tuple with a different presentation profile.
- [ ] 5.3 Emit one bounded candidate-attempt receipt with one or two ordered presentation subattempts using RFC 0027's exact fields, count units, fallback/outcome enums, and indexes; add sentinel tests excluding prompts, queries, schemas, descriptions, arguments/results, credentials, and raw exceptions.
- [ ] 5.4 Add bounded metrics for mode, runtime-version bucket, outcome, and fallback category plus operators' queries for tokens, latency, discovery misses, retries, and approval outcomes without unbounded tool/model label cardinality.

## 6. Cross-Runtime Conformance and Evaluation

- [ ] 6.1 Build a versioned conformance manifest and credential-free synthetic MCP fixture with at least 100 tools, complete canonical HTTP/SSE pagination, adapter-filtered eager/deferred artifacts, fixed intended/corpus/hidden search cases, profile-declared result limits, miss refinement, precision diagnostics, malformed schemas, fixed outcomes, retry/cache rules, and canonical serialization instructions.
- [ ] 6.2 Add per-adapter structural tests proving complete canonical transport parity, public host-filter model visibility, invocation-local host pagination, hidden name/schema/count omission, canonical direct invocation, receipt parsing, and eager behavior/fallback. For native-candidate tuples only, also prove intended-tool recall within the declared result limit, corpus-only results, hidden-result exclusion, miss refinement, on-demand typed loading, and initial-byte behavior; report extra eligible matches and precision without making them admission thresholds.
- [ ] 6.3 Run the manifest's authorized representative-runtime evaluations for configured Codex and OpenCode tuples, recording every sample/retry and content-blind task-success, no-tool, approval, attribution, token, latency, and cache outcome.
- [ ] 6.4 Admit a native compatibility record containing the complete canonical key plus manifest/fixture/result digests and verification time only when every mandatory case passes with no new task/approval/attribution/replay/final-outcome failure and canonical initial serialized bytes fall by at least 50 percent from that tuple's eager synthetic baseline; preserve the raw no-content admission artifact for review.
- [ ] 6.5 Run targeted unit/integration tests first, then `make test-plan BASE=origin/main`, collection for touched shared/runtime topology, static/spec/overwrite gates, and terminal hosted CI on the exact clean head; do not substitute a local broad lane for hosted merge evidence.

## 7. Documentation and Rollout Handoff

- [x] 7.1 Record owner adoption by marking RFC 0027 and RFC 0002's amendment consistently.
- [ ] 7.2 Update `about/lay-and-land/integration.md`, `docs/concepts/mcp-model.md`, lifecycle/trigger docs, and runtime adapter docs with complete canonical MCP listing versus adapter-owned model presentation.
- [ ] 7.3 Document registered, LLM-presentable, adapter-rendered initially loaded, and natively loaded sets; state that host filters are not authorization, host MCP pagination is internal, and eager-only runtimes receive no guaranteed large token reduction.
- [ ] 7.4 Produce a canary/rollback runbook that keeps all existing rows eager, requires separate operator authorization before setting one live butler to `auto`, and on any CLI/profile mismatch or task-quality regression selects a separately verified eager-capable profile/candidate when one is available, otherwise treats the tuple as unavailable/ineligible.
- [ ] 7.5 Verify no implementation task added a generic gateway, Code Mode, caller authentication, semantic daemon routing, or automatic binary upgrade; route any such discovery through a separate spec-first change.
