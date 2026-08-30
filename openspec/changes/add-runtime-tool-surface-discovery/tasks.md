## 1. Contract and Baseline Gates

- [x] 1.1 Record owner sign-off on the proposal, RFC 0027 decisions, v1 non-goals, and the `eager_filtered`/`auto` operational policy; approval was granted on 2026-08-30 for commit `3448804aaff7b163b4b81deb646db2c9f4ae1397`.
- [ ] 1.2 Refresh the implementation branch from the approved baseline, re-run active-change overlap and body-overwrite checks, and rebuild any affected MODIFIED requirement against the then-current main spec.
- [ ] 1.3 Convert every `core-tool-discovery` requirement into cited test targets and keep code-mode, caller-authentication, generic gateway, and automatic binary-upgrade work outside this changeset.

## 2. Runtime Configuration and Receipt Storage

- [ ] 2.1 Add a fresh core migration for `runtime_config.tool_exposure_policy` with the conservative `eager_filtered` default, closed-value constraint, and forward/backward migration tests across fresh and existing per-butler schemas.
- [ ] 2.2 Extend runtime seed parsing, typed runtime configuration, cached accessor behavior, and cache invalidation for the hot policy; cover missing seed, invalid value, existing-row precedence, and concurrent seed paths.
- [ ] 2.3 Extend the runtime-config GET/PATCH models and routes with field-tier, accepted-value, hot-update, empty-patch, and restart-required regression tests.
- [ ] 2.4 Add the Management-tab exposure-policy control and frontend API types with tests proving accurate `auto` fallback copy, hot-save feedback, and no false restart notification.
- [ ] 2.5 Add a fresh core migration and storage support for bounded `tool_surface_attempts` receipts under `session_process_logs`; test ordered per-attempt preservation, cap enforcement, existing diagnostics, TTL cleanup, and cascade deletion.

## 3. Canonical Catalog and LLM Projection

- [ ] 3.1 Extend module tool metadata with canonical name, namespace, LLM-presentable flag, and eager/deferred load posture while preserving argument-sensitivity behavior and visible/eager defaults for unclassified existing module tools.
- [ ] 3.2 Build the immutable descriptor catalog only after core/module registration and approval wrapping, store no handler callable, and prove every invocation resolves through the final wrapped FastMCP registry.
- [ ] 3.3 Create and check in an exhaustive registered-tool presentation inventory; add fail-closed `auto` admission tests for missing, stale, duplicate, and nonexistent core/module classifications plus required-tool coverage for `notify` and other spec-mandated LLM tools.
- [ ] 3.4 Implement the deterministic LLM `tools/list` projection using the untrusted presentation marker, filtering before pagination and binding cursors to the attempt plan digest including catalog generation, enabled-module snapshot, exposure policy, and resolved compatibility-key digest; test invalidation for each component.
- [ ] 3.5 Prove identical projection semantics over streamable HTTP and legacy SSE, full-list behavior for existing internal clients, and unchanged `tools/call`/handler authorization behavior for omitted names.
- [ ] 3.6 Snapshot module enabled state during planning so pre-plan disables disappear from the projection, while retaining the call-time guard for changes after planning; test re-enable on the next invocation without relying on list-changed support.
- [ ] 3.7 Add cross-runtime skill-projection tests proving every adapter resolves the canonical `.agents/skills` source and that loading a skill cannot change registration, LLM presentation, load posture, or approval behavior.

## 4. Per-Attempt Planning and Adapter Compatibility

