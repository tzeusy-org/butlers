# RFC 0027: Runtime Tool Surface Discovery and Exposure

**Status:** Accepted
**Date:** 2026-08-30
**Baseline:** `ba1c55c31056af5eaa362a254193bbf43741e86a`
**Related:** RFC 0001, RFC 0002, RFC 0005, RFC 0008

**Owner approval:** 2026-08-30, artifact
`3448804aaff7b163b4b81deb646db2c9f4ae1397`. Approval adopts this design and
permits sequencing; it does not request implementation, merge, deployment,
runtime binary upgrades, or live canary activation.

## Summary

This RFC lets each butler present an LLM-oriented projection of
its canonical MCP tool registry. The projection omits infrastructure-only
definitions and lets verified runtime hosts defer full schemas until the model
searches for them. Runtimes without verified deferred discovery use an eager
projection of the same LLM-presentable set.

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
The missing abstraction is a runtime-neutral presentation plan over the
registered surface.

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
5. **Native discovery is optional.** Unknown support always yields the eager
   projection, or makes a runtime that cannot use MCP ineligible.
6. **Skills guide but never grant.** Loading guidance cannot change tool
   registration, presentation, or call-time authority.
7. **No replay after effects.** Discovery fallback uses the existing
   pre-side-effect failover boundary.

## Layered Surface Model

For one butler:

| Layer | Definition | Owner |
|---|---|---|
| Registered/callable set | Handlers admitted by startup configuration and available from the canonical MCP registry | Daemon + modules |
| LLM-presentable set | Registered definitions classified for ordinary LLM discovery | Tool catalog metadata |
| Initially loaded set | Presentable definitions or summaries placed in initial model context | Exposure plan + runtime host |
| Loaded set | Additional presentable definitions imported after native search | Runtime host |

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
approval gates have replaced/wrapped their final handlers. The catalog stores
descriptors and canonical names, never a pre-gate callable. All invocation
continues through the final wrapped MCP registry so discovery cannot retain a
bypass reference.

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

## LLM Tool-List Projection

The generated runtime MCP URL carries the existing `runtime_session_id` and
`trigger_source` correlation parameters plus an explicit LLM-presentation
marker. These query values are untrusted presentation/correlation hints, not
authentication claims.

For `tools/list` only, the LLM marker selects the stable LLM-presentable
projection for that butler. Streamable HTTP and legacy SSE session mapping must
produce the same list. A caller without the marker, including existing
Switchboard, connector, scheduler, dashboard, and operational clients, receives
the complete registered list.

The projection is deterministic for the attempt's plan digest: catalog-
generation digest, enabled-module-snapshot digest, exposure policy, and resolved
compatibility-key digest. It does
not mutate in response to search calls, connection history, or another runtime session. Tool
calls continue through the canonical MCP registry and its existing handler
checks. The marker must never be logged as proof of caller identity, and hiding
a tool from the projected list must never be described as preventing a forged
direct protocol call to an otherwise reachable endpoint.

Filtering occurs before pagination. Every opaque cursor is bound to the plan
digest; a cursor from a different or expired projection is rejected as invalid,
and the client must restart listing from the first page. A forged or stale
cursor never falls through to the complete infrastructure list.

If the MCP implementation cannot provide a stable projection without violating
its list/caching contract, adapters may apply an equivalent verified allowlist
before model serialization. Either implementation must yield the same
LLM-presentable names and canonical calls.

## Exposure Planning State Machine

Per-schema operational policy has two owner-facing values:

- `eager_filtered`: expose the full LLM-presentable definitions eagerly.
- `auto`: choose verified native deferred discovery for the resolved tuple;
  otherwise use `eager_filtered`.

Existing rows and missing first-boot configuration default to
`eager_filtered`. Accepting or deploying the schema does not activate native
discovery.

The v1 internal plan modes are:

- `none`: no live butler MCP surface; mandatory for QA and healing.
- `eager_filtered`: full definitions for the LLM-presentable set.
- `native_deferred`: bounded namespace/server summaries plus on-demand loading
  of presentable typed definitions.

Selection is deterministic:

