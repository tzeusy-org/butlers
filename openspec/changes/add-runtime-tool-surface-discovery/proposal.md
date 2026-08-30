## Why

[Observed] Butlers' live development fleet currently exposes 1,133 MCP tools
across 12 butlers (94.4 per butler on average), while recent sessions use only a
small fraction of each advertised surface. The existing registration-time
`core_groups` and module-group filters preserve domain ownership, but they cannot
hide infrastructure-only handlers from LLM discovery or take advantage of modern
runtime-native deferred loading without making one CLI the system's design
centre.

[Inferred] Butlers needs a runtime-neutral tool exposure contract that keeps MCP
as the executable authority, separates callable tools from model-visible tools,
and treats native deferred Tool Search as a negotiated presentation optimization
with a conservative cross-CLI fallback.

## What Changes

- Introduce a canonical, metadata-backed tool catalog derived from the handlers
  already registered on each butler's FastMCP server. The catalog distinguishes
  the executable set, LLM-eligible set, discoverable set, and initially
  loaded set without broadening existing butler, group, or module ownership.
- Classify tools by LLM visibility and load posture so runtime sessions do not
  discover infrastructure-only tools, while Switchboard, connector,
  scheduler, dashboard, and recovery callers retain their existing endpoints.
- Build a per-invocation tool-surface plan after runtime/model resolution. The
  plan selects `none`, `eager_filtered`, or `native_deferred` from
  the intersection of trigger policy, configured exposure policy, verified CLI
  capabilities, and provider/model support.
- Add runtime capability probing and invocation preparation as explicit adapter
  responsibilities. Unsupported, unknown, or failed discovery capabilities fall
  back to `eager_filtered` before launch or only under the explicit complete-
  evidence zero-effect replay predicate; healing and QA isolation continues to
  select `none`.
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
- Add a credential-free cross-runtime conformance harness and measured rollout
  gates before enabling native discovery for any production runtime tuple.

## Capabilities

### New Capabilities

- `core-tool-discovery`: Defines LLM-facing MCP tool visibility, per-session
  exposure planning, runtime capability negotiation, safe fallback, skill/tool
  authority separation, and content-blind discovery evidence.

### Modified Capabilities

- `core-daemon`: Replace the explicitly deferred single-tier LLM-presentation
  contract with registered-but-LLM-hidden support while preserving the
  complete `core_groups`, type/name gating, and `route.execute` registration
  behavior.
- `core-modules`: Add the module-author contract for exposure metadata without
  changing existing argument-sensitivity or group-registration semantics.
- `core-spawner`: Add the LLM-presentation marker and require the tool-surface
  plan to be rebuilt for each runtime/model attempt while preserving one-butler
  MCP isolation.
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
  and Gemini invocation/configuration paths.
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
