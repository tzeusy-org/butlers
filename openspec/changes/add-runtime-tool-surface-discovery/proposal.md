## Why

[Observed] Butlers' live development fleet currently exposes 1,133 MCP tools
across 12 butlers (94.4 per butler on average), while recent sessions use only a
small fraction of each advertised surface. The existing registration-time
`core_groups` and module-group filters preserve domain ownership, but they cannot
hide infrastructure-only handlers from LLM discovery or take advantage of modern
runtime-native deferred loading without making one CLI the system's design
centre.

[Inferred] Butlers needs a runtime-neutral Tool Search contract that keeps MCP
as the executable authority, searches a bounded corpus, and loads full typed
schemas only when needed. Runtime-specific native search is the context-saving
mechanism; eager filtering is the conservative cross-CLI compatibility path.

## What Changes

- Introduce a canonical, metadata-backed tool catalog derived from the handlers
  already registered on each butler's FastMCP server. The catalog distinguishes
  the executable set, LLM-eligible set, discoverable set, and initially
  loaded set without broadening existing butler, group, or module ownership.
- Classify tools by LLM visibility and load posture. Canonical FastMCP
  `tools/list` remains complete for every caller; each runtime adapter renders
  a plan-bound search corpus and, for verified native tuples, a small initial
  summary plus on-demand full-schema loading. The allowlist bounds the corpus;
  it is not the token-saving mechanism. Switchboard, connector, scheduler,
  dashboard, and recovery callers retain their existing endpoints.
- Build a per-invocation tool-surface plan after runtime/model resolution. The
  plan selects `none`, `eager_filtered`, or `native_deferred` from
  the intersection of trigger policy, configured exposure policy, verified CLI
  capabilities, and provider/model support.
- Add runtime capability probing, search-corpus rendering, native search/load
  preparation, and result parsing as explicit adapter responsibilities.
  Unsupported or unknown model-presentation filters make a tuple ineligible
  rather than exposing the complete list. Verified eager or native failures
  follow the explicit complete-evidence zero-effect replay predicate; healing
  and QA isolation continues to select `none`.
- Preserve call-time module-state checks, existing handler authorization, approval gates,
  schema validation, telemetry, and canonical tool-call attribution regardless
  of how a tool was discovered or loaded.
- Record content-blind discovery receipts: selected mode, runtime/version/model,
  eligible and initially exposed counts, loaded count, fallback reason, and
  discovery outcome. Raw prompts, search queries, tool arguments, tool results,
  and credentials are excluded from the new receipt.
- Keep `.agents/skills/` as the canonical skill source and make adapter-specific
  skill projection explicit. Loading a skill remains guidance-only and cannot
  grant tool authority or widen the session's tool-surface plan.
- Add a credential-free cross-runtime conformance harness that measures
  intended-tool recall within a declared result limit, corpus-only results,
  hidden-result exclusion, miss refinement, typed loading, and initial schema
  bytes before admitting native search for any runtime tuple. Extra eligible
  matches and precision remain diagnostics unless a threshold is approved.

## Capabilities

### New Capabilities

- `core-tool-discovery`: Defines bounded native MCP Tool Search, per-session
  corpus planning, on-demand typed schema loading, runtime capability
  negotiation, safe fallback, skill/tool authority separation, and content-blind
  discovery evidence.

### Modified Capabilities

- `core-daemon`: Replace the explicitly deferred single-tier LLM-presentation
  contract with registered-but-LLM-hidden support while preserving the
  complete `core_groups`, type/name gating, and `route.execute` registration
  behavior.
- `core-modules`: Add the module-author contract for exposure metadata without
  changing existing argument-sensitivity or group-registration semantics.
- `core-spawner`: Require an immutable adapter presentation asset to be rebuilt
  for each runtime/model attempt while preserving one-butler MCP isolation and
  the complete canonical MCP endpoint. No LLM URL marker is added.
- `runtime-config-table`: Persist the conservative/automatic tool exposure
  policy as DB-backed per-butler operational tuning.
- `runtime-config-api`: Read, validate, and update the exposure policy as a hot
  runtime field.
- `runtime-config-dashboard-ui`: Let the owner choose the exposure policy and
  see that the change applies to subsequent sessions without a daemon restart.
- `session-process-logs`: Retain one bounded, content-blind presentation receipt
  per runtime attempt under the existing process-log TTL.

## Impact

- Core runtime and MCP surfaces: `src/butlers/daemon.py`,
  `src/butlers/mcp_wrappers.py`, `src/butlers/guards.py`,
  `src/butlers/core/spawner.py`, and `src/butlers/core/runtime_config.py`.
- Runtime adapters: the base adapter contract plus Codex, OpenCode, Claude Code,
  and Gemini invocation/configuration paths, including exact public host-filter
  dialects and canonical-to-host name mappings.
- Module contracts: `ToolMeta` gains LLM-visibility and load-posture metadata;
  existing argument-sensitivity metadata and approval behavior remain intact.
- Operations and observability: runtime configuration/API projection, session
  process logs, metrics, and a no-content conformance/evaluation report.
- Design contracts and documentation: new RFC 0027, cross-references from RFC
  0002 and the MCP/runtime concepts documentation.
- No new third-party dependency is proposed. No CLI binary is upgraded or
  native discovery mode enabled merely by accepting this design.

## Explicit Non-Goals

- Removing per-butler manifesto, module, or tool-group boundaries.
- Exposing every fleet tool to every butler or runtime session.
- Replacing typed MCP calls with a generic `search_tools` plus `invoke_tool`
  gateway.
