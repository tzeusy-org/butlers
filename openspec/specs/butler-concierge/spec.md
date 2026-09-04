# Concierge Staffer

## Purpose

Defines the Concierge staffer — a read-only, staffer-typed infrastructure
agent (`type = "staffer"` at `roster/concierge/`) that answers system-plane
questions about the butler fleet itself: fleet status, spend, and session
telemetry. It exists so the dashboard chat's question lane (bu-0ynlk.2) can
answer operational questions from typed read models instead of either
fabricating an answer or misrouting an operational question to a domain
butler with no authority over it. Concierge owns no write tools and never
answers a domain question (finances, health, relationships, etc.) — those
stay with their owning domain butler.

## Requirements

### Requirement: Concierge Staffer Identity

Concierge SHALL be a staffer-typed agent in the roster at `roster/concierge/`
with `type = "staffer"` in its `butler.toml`. It is excluded from
user-message routing and daily briefing contributions per the staffer
archetype contract (`openspec/specs/staffer-archetype/spec.md`).

#### Scenario: Roster configuration

- **WHEN** Concierge's `butler.toml` is loaded
- **THEN** `config.type` is `ButlerType.STAFFER`
- **AND** `config.name` is `"concierge"`
- **AND** `config.db_schema` is `"concierge"`
- **AND** `config.permissions.cross_butler_access` is `["*"]`

#### Scenario: Staffer behaviors apply automatically

- **WHEN** the Concierge daemon starts
- **THEN** it is excluded from switchboard user-message classification
- **AND** it does not register daily briefing contribution schedules
- **AND** it registers with the switchboard for reachability (butler-to-staffer
  tool routing from the dashboard chat's question lane)

### Requirement: Infrastructure Contract (MANIFESTO.md)

Concierge's `MANIFESTO.md` SHALL define an infrastructure contract:
responsibilities, non-responsibilities, SLAs, permissions model, failure
modes, and dependency graph, per the staffer archetype's infrastructure
contract convention.

#### Scenario: Contract covers Concierge responsibilities and boundaries

- **WHEN** Concierge's `MANIFESTO.md` is authored
- **THEN** it defines responsibilities scoped to fleet status, session reads,
  spend reads, and source attribution
- **AND** it explicitly states non-responsibilities: no write tools, no
  domain-question answering, no direct cross-schema access outside the two
  sanctioned RFC 0030 views
- **AND** it specifies failure modes: a per-butler fan-out gap surfaced via a
  degraded-source marker, a missing/ungranted view failing loudly at
  `on_startup`, and unpriced spend usage being reported explicitly rather
  than fabricated as zero

### Requirement: Read-Only Tool Surface

Concierge SHALL expose only read-only MCP tools. No tool in the `dashboard_read`
module (or any other module Concierge loads) may perform an INSERT, UPDATE,
or DELETE against any table, or trigger a mutation on another butler
(no Operator-style fleet controls).

#### Scenario: No write tools registered

- **WHEN** Concierge's MCP tool surface is enumerated
- **THEN** every tool in the `dashboard_read` module is a pure read (SELECT
  only) against `concierge.v_fleet_sessions`, `concierge.v_fleet_spend`, or
  `public.insight_candidates`
- **AND** no tool accepts a mutation-shaped argument (create/update/delete/
  trigger) for any resource outside Concierge's own core session log

### Requirement: Tool Surface Budget

Concierge's total MCP tool count (core tools plus every enabled module's
tools) SHALL stay within the repo-wide 30-50 tool budget (RFC 0002 auditing).

#### Scenario: Tool count assertion

- **WHEN** a roster integration test boots Concierge with the `dashboard_read`
  module enabled
- **THEN** the total registered tool count is between 30 and 50 inclusive
- **AND** every `dashboard_read_*` tool's docstring is non-empty (docstring
  completeness for this bead's own tool surface; pre-existing gaps in shared
  core tools are tracked separately, not by this assertion)

### Requirement: System-Plane Scope Boundary

Concierge SHALL answer only system-plane questions (fleet status, spend,
sessions, operational telemetry). A question about a user's own domain data
(finances, health, relationships, calendar, etc.) is out of scope for
Concierge regardless of surface-level phrasing similarity (e.g. "how much did
I spend" vs "how much did the fleet spend").

#### Scenario: Domain questions stay with domain butlers

- **WHEN** the dashboard chat's question lane classifies a question as
  domain-scoped (e.g. "how much did I spend on groceries")
- **THEN** it is NOT routed to Concierge
- **AND** Concierge's own `AGENTS.md` instructs the runtime LLM to decline
  and note the domain-butler boundary if such a question reaches it anyway

#### Scenario: System-plane questions route to Concierge via the catalog

- **WHEN** `resolve_target_via_catalog('how much did the fleet spend
  yesterday')` runs after the `public.memory_catalog` seed (RFC 0030) has
  been applied
- **THEN** the top hit's `source_butler` is `"concierge"`

### Requirement: Every Result Carries a Source Envelope

Every `dashboard_read` tool result SHALL include a `source` object with
`kind`, `ref`, and `as_of` fields, naming exactly what was read and when.

#### Scenario: Source envelope present on every tool result

- **WHEN** any `dashboard_read_*` tool is called
- **THEN** the returned dict contains `source.kind` (e.g. `"view"` or
  `"table"`), `source.ref` (the fully-qualified object read, e.g.
  `"concierge.v_fleet_sessions"`), and `source.as_of` (an ISO-8601 timestamp)

### Requirement: Cross-Schema Reads Only Through Sanctioned Views

Concierge's `butler_concierge_rw` runtime role SHALL hold NO grant of any
kind on any other butler's schema or tables. Its only cross-butler read
surface is `SELECT` on `concierge.v_fleet_sessions` and
`concierge.v_fleet_spend`, which resolve using the view owner's privileges
(the migration user, per RFC 0030's access-model note) rather than any
privilege of `butler_concierge_rw` itself. No tool or module code may issue
a query against another butler's schema directly.

#### Scenario: DB security enforcement

- **WHEN** a test connects `SET ROLE butler_concierge_rw` and attempts
  `SELECT * FROM <other_butler>.sessions` directly
- **THEN** the query is denied (insufficient privilege) — `butler_concierge_rw`
  holds no grant on that table at all
- **AND** the same role's `SELECT * FROM concierge.v_fleet_sessions` succeeds,
  because the view executes with its owner's privileges, not the querying
  role's
- **AND** the returned rows contain only the RFC 0030 column allowlist (no
  `prompt`/`result`/`tool_calls`/`cost` column present in the view's column
  set)

## Non-Goals

- Any write tool or Operator-style fleet control (out of scope; the existing
  `/api/butlers/*` admin surface remains the only mutation path).
- Domain-question answering (stays with domain butlers).
- The dashboard chat answer lane itself (bu-0ynlk.2) and the fast lane
  (bu-0ynlk.6) — Concierge is a tool provider those lanes call, not the lane
  implementation.
- Page-context resolution beyond a stub (bu-0ynlk.4).
