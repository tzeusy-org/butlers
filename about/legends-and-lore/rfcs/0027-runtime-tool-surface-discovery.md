# RFC 0027: Runtime Tool Surface Discovery and Exposure

**Status:** Accepted
**Date:** 2026-08-30
**Baseline:** `ba1c55c31056af5eaa362a254193bbf43741e86a`
**Related:** RFC 0001, RFC 0002, RFC 0005, RFC 0008

**Owner approval:** 2026-08-30, artifact
`3448804aaff7b163b4b81deb646db2c9f4ae1397`. Approval adopts this design and
permits sequencing; it does not request implementation, merge, deployment,
runtime binary upgrades, or live canary activation.

**Owner amendment:** 2026-08-31, Option B from `bu-g5fha`. Canonical FastMCP
listing remains complete; each runtime adapter turns an immutable, plan-bound
search corpus into runtime-native Tool Search/deferred loading when the exact
tuple is verified. The allowlist bounds that corpus; it is not the optimization.
The amendment is reversible and authorizes contract and Beads updates only. It
does not authorize implementation, dependency upgrades, merge, provider
evaluation, policy activation, deployment, or live canary execution.

## Summary

This RFC makes runtime-native Tool Search and deferred schema loading the
primary context-efficiency mechanism over each butler's canonical MCP tool
registry. FastMCP continues to expose the complete registered list to every MCP
client. For a `native_deferred` attempt, the adapter renders a small initial
working set plus a searchable corpus of eligible tools; the verified runtime
host searches that corpus and loads a matching full typed definition only when
needed. An `eager_filtered` attempt instead renders every allowed definition and
prepares no native search path.

The plan-bound allowlist defines the searchable corpus and keeps
infrastructure-only definitions out of model-visible input. It does not itself
deliver the material token reduction: an eager-only host still serializes every
allowed schema. Such hosts use the same bounded corpus as a compatibility path,
with deliberately modest context savings.

This is a presentation contract, not a new authorization system. Every exposed
definition still resolves to the original registered and wrapped FastMCP
handler. Existing handler authorization, module-state checks, approval gates,
schema validation, egress ownership, tracing, and tool-call capture remain the
execution authority. The unfiltered MCP endpoint remains available to existing
infrastructure clients.

This RFC supersedes RFC 0002's assumption that every registered
tool definition must be fully serialized into every LLM session and resolves
its deferred registered-but-LLM-hidden presentation tier. It does not supersede
RFC 0002's registration, module, approval, skills, or MCP-only communication
contracts.

## Motivation

RFC 0002 targets 30-50 tools per butler because eager discovery made every
registered schema a per-session context cost. A content-blind live-development
audit on 2026-08-30 observed 1,133 tools across 12 healthy butlers (94.4 per
butler), approximately 20.6k serialized tool-definition tokens per butler by
the JSON-character/4 estimate. Recent use was much narrower: Health used 18 of
146 advertised MCP tools, Finance 26 of 101, and Lifestyle 12 of 120.

The counts establish a context-efficiency opportunity, not permission to widen
authority. Per-butler specialization, manifesto alignment, core and module
groups, type/name gates, module state, and approval controls remain mandatory.
The missing abstraction is a runtime-neutral searchable corpus over the
registered surface, rendered into each host's native search/load mechanism
without replacing typed MCP execution.

Codex, OpenCode, Claude Code, Gemini, and future adapters differ in MCP
configuration, feature maturity, provider/model support, tool naming, and event
output. Butlers therefore owns the semantic plan; each adapter renders only the
features verified for its concrete runtime tuple.

## Governing Invariants

1. **MCP remains the executable interface.** Discovery resolves existing MCP
   definitions and handlers; it does not add a generic invocation protocol.
2. **Registration precedes presentation.** A tool excluded by effective core
   groups, module groups, type/name gates, or startup failure never appears in
   an LLM projection.
3. **Presentation is not authorization.** An omitted name is not a security
   boundary. Existing handler checks still decide whether a call may execute.
4. **The daemon stays deterministic.** It uses declared metadata, operational
   policy, and compatibility records; it does not semantically classify a
   prompt to decide which tools the task deserves.
5. **Native discovery is optional.** Unknown native support yields a separately
   verified eager profile when one exists; otherwise the tuple is ineligible.
6. **Skills guide but never grant.** Loading guidance cannot change tool
   registration, presentation, or call-time authority.
7. **No replay after effects.** Discovery fallback uses the existing
   pre-side-effect failover boundary.