```text
if trigger_source in {qa, healing}: none
else if policy == eager_filtered: eager_filtered
else if classification complete and native_deferred verified for exact tuple: native_deferred
else: eager_filtered
```

A same-tier model failover recomputes the plan for the new runtime, CLI version,
configuration dialect, and model/provider tuple. Plans and provider-native
resume handles never cross adapters. A hot policy update applies to newly
planned attempts; an in-flight attempt keeps its immutable plan.

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
canonical tool-name normalization, skill convention, and discovery-event/
receipt parser support. The normalized configuration digest uses canonical
sorted-key serialization after replacing session IDs, temporary paths, tokens,
credentials, and authorization headers with fixed typed sentinels.

Support is keyed by the exact `CompatibilityKey`,
not a CLI family name or optimistic version prefix. Feature strings or
experimental flags make a tuple eligible for conformance testing, not for
automatic enablement.

Invocation preparation becomes an explicit adapter responsibility while the
existing `build_config_file()` method remains as the compatibility primitive
for adapters that use it. Adapters may compose their current builder, private
writer, or CLI flags; they must not be assumed to share a single file format.
A tool-capable adapter that cannot render the LLM projection and its MCP server
remains on its verified eager path. A runtime that cannot invoke MCP at all is
ineligible for tool-bearing work.

## Native Deferred Contract

The initial model context contains stable namespace/server summaries and any
definitions explicitly classified eager. Search returns only definitions from
the LLM-presentable set and loads complete typed definitions through the host's
native protocol. The eventual call uses the canonical MCP name and input schema.

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

`eager_filtered` exposes the complete LLM-presentable list through ordinary MCP
tool discovery. It preserves canonical names, schemas, parser behavior, and the
current direct-call flow on every supported CLI.

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

`preparation_failed` may render eager assets without replay because no process
started. The two native runtime categories permit at most one eager fallback
only when the effect predicate is conclusively clean:

```text
effect_evidence_complete == true
and canonical_mcp_call_count == 0
and non_mcp_effect_capable_call_count == 0
```

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

## Conformance and Rollout Gate

Every native tuple starts unverified. A checked-in, versioned conformance
manifest defines scenario IDs, expected outcomes, runtime samples, allowed
retries, cache conditions, malformed-schema dispositions, and the admission
report schema. Verification has two lanes:

1. A credential-free synthetic MCP server with at least 100 tools across
   representative namespaces proves eager projection, native search, canonical
   invocation, infrastructure-definition omission, malformed-schema handling,
   fallback, and receipt extraction.
2. An authorized representative runtime evaluation proves task success,
   no-tool completion, approval preservation, call attribution, context cost,
   latency, and cache behavior without logging sensitive content.

A compatibility record is immutable and contains the complete
`CompatibilityKey` plus conformance-manifest digest, fixture digest, result
digest, and verification timestamp. Evidence/result fields are not key fields.
Any `CompatibilityKey` mismatch invalidates the record. Native enablement
requires all mandatory manifest scenarios passing, no new task/approval/
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

1. Ship metadata, LLM projection, receipts, and `eager_filtered` only.
2. Complete and test the exhaustive core presentation inventory.
3. Verify candidate Codex and OpenCode tuples in isolation; a binary upgrade is
   a separate reviewed change.
4. Produce a canary-ready compatibility record and rollback runbook.
5. Only after separate operator authorization, set `auto` for one high-surface,
   low-consequence canary and compare task success, discovery misses, retries,
   tokens, latency, and approval outcomes before expanding.

Changing any compatibility-key field invalidates the record and returns `auto`
to `eager_filtered`. Rollback is an operational policy
change; it does not unregister handlers or rewrite session history.

## Integration

- **RFC 0001:** Planning occurs after per-attempt runtime/model resolution and
  before invocation; failover recomputes the plan.
- **RFC 0002:** Registration, module groups, schemas, approval metadata,
  logging, and skills remain authoritative. This RFC changes only LLM
  presentation and eager-discovery assumptions.
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

- catalog metadata and an exhaustive registered-tool LLM-presentation classification before native enablement,
- a stable LLM-facing `tools/list` projection with unchanged canonical calls,
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
