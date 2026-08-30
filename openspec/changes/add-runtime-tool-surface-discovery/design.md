## Context

See `proposal.md` for motivation and RFC 0027 for the full design contract.
Today, core/module groups decide which handlers FastMCP registers at daemon
startup, and the spawner gives each ordinary runtime exactly one MCP server URL.
Every client then sees the same registered list. Runtime adapters share a coarse
`tool_use` declaration but construct MCP configuration and parse tool evidence
through different code paths.

The implementation must preserve one canonical wrapped handler per tool,
existing infrastructure clients, one-butler MCP isolation, approval behavior,
and a usable eager path on every supported CLI. It must not rely on the
installed CLI generation matching current upstream documentation.

## Goals / Non-Goals

**Goals:**

- Separate the registered/callable surface from the LLM-presentable surface.
- Make eager LLM filtering runtime-neutral and native deferred loading an exact-
  tuple optimization.
- Recompute presentation per runtime attempt, including failover attempts.
- Preserve typed direct MCP calls and all existing execution wrappers.
- Give the owner a DB-backed `eager_filtered`/`auto` control with conservative
  migration behavior.
- Produce content-blind evidence sufficient to compare correctness, token load,
  latency, and fallback behavior.

**Non-Goals:**

- Caller authentication, per-session call authorization, or direct-call denial
  for definitions omitted from the LLM projection.
- Code Mode/programmatic nested tool execution in v1.
- A generic tool-search/invoke gateway for eager-only runtimes.
- Semantic prompt classification in the daemon.
- Automatic CLI upgrades or equal token reduction across runtime families.

## Decisions

### Decision 1: Add a presentation catalog, not another handler registry

The daemon builds immutable descriptors for the handlers FastMCP already owns.
The descriptor contains canonical name, module/group, logical namespace,
`llm_presentable`, `load_posture`, and schema/description digests. It contains
no executable function reference.

Catalog finalization occurs after all core and module tools register and after
approval gates install their final wrappers. Calls always resolve by canonical
name through the final FastMCP registry. This prevents deferred discovery from
retaining a pre-approval or pre-observability callable.

Existing unclassified module tools default to presentable/eager during the
migration. Core tools require a checked-in exhaustive classification before a
butler can use `auto`.

Alternative rejected: registering copies on a second FastMCP server. That risks
wrapper drift, duplicate names, and approval bypass.

### Decision 2: Filter `tools/list`; do not alter `tools/call`

The runtime MCP URL gains an explicit LLM-presentation marker alongside the
existing session/trigger correlation parameters. For `tools/list`, the marker
selects the deterministic LLM-presentable projection. Internal clients without
the marker retain the complete registered list. Filtering happens before MCP
pagination, and each cursor is bound to a plan digest containing the catalog-
generation digest, enabled-module-snapshot digest, exposure policy, and resolved
compatibility-key digest so pages cannot mix projections.

The marker is untrusted and non-authoritative. It does not change `tools/call`,
handler lookup, or caller validation. Documentation and UI must not describe a
hidden definition as inaccessible through a forged direct protocol call.
Streamable HTTP and legacy SSE session mapping must yield identical lists.

If FastMCP cannot implement this projection without breaking its caching/list
contract, an adapter-side allowlist may produce the same names. The conformance
suite treats the resulting presentation, not the hook location, as the contract.

Alternative rejected: signed caller scopes in this change. That requires a
fleet-wide credential and transport migration and changes doctrine beyond the
tool-discovery motif.

### Decision 3: Use two owner policies and three internal modes

`runtime_config.tool_exposure_policy` is a hot enum:

- `eager_filtered`: always expose the full LLM-presentable list.
- `auto`: select `native_deferred` only for an exact verified tuple; otherwise
  fall back to `eager_filtered`.

Internal plan modes are `none`, `eager_filtered`, and `native_deferred`. QA and
healing always select `none`. Existing DB rows, absent TOML seed fields, and
new migrations default to `eager_filtered`, so neither schema deployment nor
design acceptance activates native behavior.

The accessor cache is invalidated after PATCH. New runtime attempts see the
updated hot policy; in-flight attempts keep an immutable plan.

Alternative rejected: exposing force-native values. Operators must not be able
to assert unverified CLI integration support.

### Decision 4: Negotiate presentation separately from model feature fit

Model-catalog capability descriptors answer whether a candidate can use tools,
produce structured output, or resume. Presentation support is an integration
fact about the runtime binary, adapter profile, configuration dialect, and
model/provider tuple. It therefore uses a separate repo-owned compatibility
registry that model-catalog overrides cannot forge.

The canonical compatibility key contains runtime type, executable artifact
digest/identity/exact version, adapter-profile revision, configuration dialect
and normalized digest, transport/protocol version, and exact provider/model
IDs. Configuration normalization replaces session IDs, temporary paths, tokens,
credentials, and authorization headers with typed sentinels before canonical
sorted-key hashing. The immutable record adds manifest/fixture/result digests
and verification time as evidence fields. Any key mismatch invalidates the
record and makes `auto` choose eager.

The adapter invocation-preparation step may reuse the existing
`build_config_file()` method or its current runtime-specific writer. The design
does not require all adapters to converge on one file format.

### Decision 5: Build the plan inside each failover attempt