8. **Canonical listing stays complete.** FastMCP `tools/list` is the complete
   registered protocol surface over streamable HTTP and SSE; it is not the
   model-presentation boundary.
9. **Adapters fail closed.** Every tool-bearing tuple must prove a supported
   public host filter that keeps non-plan definitions out of model context and
   native search. A tuple without that boundary is ineligible.

## Layered Surface Model

For one butler:

| Layer | Definition | Owner |
|---|---|---|
| Registered/callable set | Handlers admitted by startup configuration and available from the canonical MCP registry | Daemon + modules |
| LLM-presentable set | Registered definitions classified for ordinary LLM discovery | Tool catalog metadata |
| Initially loaded set | Presentable definitions or summaries placed in initial model context | Exposure plan + adapter + runtime host |
| Loaded set | Additional presentable definitions imported after native search | Adapter + runtime host |

The subset law is load-bearing:

```text
initially loaded <= LLM-presentable <= registered/callable
loaded            <= LLM-presentable <= registered/callable
```

The loaded set is model context, not server state or authority. Module disable,
approval, and handler-specific authorization are checked again when the
canonical handler is called.

The old 30-50 target becomes an initial-working-set target. It is no longer a
hard ceiling on the registered set, but group and manifesto pruning remain
required because they encode role fit and prevent cross-domain capability
sprawl.

## Tool Metadata Contract

Every definition eligible for native deferred discovery has a descriptor tied
to its registered handler:

```text
canonical_name       stable MCP handler name
module_name          core or owning module
group_name           core/module registration group
namespace            bounded logical discovery namespace
llm_presentable      boolean
load_posture         eager | deferred
schema_digest        digest of the model-visible input schema
description_digest   digest of the model-visible description
```

`llm_presentable = false` omits the definition from the LLM projection. It does
not add a call-time denial to the canonical MCP endpoint. `load_posture`
controls only whether a presentable definition is fully serialized initially
or may be imported by native search.

During migration, an existing unclassified module tool retains its current
LLM-presentable eager behavior. It is ineligible for native deferred loading
until classified. Every registered core and module handler must receive an
explicit classification before `auto` policy can select native deferred mode
for that butler; incomplete classification forces the eager projection.

Metadata never overrides registration, module enabled state, approval rules,
channel-egress ownership, handler validation, or existing caller checks.

The catalog is finalized only after core and module registration and after
approval gates have replaced/wrapped their final handlers. It stores immutable
model-visible definition data, canonical names, and digests, never a pre-gate
callable. Adapters may serialize those definitions or canonical resolvers into
host-native assets. All invocation continues through the final wrapped MCP
registry so discovery cannot retain a bypass reference.

## LLM Visibility Classification

The first classification sweep is exhaustive across the complete core
inventory. At minimum, the following server-facing categories are not
LLM-presentable unless a separate capability contract explicitly requires
ordinary LLM use:

- routed-request admission,
- daemon tick, shutdown, and recovery controls,
- session cancellation,
- Switchboard ingest, routing, connector-heartbeat, and backfill callbacks,
- server-to-server delegation wakes and domain-event delivery callbacks,
- dashboard-only runtime/module management writes.

Ordinary LLM capabilities such as state, scheduling, memory access,
notifications, media, temporal reasoning, delegation initiation/answering,
event publication/subscription, and domain module tools remain presentable when
their existing configuration admits them. Existing capability specs such as
the required `notify` tool remain authoritative: deferred loading may omit a
full schema initially but cannot make a mandatory LLM capability undiscoverable.

The implementation must produce a checked-in LLM-presentation inventory
covering every core tool name. CI fails if a new core tool lacks a classification
or if a classification names no registered tool.

## Adapter-Owned Search Corpus and Model Presentation

The generated runtime MCP URL carries only the existing
`runtime_session_id` and `trigger_source` correlation parameters. FastMCP
`tools/list` remains the complete registered protocol surface for every caller
over streamable HTTP and legacy SSE. No query marker turns the canonical MCP
endpoint into an LLM projection boundary.

For each runtime/model candidate, the spawner creates an immutable
`ToolSurfacePlan`. Its digest binds attempt identity, catalog generation,
enabled-module snapshot, exposure policy, and resolved compatibility key. The
plan contains the exact canonical-name search corpus plus immutable eager
definitions or summaries. The adapter renders the corpus through the runtime
host's supported public configuration and, for verified native tuples, configures
the host's Tool Search/index/load path. The allowlist is the corpus boundary;
search-driven omission of full schemas from the initial prompt is the
context-efficiency feature.

