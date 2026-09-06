# RFC 0027 Review -- Option B Amendment

**Date:** 2026-08-31
**Decision:** Owner selected Option B in `bu-g5fha`
**Status:** Amendment candidate; implementation and merge remain separately gated

## Trigger

The required FastMCP 3.4.2 public-seam probe proved that transforms can filter
the sequence before native pagination, but the actual request cursor is visible
only inside the private wire handler. Reusing a cursor after the projection
changed was accepted over both streamable HTTP and legacy SSE because the
native cursor contains only an offset.

No public commitment to a request-aware pagination extension was found as of
2026-08-31. The owner therefore rejected an indefinite wait and selected
Option B.

## Pinned Upstream Evidence

- Inspection date: 2026-08-31.
- FastMCP main: [`977ba66c811728aff1522bca48e8cc86eb2aec15`](https://github.com/PrefectHQ/fastmcp/commit/977ba66c811728aff1522bca48e8cc86eb2aec15).
- Latest stable inspected: [v3.4.7](https://github.com/PrefectHQ/fastmcp/releases/tag/v3.4.7).
- Latest prerelease inspected: [v4.0.0b5](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.0.0b5).
- Offset-only paginator at the pinned main commit:
  [`fastmcp_slim/fastmcp/utilities/pagination.py`](https://github.com/PrefectHQ/fastmcp/blob/977ba66c811728aff1522bca48e8cc86eb2aec15/fastmcp_slim/fastmcp/utilities/pagination.py).
- Private wire handler at the pinned main commit:
  [`fastmcp_slim/fastmcp/server/mixins/mcp_operations.py`](https://github.com/PrefectHQ/fastmcp/blob/977ba66c811728aff1522bca48e8cc86eb2aec15/fastmcp_slim/fastmcp/server/mixins/mcp_operations.py#L132-L149).
- Public searches inspected:
  [open cursor issues](https://github.com/PrefectHQ/fastmcp/issues?q=is%3Aissue%20state%3Aopen%20pagination%20cursor),
  [open pagination pull requests](https://github.com/PrefectHQ/fastmcp/pulls?q=is%3Apr%20state%3Aopen%20pagination%20cursor), and
  [milestones](https://github.com/PrefectHQ/fastmcp/milestones?state=open).

This evidence is time-bounded. A later supported public seam may justify a new
amendment; it is not assumed unavailable forever.

## Selected Boundary

- Runtime-native Tool Search and on-demand full-schema loading are the primary
  product behavior and source of material context reduction.
- FastMCP remains the complete canonical registry and complete HTTP/SSE
  `tools/list` source.
- The daemon finalizes immutable post-approval definitions and digests, never
  handler callables.
- Each spawner attempt creates a plan-digest-bound canonical-name search corpus.
- The runtime adapter renders that corpus through supported public host
  configuration and configures native search/load only for an exact verified
  tuple. The allowlist is the corpus boundary, not the token-saving mechanism.
- Opaque runtime-host MCP cursors remain invocation-local transport state. The
  amendment does not falsely claim that Butlers can inspect or validate them.
- A tuple that cannot prove hidden definitions stay out of model-visible input
  is ineligible. The complete MCP list is not a presentation fallback.
- Direct calls continue through canonical names, schemas, wrappers, approvals,
  module-state checks, attribution, tracing, and tool-call capture.

## Candidate Host Mechanisms

The amendment records these as compatibility candidates, not admission proof:

| Host | Installed version | Public filtering candidate |
|---|---:|---|
| Codex | 0.151.0 | `mcp_servers.<id>.enabled_tools` |
| OpenCode | 1.2.27 | Ordered root `tools` deny-server then exact-name allow entries |
| Claude Code | 2.1.251 | Exact complement `--disallowedTools` MCP FQNs with `--strict-mcp-config` |
| Gemini CLI | 0.28.2 | Per-server `includeTools` in invocation-local settings |

Conformance must distinguish model availability from call permission. A
permission-only control does not satisfy corpus filtering. For native tuples it
must also prove intended-tool recall within a declared result limit, corpus-only
results, hidden-result exclusion, miss refinement, on-demand typed definition
loading, and initial schema-byte savings. Extra eligible matches and precision
remain diagnostics unless a threshold is approved.

## Eager Policy Refinement

The existing `eager_filtered` policy remains strict. A host that mandates
native deferral for an exact tuple cannot satisfy this policy and must be
skipped for an eager-capable candidate. The amendment does not silently change
the owner control to mean "filtered but possibly native."

## Rejected Paths

- Indefinite waiting without an upstream commitment.
- Private FastMCP handler override or monkeypatch.
- A second filtered MCP server.
- JSON-RPC/SSE frame rewriting, proxying, or a generic search/invoke gateway.
- Caller-authentication expansion, Code Mode, or automatic binary upgrades.

## Authority

The owner decision authorizes this RFC/OpenSpec/Beads amendment. It does not
authorize implementation, dependency upgrades, amendment merge, provider
evaluation, policy activation, deployment, or live canary execution.
