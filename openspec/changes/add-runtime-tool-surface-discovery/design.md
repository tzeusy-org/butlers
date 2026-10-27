## Context

See `proposal.md` for motivation and RFC 0027 for the full design contract.
Today, core/module groups decide which handlers FastMCP registers at daemon
startup, and the spawner gives each ordinary runtime exactly one MCP server URL.
Every MCP client sees the same complete registered list. Runtime adapters share
a coarse `tool_use` declaration but construct MCP configuration, control host
tool availability, and parse tool evidence through different code paths.

The FastMCP 3.4.2 public transform can filter before native pagination but
cannot observe the real request cursor. The same offset-only pagination remains
in 3.4.7, 4.0.0b5, and pinned upstream main
`977ba66c811728aff1522bca48e8cc86eb2aec15`. The dated source/release/search
record in `about/legends-and-lore/reviews/0027/round-4-option-b.md` found no
public commitment as of 2026-08-31. Owner-selected Option B therefore keeps
FastMCP complete, makes adapter-rendered filtering the search-corpus boundary,
and uses verified runtime-native search/load for material context reduction.

The implementation must preserve one canonical wrapped handler per tool,
existing infrastructure clients, one-butler MCP isolation, approval behavior,
and a usable eager path on every supported CLI. It must not rely on the
installed CLI generation matching current upstream documentation.

## Goals / Non-Goals

**Goals:**

- Separate the registered/callable surface from the LLM-presentable surface.
- Make runtime-native Tool Search and deferred full-schema loading the primary
  context-efficiency behavior over a runtime-neutral bounded corpus.
- Make the corpus allowlist and adapter rendering explicit without treating
  filtering alone as the optimization.
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

### Decision 2: Keep FastMCP listing complete; adapters own the search corpus

FastMCP remains the complete canonical handler registry and `tools/list`
surface over streamable HTTP and SSE. The runtime MCP URL carries only existing
session/trigger correlation. There is no LLM-presentation marker and no second
filtered MCP endpoint.

After runtime/model resolution, the spawner builds an immutable
`ToolSurfacePlan` whose digest binds attempt identity, catalog generation,
enabled-module snapshot, policy, and exact compatibility key. The plan contains
the canonical-name search corpus plus immutable eager definitions or summaries.
The adapter converts canonical names to host-native names, bounds the corpus
through supported public host configuration, and configures verified native
search/load behavior. Filtering determines eligibility; deferring full schemas
until search time produces the material context savings.

Runtime hosts remain opaque MCP clients and may internally enumerate/page the
complete canonical list. Butlers does not own or validate those host cursors.
They must remain invocation-local and non-model-visible. Conformance uses a
paginated hidden-sentinel server to prove the adapter/host combination never
serializes hidden names, schemas, descriptions, parameters, or counts into
model input/search. A tuple that cannot prove this boundary is ineligible; the
complete list is not a fallback presentation.

The host filter is presentation, not authorization. `tools/call`, handler
lookup, approval, module-state, attribution, and caller checks stay canonical.
Private FastMCP handlers, monkeypatches, duplicate servers, proxies, and
JSON-RPC/SSE rewriting are prohibited.

Alternative rejected: signed caller scopes in this change. That requires a
fleet-wide credential and transport migration and changes doctrine beyond the
tool-discovery motif.

### Decision 3: Use two owner policies and three internal modes

`runtime_config.tool_exposure_policy` is a hot enum:

- `eager_filtered`: require a verified eager-capable profile to serialize the
  full LLM-presentable list; otherwise the candidate is ineligible.
- `auto`: select `native_deferred` only for an exact verified tuple whose native
  granularity represents every allowed load posture; otherwise use a separately
  verified eager profile/candidate or declare the tuple ineligible.

Internal plan modes are `none`, `eager_filtered`, and `native_deferred`. QA and
healing always select `none`. Existing DB rows, absent TOML seed fields, and
new migrations default to `eager_filtered`, so neither schema deployment nor
design acceptance activates native behavior.

An `all_deferred` host cannot satisfy native mode when any presentable tool has
`load_posture=eager`, and cannot satisfy strict `eager_filtered` at all.

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
record and makes `auto` choose a verified eager-capable profile/candidate; when
none exists, the tuple is ineligible rather than exposed unfiltered.

The associated profile also records the public allowlist dialect,
canonical-to-host name mapping, whether filtering changes model availability or
only call permission, eager/native controllability, native deferral granularity,
native search result-limit/ordering behavior, host-pagination isolation, and
parser/receipt support. Permission-only controls do not satisfy
model-presentation filtering.

The adapter invocation-preparation step may reuse the existing
`build_config_file()` method or its current runtime-specific writer. It also
renders the plan allowlist and rejects runtime arguments that could override
the generated MCP/filter configuration. The design does not require all
adapters to converge on one file format.

### Decision 5: Build the plan inside each failover attempt

The current spawner constructs one MCP configuration before the model-failover
loop. The new preparation boundary moves presentation planning, host allowlist,
and MCP assets into each attempt after the candidate runtime/model is known.
Environment ownership,
resume-handle isolation and one logical session row remain unchanged. The
existing cap continues to bound candidate/model attempts; each candidate owns
one initial presentation subattempt plus at most one replay-safe eager
subattempt. Adapter-internal transport retries stay inside the same presentation
subattempt and existing process diagnostics.