- Overriding private FastMCP handlers, running a second filtered MCP server,
  proxying/re-writing JSON-RPC or SSE frames, or treating opaque host cursors as
  Butlers-owned plan state.
- Treating tool discovery, skill loading, descriptions, or MCP annotations as
  authorization.
- Adding fleet-wide MCP caller authentication or claiming that LLM-hidden
  presentation is a server security boundary; that is a separate doctrine and
  transport change.
- Automatically upgrading Codex, OpenCode, Claude Code, or Gemini binaries.
- Enabling programmatic/code-mode execution in v1; it remains reserved until a
  separate isolation design proves nested code cannot reach broader authority.

## Owner Sign-off

- **Status:** Approved
- **Approved by:** owner
- **Date:** 2026-08-30
- **Approved artifact:** commit `3448804aaff7b163b4b81deb646db2c9f4ae1397`
- **Approval scope:** proposal, RFC 0027 decisions, v1 non-goals, and the
  `eager_filtered`/`auto` operational policy
- **Authority:** clears the specification gate and permits handoff for
  sequencing; it does not itself request implementation, merge, deployment,
  runtime binary upgrades, or live canary activation

## Owner Amendment — Option B

- **Status:** Selected; amendment integration pending
- **Selected by:** owner
- **Date:** 2026-08-31
- **Decision bead:** `bu-g5fha`
- **Decision:** Keep canonical FastMCP listing complete and make each adapter
  render a fresh per-attempt search corpus through public runtime-host
  configuration. Verified native tuples search that corpus and load typed
  schemas on demand; the allowlist bounds eligibility but is not the
  context-saving mechanism.
- **Reason:** No supported FastMCP request-aware pagination seam or committed
  upstream roadmap justifies holding delivery. Private hooks, duplicate
  servers, proxies, and wire rewriting remain rejected.
- **Cursor refinement:** Adapters do not own opaque host MCP cursors.
  Host-internal pagination is invocation-local and non-model-visible;
  conformance proves hidden definitions never enter model context/search.
- **Strict policy:** A host that mandates native deferral cannot satisfy
  `eager_filtered` and is ineligible under that policy.
- **Doctrine amendment status:** Pending exact-artifact owner approval. On an
  owner-approved merge, `about/heart-and-soul/architecture.md` makes the 30-50
  target and initial-context cost refer to definitions initially loaded per
  session rather than every canonical registered handler. The prior Option B
  selection did not itself adopt this Heart-and-Soul amendment.
- **Authority:** authorizes RFC/OpenSpec/Beads amendment only. Implementation,
  dependency upgrades, amendment merge, provider evaluation, policy activation,
  deployment, and canary retain separate gates.

## Exact Artifact Approval History

- `f95052bc1ae721b11fc19f1aed8ac9e58e5c318d`: **Approved by owner on
  2026-09-05.** Superseded as the approval artifact by the corrective successor
  that completes the exact current core-tool registration inventory; its
  accepted Option B and doctrine decisions remain historical provenance. Its
  hosted CI run `33952958265` is also historical and non-transferable.
- `b7ff6c2bf064742907ac14db7e6a5f8e7c4df407`: **Approved by owner.**
  Superseded as the approval artifact after live `main` added
  `conversation_recall` and `conversation_thread_read`; its accepted Option B,
  doctrine, and earlier inventory corrections remain historical provenance.
  Hosted CI run `33962391110` and the independent artifact ACCEPT applied only
  to that head and are superseded.
- `99ecce950314ac597802ed42fca81c7fdb04c65a`: **Not owner-approved as an
  exact artifact.** Hosted CI run `33978679113` and the independent alignment
  GO applied only to that head. PR #4045 subsequently added the `graph` group
  to the live runtime-config vocabulary after the branch had already gained
  the seven-tool `fleet_cases` group, so that review and CI are superseded and
  non-transferable.
- `263d9a6c211fbf0a501cda68ef22f296dc73b2c7`: **Not owner-approved as an
  exact artifact.** Hosted CI run `34028358724` succeeded, but the independent
  exact-head review returned NO-GO on runtime-seed, transport, wrapper, retry,
  and legacy-catalog current-truth drift. That review and CI apply only to this
  superseded head and do not transfer to its correction.
- `d3423639661a4cdd348b4831e909c165f1f9bce1`: **Not owner-approved as an
  exact artifact.** Hosted CI run `34031392691` succeeded and the correction
  resolved the recorded current-truth findings. It is nevertheless superseded
  because the separately reviewed catalog-cleanup prerequisite had not yet
  been integrated into that tree; neither its CI nor any review transfers.
- Post-cleanup integrated successor: **Pending fresh exact-head owner approval.**
  The approval must cite the immutable PR head presented in the final review
  packet. Every artifact-specific approval, review, and CI result at or before
  `d3423639661a4cdd348b4831e909c165f1f9bce1` is explicitly superseded; none
  transfers to the successor. Any later edit likewise creates another artifact
  and invalidates exact-head evidence.
- **Cleanup prerequisite satisfied:** `bu-hxirg` landed through PR #4048 as
  `13d269b1ef80a5815a6ef2567b139398940196a0`. PR #3952 integrated that exact
  `main` through merge commit
  `364585ccf7ffef82b04b64172e5d71d8cdec1576`, whose parents are
  `d3423639661a4cdd348b4831e909c165f1f9bce1` and
  `13d269b1ef80a5815a6ef2567b139398940196a0`. Final review and owner approval
  SHALL bind the later immutable PR head containing this integration and SHALL
  be repeated if `main` or the PR head moves again.