Host-native names are derived deterministically from canonical MCP names and
recorded in the compatibility profile. A host may enumerate and paginate the
complete MCP list internally, but its cursor is host-private transport state,
not a Butlers plan cursor. Host pagination stays within one invocation and is
never reused as presentation state across plans. The adapter must prove through
conformance that hidden sentinel names, schemas, descriptions, parameter data,
and counts never enter model-visible input or native search results.

The allowlist is deterministic for the attempt and does not mutate in response
to search calls, connection history, or another runtime session. A forged,
stale, missing, or conflicting host filter fails preparation. A runtime tuple
that cannot prevent its host from independently serializing the complete MCP
list is ineligible for tool-bearing work; the complete list is never a
presentation fallback.

Tool calls continue by canonical name through the complete MCP endpoint and
its existing final handler checks. Model-visible omission remains presentation,
not authorization, and must never be described as preventing a forged direct
protocol call to an otherwise reachable handler.

## Exposure Planning State Machine

Per-schema operational policy has two owner-facing values:

- `eager_filtered`: require the adapter/host to serialize the full
  LLM-presentable definitions eagerly.
- `auto`: choose verified native deferred discovery for the resolved tuple;
  otherwise use a separately verified eager profile/candidate or declare the
  tuple ineligible.

Existing rows and missing first-boot configuration default to
`eager_filtered`. Accepting or deploying the schema does not activate native
discovery.

The v1 internal plan modes are:

- `none`: no live butler MCP surface; mandatory for QA and healing.
- `eager_filtered`: full definitions for the LLM-presentable set.
- `native_deferred`: bounded namespace/server summaries plus on-demand loading
  of presentable typed definitions.

Selection is deterministic and also requires the profile's native deferral
granularity to represent every allowed tool's `load_posture`:

```text
if trigger_source in {qa, healing}: none
else if policy == eager_filtered and verified eager profile exists: eager_filtered
else if policy == eager_filtered: ineligible
else if classification complete
        and native_deferred verified for exact tuple
        and every allowed load_posture is representable: native_deferred
else if verified eager profile exists: eager_filtered
else: ineligible
```

A same-tier model failover recomputes the plan for the new runtime, CLI version,
configuration dialect, and model/provider tuple. Plans and provider-native
resume handles never cross adapters. A hot policy update applies to newly
planned attempts; an in-flight attempt keeps its immutable plan.

Some hosts may mandate native deferral when Tool Search is available. Such a
tuple cannot satisfy strict `eager_filtered` policy and is skipped for an
eager-capable candidate. The policy is not silently redefined to permit native
behavior.

## Capability Negotiation and Adapter Contract

The existing adapter `tool_use` declaration proves only that a runtime can use
tools. Each adapter also needs a content-blind presentation capability profile.
The canonical `CompatibilityKey` is:

- `runtime_type`, executable artifact digest, identity, and exact version,
- adapter-profile revision,
- configuration dialect and normalized configuration digest,
- MCP transport and protocol version,
- exact provider ID and model ID.

The profile associated with the key declares supported presentation modes,
allowlist dialect, canonical-to-host tool-name mapping, whether the host filter
changes availability or only permission, eager/native controllability, native
granularity (`all_deferred` or selective), skill convention, host-pagination
isolation, native search result-limit/ordering behavior, and
discovery-event/receipt parser support. The normalized
configuration digest uses canonical sorted-key serialization after replacing
session IDs, temporary paths, tokens, credentials, and authorization headers
with fixed typed sentinels.

Support is keyed by the exact `CompatibilityKey`,
not a CLI family name or optimistic version prefix. Feature strings or
experimental flags make a tuple eligible for conformance testing, not for
automatic enablement.

Native selection additionally requires the profile's deferral granularity to
represent every allowed tool's load posture. An `all_deferred` host is
ineligible for native mode when any presentable tool requires eager loading;
the planner then chooses a separately verified eager candidate or declares the
tuple ineligible.

Invocation preparation becomes an explicit adapter responsibility while the
existing `build_config_file()` method remains as the compatibility primitive
for adapters that use it. Adapters may compose their current builder,
runtime-owned config writer, or supported CLI flags; they must not be assumed
to share a single file format. Conflicting runtime arguments that could replace
the generated MCP/filter configuration are rejected. A tool-capable adapter
that cannot prove a public model-presentation filter is ineligible for
tool-bearing work. A runtime that cannot invoke MCP at all is likewise
ineligible.