The current spawner constructs one MCP configuration before the model-failover
loop. The new preparation boundary moves presentation planning/assets into each
attempt after the candidate runtime/model is known. Environment ownership,
resume-handle isolation and one logical session row remain unchanged. The
existing cap continues to bound candidate/model attempts; each candidate owns
one initial presentation subattempt plus at most one replay-safe eager
subattempt. Adapter-internal transport retries stay inside the same presentation
subattempt and existing process diagnostics.

Native-to-eager retry is allowed at most once and only for a closed native
transport/protocol failure when merged evidence is complete and proves zero MCP
and zero non-MCP effect-capable actions. Shell/command, file-edit/apply-patch,
browser/computer, app, unknown, and parser-ambiguous actions block replay. A
valid no-tool/plain-text response is not failure evidence.

### Decision 6: Keep native deferred execution direct and typed

Native search initially exposes bounded namespace/server summaries and selected
eager definitions. Search may load only presentable definitions. The eventual
call uses the canonical name/schema and crosses the normal MCP wrapper.

Namespaces reuse module/group concepts and target fewer than ten related tools
where natural. They never rename handlers.

Code Mode remains reserved because current CLI processes can carry filesystem,
shell, configuration, and network authority beyond a nested catalog. A future
design must provide an enforceable narrower capability, not metadata or prompt
guidance alone.

### Decision 7: Persist a bounded attempt array in the existing TTL store

`session_process_logs` remains one TTL-governed row per logical session. An
additive `tool_surface_attempts JSONB NOT NULL DEFAULT '[]'` column stores at
most the spawner's existing candidate-attempt cap. Each candidate item contains
an ordered one-or-two-element `presentation_subattempts` array using the closed
receipt schema from RFC 0027. Updates address candidate and presentation indexes
without discarding earlier failover or discovery-fallback evidence.

Raw queries, prompts, schemas, descriptions, arguments, results, credentials,
provider exception text, and command output are not included. Existing stderr
storage remains governed by its current separate contract.

Alternative rejected: a new unbounded discovery-event table. Attempt-level
receipts share the process log's 14-day lifecycle and do not need independent
query semantics.

### Decision 8: Native enablement is evidence-gated

The checked-in conformance manifest fixes scenario IDs, expected outcomes,
runtime samples, allowed retries, cache conditions, malformed-schema
dispositions, and admission-report shape. The credential-free lane uses at
least 100 synthetic tools and tests filtering
before pagination, stable cursor snapshots, malformed definitions, native
search/load, canonical invocation, and receipt extraction. The authorized lane
uses representative real runtime tuples and tests task success, no-tool
completion, approval preservation, attribution, tokens, latency, and cache
behavior without persisting sensitive content.

A tuple is eligible only when every mandatory scenario passes with no new task,
approval, attribution, replay, or final-outcome failure, and native mode reduces
canonical compact sorted-key UTF-8 initial tool-definition bytes by at least 50
percent from its eager synthetic baseline. Version strings and feature flags
alone cannot enable it. Latency, cache, and total-token changes are reported but
are not pass/fail claims without a separately approved threshold.

## Risks / Trade-offs

- **[Risk] Eager-only runtimes see modest savings.** → Document behavioral
  parity separately from token reduction; do not broaden surfaces on the
  assumption that eager filtering solves schema cost.
- **[Risk] List pagination leaks or mixes hidden definitions.** → Filter before
  pagination and bind opaque cursors to a plan digest containing catalog
  generation, enabled-module snapshot, exposure policy, and resolved
  compatibility-key digest.
- **[Risk] A stale schema remains in model context after module disable.** →
  Snapshot module state for planning and retain the existing call-time guard.
- **[Risk] Discovery events pollute tool-loop or side-effect evidence.** → Keep
  discovery receipts separate from canonical MCP call records and guardrail
  counts.
- **[Risk] Upstream CLI behavior changes without a version bump.** → Bind the
  compatibility key to artifact, adapter-profile, and normalized-configuration
  digests, retain fixture/manifest/result digests as evidence, and return to
  eager on any key mismatch.
- **[Risk] Hidden presentation is mistaken for security.** → Keep query markers
  untrusted, leave `tools/call` unchanged, use explicit UI/docs copy, and retain
  separate handler-specific authorization tests.
- **[Risk] Approval wrapping is bypassed by cached callables.** → Store no
  callable in the catalog and finalize descriptors only after gate installation.
- **[Trade-off] Runtime-config scope expands the feature.** → The hot owner
  control is retained because it supplies a safe canary and immediate rollback;
  all associated table/API/UI contracts are included in this changeset.

## Migration Plan

1. Add the runtime-config policy and process-log receipt column with
   conservative defaults; ship API/UI support without changing session
   presentation.
2. Add catalog metadata, exhaustive core classification, projection tests, and
   eager-filtered receipts. Keep every butler on `eager_filtered`.
3. Move per-attempt preparation inside failover and prove all runtime adapters
   retain eager behavior. Resolve any adapter that declares tool use but does
   not actually wire its MCP configuration before continuing.
4. Produce compatibility records from the synthetic and authorized lanes. CLI
   binary upgrades, if needed, land as separate reviewed dependency changes.
5. Produce a canary-ready compatibility record and rollback runbook. Setting a
   live butler to `auto` requires separate operator authorization.
6. After an authorized canary, expand only if task success, discovery misses,
   retries, tokens, latency, and approval evidence pass the recorded gate.

Rollback sets the affected butler to `eager_filtered`. The next attempt uses
the eager projection; registered handlers, session history, and in-flight plans
are not rewritten. A migration downgrade removes the new fields only after all
rows are returned to eager behavior.