- [ ] 4.1 Add repo-owned presentation profiles keyed by runtime type, executable artifact digest/identity/exact version, adapter-profile revision, normalized configuration dialect/digest, transport/protocol version, and exact provider/model IDs; test mismatch invalidation for every field and reject operator/model-catalog attempts to self-declare support.
- [ ] 4.2 Move semantic surface-plan and invocation-asset preparation inside each spawner attempt after runtime/model resolution while preserving one-butler MCP isolation, copied environments, resume-handle isolation, and the logical-session attempt cap.
- [ ] 4.3 Implement `none`, `eager_filtered`, and `native_deferred` selection with QA/healing invariants, conservative defaults, hot-policy behavior, immutable in-flight plans, and per-failover recomputation tests.
- [ ] 4.4 Verify every registered tool-capable adapter can render and invoke the eager LLM projection; any adapter that cannot actually wire MCP must be repaired or fail-closed as not tool-capable before it remains eligible.
- [ ] 4.5 Add Codex native-deferred preparation and discovery-event parsing only behind a passing exact-tuple profile; keep removed/experimental feature flags from acting as enablement evidence.
- [ ] 4.6 Keep OpenCode on verified eager presentation unless a non-Code-Mode native deferred contract passes; evaluate any candidate binary/config dialect in an isolated dependency change and leave Code Mode disabled.
- [ ] 4.7 Preserve canonical tool-name normalization and server-side call capture across eager and deferred modes, keeping discovery events out of domain tool-call budgets and degenerate-loop accounting.

## 5. Failure, Replay, and Observability

- [ ] 5.1 Implement `preparation_failed`, `native_transport_failed`, and `native_protocol_failed` handling with at most one eager fallback only when merged MCP/non-MCP effect evidence is complete and both effect counts are zero.
- [ ] 5.2 Add regressions proving valid plain-text and successful shell-only completions remain valid, while shell/file/app activity, partial MCP execution, unknown events, and adapter/parser ambiguity block replay; also cover failover to a tuple with a different presentation profile.
- [ ] 5.3 Emit one bounded candidate-attempt receipt with one or two ordered presentation subattempts using RFC 0027's exact fields, count units, fallback/outcome enums, and indexes; add sentinel tests excluding prompts, queries, schemas, descriptions, arguments/results, credentials, and raw exceptions.
- [ ] 5.4 Add bounded metrics for mode, runtime-version bucket, outcome, and fallback category plus operators' queries for tokens, latency, discovery misses, retries, and approval outcomes without unbounded tool/model label cardinality.

## 6. Cross-Runtime Conformance and Evaluation

- [ ] 6.1 Build a versioned conformance manifest and credential-free synthetic MCP fixture with at least 100 tools, natural namespaces, eager/deferred definitions, infrastructure-only definitions, malformed schemas, multi-page `tools/list` responses, fixed expected outcomes, retry/cache rules, and canonical serialization instructions.
- [ ] 6.2 Add per-adapter structural conformance tests for eager projection, native search/load where supported, canonical direct invocation, pagination/cursor isolation, hidden-definition omission, receipt parsing, and eager fallback.
- [ ] 6.3 Run the manifest's authorized representative-runtime evaluations for configured Codex and OpenCode tuples, recording every sample/retry and content-blind task-success, no-tool, approval, attribution, token, latency, and cache outcome.
- [ ] 6.4 Admit a native compatibility record containing the complete canonical key plus manifest/fixture/result digests and verification time only when every mandatory case passes with no new task/approval/attribution/replay/final-outcome failure and canonical initial serialized bytes fall by at least 50 percent from that tuple's eager synthetic baseline; preserve the raw no-content admission artifact for review.
- [ ] 6.5 Run targeted unit/integration tests first, then `make test-plan BASE=origin/main`, collection for touched shared/runtime topology, static/spec/overwrite gates, and terminal hosted CI on the exact clean head; do not substitute a local broad lane for hosted merge evidence.

## 7. Documentation and Rollout Handoff

- [x] 7.1 Record owner adoption by marking RFC 0027 and RFC 0002's amendment consistently.
- [ ] 7.2 Update `about/lay-and-land/integration.md`, `docs/concepts/mcp-model.md`, lifecycle/trigger docs, and runtime adapter documentation with current shipped behavior.
- [ ] 7.3 Document the distinction between registered, LLM-presentable, and initially loaded sets; state explicitly that hidden presentation is not authorization and eager-only runtimes receive no guaranteed large token reduction.
- [ ] 7.4 Produce a canary/rollback runbook that keeps all existing rows eager, requires separate operator authorization before setting one live butler to `auto`, and returns to eager on any CLI/profile mismatch or task-quality regression.
- [ ] 7.5 Verify no implementation task added a generic gateway, Code Mode, caller authentication, semantic daemon routing, or automatic binary upgrade; route any such discovery through a separate spec-first change.
