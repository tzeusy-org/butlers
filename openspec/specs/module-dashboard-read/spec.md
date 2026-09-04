# dashboard_read Module

## Purpose

Defines the `dashboard_read` module (`src/butlers/modules/dashboard_read/`),
which wires the Concierge staffer's read-only fleet-telemetry MCP tools onto
its FastMCP server. The module reads exclusively through the two RFC 0030
UNION views (`concierge.v_fleet_sessions`, `concierge.v_fleet_spend`) and the
plain public `insight_candidates` table, wrapping every result in a source
envelope so a downstream answer can cite exactly what was read and when.

## Requirements

### Requirement: Module Registration

`DashboardReadModule` SHALL implement the `Module` ABC
(`src/butlers/modules/base.py`) and be auto-discovered by
`butlers.modules.registry.default_registry()` as a standard framework
module (not a roster-scoped one), matching every other built-in module
(`memory`, `calendar`, `contacts`, ...).

#### Scenario: Auto-discovery

- **WHEN** `default_registry()` walks `butlers.modules` sub-packages
- **THEN** `DashboardReadModule` (module name `"dashboard_read"`) is
  discovered and registered without any explicit import list entry
- **AND** it is enabled only for butlers that declare `[modules.dashboard_read]`
  in their `butler.toml` — currently only `roster/concierge/butler.toml`

### Requirement: Startup View Availability Check

`DashboardReadModule.on_startup` SHALL verify both RFC 0030 views are
queryable before the module is considered ready, failing loudly (raising)
rather than degrading to a silent empty-result state.

#### Scenario: Missing view fails startup loudly

- **WHEN** `concierge.v_fleet_sessions` or `concierge.v_fleet_spend` does not
  exist (e.g. the `concierge` migration chain has not been applied) or the
  `SELECT` grant to `butler_concierge_rw` has been revoked
- **THEN** `on_startup` raises `RequiredViewMissingError`, naming the exact
  missing view, instead of registering tools that would silently return
  empty results

### Requirement: Every Tool Wraps a Query Function With a Source Envelope

Every MCP tool registered by this module SHALL delegate its query to a
function in `queries.py` and attach a `source: {kind, ref, as_of}` envelope
to its result — never ad-hoc inline SQL inside a tool closure, and never a
bare payload with no provenance.

#### Scenario: Tool bodies delegate to queries.py

- **WHEN** any `dashboard_read_*` tool closure in `tools.py` is inspected
- **THEN** its body calls exactly one function from `queries.py` (plus
  trivial argument clamping/validation) and returns that function's result
  merged with a `source` envelope from `queries.source_envelope()`

#### Scenario: Degraded read never presents as empty-and-healthy

- **WHEN** a query against `concierge.v_fleet_sessions` or
  `concierge.v_fleet_spend` raises (e.g. a transient connection fault)
- **THEN** the tool call raises rather than returning a fabricated empty
  result, per the repo's degraded-envelope doctrine
  (`docs/api_and_protocols/response-conventions.md`) — a downed source is
  never presented as a truthful "nothing to report"

### Requirement: Spend Cost Is Computed in Python, Never in SQL

Dollar/cent cost figures SHALL be computed by the module's Python tool layer
from raw token counts and model name via `butlers.core.pricing`
(`estimate_session_cost`), never stored or computed inside a SQL view or
query, and never fabricated as a confident number when a model's rate is
unknown to `pricing.toml`.

#### Scenario: Unpriced usage is reported explicitly

- **WHEN** `dashboard_read_spend_summary` aggregates a window that includes
  sessions using a model absent from `pricing.toml`
- **THEN** those sessions' tokens are reported under
  `unpriced_input_tokens`/`unpriced_output_tokens`
- **AND** `total_cost_cents` reflects only the priced portion, or is `null`
  when no session in the window has a priced model — never a fabricated
  `0` standing in for "unknown"

### Requirement: Tool Docstrings Are Non-Empty and LLM-Explainable

Every tool registered by this module SHALL have a non-empty docstring
describing its purpose, parameters, and any notable scope boundary (e.g.
"this is not a text search over prompts").

#### Scenario: Docstring completeness assertion

- **WHEN** a roster integration test enumerates every tool this module
  registers
- **THEN** each tool's docstring is non-empty

## Non-Goals

- Any write tool (this module registers reads only; see
  `openspec/specs/butler-concierge/spec.md`'s read-only tool surface
  requirement).
- Fanning out live MCP calls to other butlers — every cross-butler read goes
  through the two RFC 0030 views, never a Switchboard tool call to another
  butler.
- Full parity with every dashboard API route's response shape (e.g. the
  `/api/butlers/board` endpoint's live MCP-ping-derived `activity`/`cell_tone`
  fields, which require MCP connectivity this module deliberately does not
  have) — `dashboard_read_fleet_status` is a DB-only rollup, not a
  replacement for that endpoint.