## Native Tool Search and Deferred Loading Contract

The adapter-owned initial artifact contains stable namespace/server summaries
and any definitions explicitly classified eager. This deliberately small
initial artifact is the source of the token-efficiency gain. Native host Tool
Search indexes only the immutable attempt corpus, returns only LLM-presentable
matches from that corpus, and loads complete typed definitions
through the host's native protocol. For each fixed conformance query, every
intended tool must appear within the profile-declared result limit, every result
must belong to the immutable corpus, and infrastructure-only tools must remain
absent. Extra eligible matches and precision are reported diagnostics unless a
separate threshold is approved. The eventual call uses the canonical MCP name
and input schema.

Native conformance must establish useful search behavior, not merely the
existence of an enablement flag: representative queries find the intended tool,
all results stay within the corpus, infrastructure-only matches stay absent, a
search miss can be refined without widening the corpus, and the loaded
definition remains direct and typed.

An `all_deferred` native host is eligible only when no allowed definition has
`load_posture=eager`. Selective hosts must prove they preserve every eager
definition initially while deferring only definitions marked deferred.

Logical namespaces should be cohesive. The implementation target is fewer than
ten related tools per namespace where existing module/group boundaries support
that without aliases. Namespaces are presentation metadata; they do not rename
MCP handlers or change ownership.

Approval-capable and externally consequential tools are permitted in native
deferred discovery because the loaded definition still produces an ordinary
direct MCP call. The normal approval and execution wrappers remain mandatory.

Programmatic/code-mode execution is reserved, not a v1 plan mode. It cannot be
enabled until a separate approved design proves that the nested runtime has a
distinct, narrower capability, cannot access the broader CLI configuration or
arbitrary MCP/network/shell paths, and preserves approval and partial-call
evidence. Metadata or prompt guidance alone is insufficient isolation.

## Eager Compatibility Contract

`eager_filtered` requires the adapter to render the complete LLM-presentable
definitions through the host's public allowlist/configuration mechanism before
model serialization. The underlying MCP list remains complete. The adapter
preserves canonical names, schemas, parser behavior, and the current direct-call
flow on every eligible CLI.

This is the compatibility and rollback path, not the token-efficiency feature.
Its performance promise is deliberately limited. It removes definitions
classified infrastructure-only but still serializes every LLM-presentable
schema. Substantial context reduction on a high-surface butler requires a
verified native-deferred tuple; v1 does not introduce a generic search/invoke
gateway to simulate that on legacy runtimes.

## Failure and Replay Safety

Presentation fallback uses a closed trigger vocabulary:

- `preparation_failed`: assets could not be rendered before subprocess launch.
- `native_transport_failed`: the adapter parsed an explicit MCP transport or
  connection failure event.
- `native_protocol_failed`: the adapter parsed an explicit native
  search/load-protocol failure event.

`preparation_failed` may render eager assets without process replay only when
the same candidate has a separately verified eager-capable profile. The two
native runtime categories permit at most one eager fallback only when that same
profile exists and the effect predicate is conclusively clean:

```text
effect_evidence_complete == true
and canonical_mcp_call_count == 0
and non_mcp_effect_capable_call_count == 0
```

Without a verified eager-capable profile, no presentation fallback occurs.
Normal model-candidate failover may proceed only under its existing independent
eligibility rules.

Canonical MCP evidence comes from server-side capture reconciled with adapter
events. Non-MCP effect-capable evidence includes shell/command execution, file
write/edit/apply-patch, browser/computer actions, external app calls, and any
future host tool not explicitly proven side-effect-free by its contract. The
adapter parser must account for every emitted tool/effect event type. Missing,
unknown, malformed, or parser-ambiguous evidence sets
`effect_evidence_complete = false` and blocks replay.

Plain-text completion with no tool call is valid unless an explicit closed
failure trigger exists. Shell-only completion is likewise valid when the
invocation succeeds, but a later native failure cannot replay it because shell
execution is effect-capable. Zero calls alone is never a failure signal.

One logical session contains candidate attempts (model/runtime failover), each
with at most two presentation subattempts: initial mode plus optional eager
fallback. Candidate attempts keep the existing cap of ten. Adapter-internal
transport retries remain process diagnostics within one presentation
subattempt. No candidate or presentation receipt may overwrite another.

## Skills and Tools

`.agents/skills/` remains the canonical butler skill source. An adapter may
materialize or link a runtime-specific discovery layout, but every projection
resolves the same canonical skill identity and content.

Skill and tool search remain independent:

- Skill loading adds instructions to model context.
- Tool discovery adds schemas from the LLM-presentable set.
- A skill may recommend a canonical tool or namespace but cannot register,
  present, or approve a tool.
- Missing native skill search does not alter the tool plan.

## Observability and Privacy

Each candidate attempt emits one bounded receipt with an ordered
`presentation_subattempts` array of length one or two. Every subattempt uses the
following closed schema:

```text
mode
policy
candidate_attempt_index
presentation_subattempt_index
runtime_type
runtime_version
runtime_artifact_digest
adapter_profile_revision
configuration_dialect
configuration_digest
transport
protocol_version
provider_id
model_id
registered_count
llm_presentable_count
initially_exposed_count
initially_exposed_schema_bytes
initial_summary_count
initial_summary_bytes
loaded_count
discovery_call_count
fallback_category
outcome
effect_evidence_complete
canonical_mcp_call_count
non_mcp_effect_capable_call_count
compatibility_record_digest
```

All numeric fields are non-null non-negative integers. `registered_count`,
`llm_presentable_count`, `initially_exposed_count`, and `loaded_count` count
distinct canonical tool definitions. `initial_summary_count` counts distinct
namespace/server summary entries. `discovery_call_count`,
`canonical_mcp_call_count`, and `non_mcp_effect_capable_call_count` count
operation occurrences. Byte fields count canonical compact sorted-key UTF-8
bytes. Initial full-definition fields cover definitions serialized before model
execution; summary fields exclude full schemas; `loaded_count` covers distinct
deferred full definitions imported afterward and is zero for eager mode.

`fallback_category` is one of `none`, `policy_forced_eager`,
`classification_incomplete`, `tuple_unverified`, `tuple_unsupported`,
`profile_mismatch`, `preparation_failed`, `native_transport_failed`, or
`native_protocol_failed`. `outcome` is one of `prepared`, `completed`,
`failed_pre_effect`, `failed_post_effect`, `failed_effect_unknown`,
`fallback_succeeded`, or `fallback_failed`. Incomplete effect evidence retains
the original native failure category, sets `effect_evidence_complete=false`,
and uses `failed_effect_unknown`.

The receipt contains counts, enums, stable runtime/model identifiers already in
dispatch provenance, and digests. It excludes prompts, search queries,
descriptions, schemas, tool arguments/results, credentials, provider exception
text, and command output.

Existing server-side tool capture remains execution truth. A discovery hit is
not a domain tool invocation. Canonical MCP calls loaded through search must
still appear in the normal server capture.

Receipts follow existing process-log retention and create no unbounded history.
Metrics use bounded labels only; model IDs and tool names stay out of metric
labels where cardinality would grow without bound.

## Tool Search Conformance and Rollout Gate

Every native tuple starts unverified. A checked-in, versioned conformance
manifest defines scenario IDs, expected outcomes, runtime samples, allowed
retries, cache conditions, malformed-schema dispositions, and the admission
report schema. Verification has two lanes:

1. A credential-free synthetic MCP server with at least 100 tools across
   representative namespaces proves the canonical HTTP/SSE list remains
   complete, then proves each adapter's host filter keeps hidden sentinels out
   of model-visible eager input and native search. For native tuples it also
   proves intended-tool recall within the declared result limit, corpus-only
   results, exclusion of hidden matches, search-miss refinement, on-demand typed definition loading,
   canonical invocation, malformed-schema handling, fallback, and receipt
   extraction. Precision and extra eligible matches remain reported diagnostics.
2. An authorized representative runtime evaluation proves task success,
   no-tool completion, approval preservation, call attribution, context cost,
   latency, and cache behavior without logging sensitive content.

A compatibility record is immutable and contains the complete
`CompatibilityKey` plus conformance-manifest digest, fixture digest, result
digest, and verification timestamp. Evidence/result fields are not key fields.
Any `CompatibilityKey` mismatch invalidates the record. Native Tool Search
admission requires all mandatory manifest scenarios passing, no new task/approval/
attribution/replay/final-outcome failure relative to eager mode, and at least
50 percent fewer initially serialized tool-definition bytes than the tuple's
eager-filtered synthetic baseline.

Byte measurement uses canonical compact sorted-key JSON encoded as UTF-8. The
eager denominator is the complete initially serialized LLM-presentable tool
definitions. The deferred numerator is initially serialized full definitions
plus namespace/server summaries; tools loaded after model execution begins are
reported separately and do not enter the initial-load ratio. Every run and
retry is disclosed; failed required scenarios cannot be averaged away. Latency,
cache behavior, and total tokens remain reported diagnostics, not admission
claims without a separately approved threshold.