Native-to-eager retry is allowed at most once and only for a closed native
transport/protocol failure when the same candidate has a separately verified
eager-capable profile and merged evidence is complete and proves zero MCP and
zero non-MCP effect-capable actions. Without that profile, no presentation
fallback occurs. Shell/command, file-edit/apply-patch, browser/computer, app,
unknown, and parser-ambiguous actions block replay. A valid no-tool/plain-text
response is not failure evidence.

### Decision 6: Search first, then load and call the direct typed tool

Native search initially exposes adapter-rendered bounded namespace/server
summaries and selected eager definitions. Its native index contains only the
attempt corpus. For fixed manifest queries, every intended tool must appear
within the profile-declared result limit, every result must belong to the corpus,
and hidden matches must remain absent. Extra eligible matches and precision are
reported diagnostics unless a separate threshold is approved. Search permits
miss refinement without widening the corpus and may load only presentable full
definitions. The eventual call uses the canonical name/schema and crosses the
normal MCP wrapper.

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

### Decision 8: Native Tool Search admission is evidence-gated

The checked-in conformance manifest fixes scenario IDs, expected outcomes,
runtime samples, allowed retries, cache conditions, malformed-schema
dispositions, and admission-report shape. The credential-free lane uses at
least 100 synthetic tools and separately tests complete canonical HTTP/SSE
listing plus adapter-rendered eager/native model input. A paginated hidden-
sentinel server proves invocation-local host pagination cannot leak hidden
definitions, schemas, or counts. It also tests intended-tool recall within the
declared result limit, corpus-only results, hidden-result exclusion, miss refinement, malformed definitions,
on-demand typed loading, canonical invocation, and receipt extraction. The
authorized lane uses representative real runtime tuples and tests task success,
no-tool completion, approval preservation, attribution, tokens, latency, and
cache behavior without persisting sensitive content.

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
- **[Risk] Native search misses or ranks the required tool poorly.** → Admit an
  exact tuple only after representative top-k recall, corpus/hidden-boundary,
  and miss-refinement cases pass; report precision, extra eligible matches, and
  discovery misses during canary evaluation.
- **[Risk] An opaque host MCP client serializes hidden definitions.** → Bind a
  fresh public host allowlist to every plan and use paginated hidden-sentinel
  conformance to prove hidden definitions, schemas, and counts never enter
  model input/search. Host cursors remain invocation-local and non-model-visible.
- **[Risk] A stale schema remains in model context after module disable.** →
  Snapshot module state for planning and retain the existing call-time guard.
- **[Risk] Discovery events pollute tool-loop or side-effect evidence.** → Keep
  discovery receipts separate from canonical MCP call records and guardrail
  counts.
- **[Risk] Upstream CLI behavior changes without a version bump.** → Bind the
  compatibility key to artifact, adapter-profile, and normalized-configuration
  digests, retain fixture/manifest/result digests as evidence, and on any key
  mismatch select a separately verified eager-capable profile/candidate when
  one is available, otherwise mark the tuple ineligible.
- **[Risk] Hidden presentation is mistaken for security.** → Keep host filters
  presentation-only, leave `tools/call` unchanged, use explicit UI/docs copy,
  and retain separate handler-specific authorization tests.
- **[Risk] Approval wrapping is bypassed by cached callables.** → Store no
  callable in the catalog and finalize descriptors only after gate installation.
- **[Trade-off] Runtime-config scope expands the feature.** → The hot owner
  control is retained because it supplies a safe canary and immediate rollback;
  all associated table/API/UI contracts are included in this changeset.

## Migration Plan

1. Add the runtime-config policy and process-log receipt column with
   conservative defaults; ship API/UI support without changing session
   presentation.
2. Add catalog metadata, exhaustive core classification, complete canonical
   HTTP/SSE listing tests, and eager-filtered receipts. Keep every butler on
   `eager_filtered`.
3. Move per-attempt preparation inside failover and render the immutable
   canonical-name search corpus through each host's supported public
   configuration. Prove hidden sentinels never reach model input/search and
   reject any tuple that cannot enforce this boundary.
4. Produce compatibility records from the synthetic and authorized lanes,
   including top-k recall, corpus/hidden boundaries, miss behavior, precision
   diagnostics, typed loading, initial-byte savings, filter dialect, name
   mapping, eager/native controllability, and host-pagination isolation. CLI
   binary upgrades, if needed, land as separate reviewed dependency changes.
5. Produce a canary-ready compatibility record and rollback runbook. Setting a
   live butler to `auto` requires separate operator authorization.
6. After an authorized canary, expand only if task success, discovery misses,
   retries, tokens, latency, and approval evidence pass the recorded gate.

Rollback sets the affected butler policy to `eager_filtered`. The next attempt
uses a separately verified eager-capable adapter projection only when a matching
profile/candidate is available; otherwise its tuple is ineligible. A tuple that
mandates native deferral is skipped under that policy. Registered handlers,
session history, and in-flight plans are not rewritten. A migration downgrade
removes the new fields only after all rows use verified eager behavior or are
marked ineligible.