Rollout stages:

1. Ship metadata, adapter-rendered searchable-corpus boundaries, receipts, and
   `eager_filtered` only.
2. Complete and test the exhaustive core presentation inventory.
3. Verify candidate Codex and OpenCode tuples in isolation; a binary upgrade is
   a separate reviewed change.
4. Produce a canary-ready compatibility record and rollback runbook.
5. Only after separate operator authorization, set `auto` for one high-surface,
   low-consequence canary and compare task success, discovery misses, retries,
   tokens, latency, and approval outcomes before expanding.

Changing any compatibility-key field invalidates the record. `auto` then uses a
separately verified eager profile/candidate when one exists; otherwise the
tuple is ineligible. Rollback is an operational policy change; it does not
unregister handlers or rewrite session history.

## Integration

- **RFC 0001:** Planning occurs after per-attempt runtime/model resolution and
  before invocation; failover recomputes the plan.
- **RFC 0002:** Registration, module groups, schemas, approval metadata,
  logging, complete canonical listing, and skills remain authoritative. This
  RFC changes only adapter-owned LLM presentation and eager-discovery
  assumptions.
- **RFC 0005:** Bounded receipts extend runtime observability without replacing
  server-side tool spans and capture.
- **RFC 0008:** Generated adapter assets remain ephemeral and restrictive;
  no discovery content or credentials are added to logs.
- **Core tool discovery spec:** `REQ-core-tool-discovery-001` through `009`
  define the observable guarantees.

## Alternatives Considered

### Enable native features directly in each adapter

Rejected as the system contract. It makes one CLI's flags and event shapes the
architecture. Native features remain optional renderers of a shared plan.

### Wait indefinitely for a FastMCP request-aware pagination seam

Rejected on 2026-08-31. The pinned release/source/search evidence in
`reviews/0027/round-4-option-b.md` found no public commitment as of that date.
A future public hook may be considered through a later amendment, but it is not
the delivery critical path.

### Override private FastMCP handlers or rewrite MCP frames

Rejected. Private list-handler overrides, monkeypatches, duplicate filtered
servers, proxies, and JSON-RPC/SSE rewriting create an unstable second protocol
boundary. Adapters use only public host filtering and canonical MCP calls.

### Expose `search_tools` and `invoke_tool` as universal meta-tools

Rejected for v1. It collapses typed schemas into a generic payload, obscures
attribution, complicates approvals, and creates a second dispatch path.

### Add fleet-wide MCP caller authentication in this change

Rejected as a separate motif and doctrine/transport change. It requires a
credential lifecycle and coordinated migration of every Switchboard,
connector, dashboard, scheduler, recovery, and runtime client. This RFC remains
honest that LLM omission is not a server authorization boundary.

### Select task tools by semantically classifying the prompt in the daemon

Rejected. It moves reasoning into deterministic infrastructure and can silently
omit a necessary capability.

### Remove group pruning after adding Tool Search

Rejected. Groups encode ownership and role fit, not only token cost.

### Enable code mode for read-only tools

Deferred. Current CLI processes have broader filesystem, shell, configuration,
or network authority than a nested tool catalog. A separate isolation contract
must prove the nested program cannot escape its narrower capability.

## V1 Scope

V1 includes:

- catalog metadata and an exhaustive registered-tool LLM-presentation classification before native Tool Search admission,
- a stable adapter-owned searchable corpus bound to each attempt while
  canonical FastMCP `tools/list` remains complete,
- verified runtime-native search, result selection, and on-demand loading of
  original typed MCP definitions,
- `eager_filtered` and `auto` DB-backed operational policy,
- `none`, `eager_filtered`, and `native_deferred` plan modes,
- per-attempt capability negotiation and exact-tuple compatibility records,
- conformance and representative-runtime evaluation gates,
- adapter preparation on all registered tool-capable runtimes,
- content-blind receipts and eager rollback,
- at least one canary-ready verified native compatibility record and rollback runbook; live canary execution remains separately authorized.

V1 does not include:

- server-wide MCP caller authentication or per-session call authorization,
- a generic search/invoke gateway,
- automatic group broadening,
- automatic runtime binary upgrades,
- semantic prompt classification in the daemon,
- programmatic/code-mode execution,
- equal context reduction on runtimes that only support eager discovery,
- a new dashboard surface beyond the existing runtime-config control for
  `eager_filtered` versus `auto`.
