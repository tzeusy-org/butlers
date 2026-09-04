# RFC 0030: System-Plane Read Exception

**Status:** Accepted
**Date:** 2026-09-04

## Summary

This RFC extends the cross-butler briefing exception mechanism (RFC 0010) to a
second class of deterministic, zero-reasoning data extraction: fleet
operational telemetry. The Concierge staffer (`roster/concierge/`) answers
system-plane dashboard-chat questions — "how much did the fleet spend
yesterday", "which sessions failed today", "what is currently running" — by
reading two column-allowlisted, read-only UNION views
(`concierge.v_fleet_sessions`, `concierge.v_fleet_spend`) instead of issuing
Switchboard MCP fan-out to every domain butler. RFC 0010's five guardrails
apply unchanged; this RFC adds a sixth, specific to reads that could
otherwise leak free-text session content across a schema boundary: a
**column allowlist**, enforced by the view definition itself, not by
application-code discipline.

## Motivation

The dashboard chat's question lane (bu-0ynlk.2) needs to answer questions
about the fleet's own operation — spend, session failures, uptime — without
either fabricating an answer or misrouting an operational question to a
domain butler that has no authority over it (a butler answering "how much did
the fleet spend" would have to either lie about scope or silently narrow the
question to its own schema, both worse than a correctly-scoped "not my
domain").

The architecturally pure alternative is Switchboard fan-out: Concierge
receives the question, issues one MCP request per domain butler asking "how
many sessions did you run and what did they cost", each butler spawns a
session purely to run a deterministic SQL aggregate, and Concierge
synthesizes the results. For a fleet of a dozen butlers, that is up to twelve
LLM sessions spent on work that is, byte for byte, the same class of
zero-reasoning extraction RFC 0010 already carved out an exception for.

Every one of those twelve sessions would do nothing but run
`SELECT count(*), sum(input_tokens), ... FROM sessions WHERE ...` — the exact
shape of query core_055's `v_qa_recent_failures` and RFC 0010's
`v_briefing_contributions` already prove is safe to expose through a
column-scoped, migration-tracked UNION view. Reusing that mechanism for the
Concierge staffer's fleet telemetry costs zero additional LLM sessions per
question instead of up to twelve.

## Design

### Exception Scope

The exception permits the Concierge staffer (and only the Concierge staffer,
via its `butler_concierge_rw` runtime role) to read every other butler's
`sessions` table through two views:

- **What is accessed:** `sessions` rows, projected through the column
  allowlist below. No other table in any other butler's schema is accessible.
- **Direction:** Read-only. Concierge reads from every other butler; no
  butler reads from Concierge, and Concierge never writes to another
  butler's schema.
- **Mechanism:** Two SQL views (`concierge.v_fleet_sessions`,
  `concierge.v_fleet_spend`) in Concierge's own schema, created and granted
  by `roster/concierge/migrations/001_fleet_views.py` — not direct
  cross-schema queries in application code.
- **When:** On-demand, per MCP tool call from the dashboard chat's question
  lane (via Switchboard tool routing to Concierge) — not a scheduled batch
  job. This is the one point of departure from RFC 0010's "batch, not
  real-time" MAY-reuse criterion; see Reuse Criteria below for why it still
  qualifies.

### The Views

```sql
CREATE VIEW concierge.v_fleet_sessions AS
    SELECT
        'health'::text AS source_butler,
        s.id, s.started_at, s.completed_at AS ended_at,
        CASE WHEN s.completed_at IS NULL THEN 'running'
             WHEN s.success IS TRUE THEN 'success'
             WHEN s.success IS FALSE THEN 'failed'
             ELSE 'unknown' END AS status,
        s.trigger_source, s.model, s.input_tokens, s.output_tokens,
        CASE WHEN s.error IS NULL THEN NULL
             WHEN split_part(s.error, ':', 1) ~ '^[A-Za-z_][A-Za-z0-9_.]{0,63}$'
                 THEN split_part(s.error, ':', 1)
             ELSE 'other' END AS error_class
    FROM health.sessions s
    UNION ALL
    -- ... one term per fleet schema (education, finance, general, home,
    -- lifestyle, messenger, qa, relationship, switchboard, travel,
    -- chronicler, concierge) ...
;

CREATE VIEW concierge.v_fleet_spend AS
    SELECT 'health'::text AS source_butler,
        s.id, s.started_at, s.completed_at AS ended_at,
        s.model, s.input_tokens, s.output_tokens
    FROM health.sessions s
    WHERE s.completed_at IS NOT NULL
    UNION ALL
    -- ... one term per fleet schema ...
;
```

Both views live in the `concierge` schema. Each UNION term hardcodes its
`source_butler` string literal, identical to RFC 0010's
`v_briefing_contributions` and core_055's `v_qa_recent_failures` — provenance
is set by the view definition, never derived from row data.

### Sixth Guardrail: Column Allowlist

RFC 0010's exception was scoped to structured JSON contribution envelopes
(`briefing/daily/%` keys) — the shape of what could leak was already bounded
by the writer's own discipline. Fleet session rows are different: `sessions`
carries `prompt`, `result`, and `tool_calls` — arbitrary user- and
agent-authored free text, potentially sensitive, that must never cross a
schema boundary just because a question happened to be about spend or
failures.

**The column allowlist is enforced by the view definition, not by
application-code discipline.** Only these columns are ever selected into
either view:

| Column | Type | Source |
|---|---|---|
| `source_butler` | TEXT | Hardcoded literal per UNION term |
| `id` | UUID | `sessions.id` |
| `started_at` | TIMESTAMPTZ | `sessions.started_at` |
| `ended_at` | TIMESTAMPTZ | `sessions.completed_at` |
| `status` | TEXT | Derived: `'running'` \| `'success'` \| `'failed'` \| `'unknown'` |
| `trigger_source` | TEXT | `sessions.trigger_source` |
| `model` | TEXT | `sessions.model` |
| `input_tokens` | INTEGER | `sessions.input_tokens` |
| `output_tokens` | INTEGER | `sessions.output_tokens` |
| `error_class` | TEXT | Derived short classifier (see below) — **never** `sessions.error` |

`sessions.prompt`, `sessions.result`, `sessions.tool_calls`, and
`sessions.cost` are **never** selected into either view. `cost_cents` (the
dollar figure the dashboard chat surfaces) is **not** a view column at all —
dollar cost is computed downstream by the `dashboard_read` module's Python
tool layer from `input_tokens`/`output_tokens`/`model` via
`pricing.toml` (`estimate_session_cost`), exactly as the existing
`/api/spend/*` routes already compute it. This repo has no canonical
dollar-cost column on `sessions` anywhere; the view does not invent one.

`error_class` is the one column that requires a real content filter, not just
a column omission: `sessions.error` can contain a full exception message,
which may itself embed sensitive data. The view only lets through a value
matching `^[A-Za-z_][A-Za-z0-9_.]{0,63}$` (i.e. something shaped like a bare
Python exception class name, such as `ValueError` or
`asyncpg.PostgresError`) extracted from the text before the first `:`.
Anything else — a message with no colon-prefixed classname, or one longer
than 64 characters — collapses to the literal string `'other'`. No free-text
error content can pass through this filter; the worst case is an
uninformative `'other'` bucket, never a content leak.

### Data Flow

```
dashboard chat question lane (bu-0ynlk.2)
  -> Switchboard tool routing -> concierge.dashboard_read_* MCP tool
     -> SELECT ... FROM concierge.v_fleet_sessions | v_fleet_spend
        (single query against Concierge's own schema; no MCP fan-out,
         no LLM session spent on any other butler)
     -> dashboard_read module computes cost_cents (Python, pricing.toml)
        and wraps the result in a source={kind, ref, as_of} envelope
  <- tool result returned to the question-lane session
```

### Guardrails (RFC 0010's five, plus the sixth above)

**1. Read-only SQL view.** Both are UNION views; PostgreSQL structurally
rejects INSERT/UPDATE/DELETE on them.

**2. Explicit source attribution.** `source_butler` is a hardcoded literal
per UNION term.

**3. Filtered.** `v_fleet_spend` is additionally filtered to
`completed_at IS NOT NULL` (a running session's token counts are not yet
final); callers apply their own date-range predicate on top of either view.

**4. Health-check validated.** `DashboardReadModule.on_startup` calls
`queries.ensure_views_available()`, which probes both views via
`to_regclass` and raises loudly if either is missing or ungranted — the
module refuses to start silently degraded, and any tool that runs before a
view exists names the exact missing view in its error rather than returning
a quiet empty result.

**5. Migration-based grants — scoped to the views only.** The only grant
issued is `SELECT` on the two views themselves, to `butler_concierge_rw`, in
`roster/concierge/migrations/001_fleet_views.py` — a version-controlled,
reversible Alembic migration, not ad-hoc SQL. `butler_concierge_rw` receives
**no** grant on any other butler's underlying `sessions` table: PostgreSQL
views execute with their owner's privileges by default (no
`security_invoker` option is set here), and the migration user that creates
the views already owns every table in every schema. This is what makes
"direct cross-schema `SELECT` is denied, but the view succeeds" a real,
database-enforced property rather than a path this role simply chooses not
to use — see the DB security test in
`roster/concierge/tests/test_dashboard_read.py`.

**6. Column allowlist (new).** Enumerated above; enforced structurally by
the view's `SELECT` list, not by a code-review convention. A DB-level
security test (`roster/concierge/tests/test_dashboard_read.py`) asserts the
view's column set contains no `prompt`/`result`/`tool_calls`/`cost` column.

## Reuse Criteria

RFC 0010's MAY/MUST-NOT criteria are restated here with the one deliberate
departure called out.

### MAY Be Reused When ALL of These Hold

1. **Read-only**, enforced at the database level (a view or restricted role).
2. **Deterministic** — the code that runs the cross-schema read is pure SQL
   plus pure Python token→dollar arithmetic, never LLM reasoning.
3. **Column-allowlisted at the view** whenever the source table can carry
   free-text or otherwise sensitive content the exception was not asked to
   expose (RFC 0030 addition; RFC 0010 did not need this because its source
   was already a bounded JSON contribution envelope).
4. **Auditable** — migration-tracked views and grants with explicit source
   attribution, never embedded in application code.
5. **Cost-justified** — the compliant alternative (Switchboard fan-out) would
   cost materially more LLM sessions for zero-reasoning work.

### Departure from RFC 0010's "Batch, Not Real-Time" Criterion

RFC 0010 restricts its pattern to pre-scheduled batch aggregation and
explicitly lists "real-time queries" under MUST NOT: *"If the data is needed
on-demand during an LLM session ... use MCP tool calls through the
Switchboard."* This RFC's use **is** exactly that — an on-demand MCP tool
call, made through the Switchboard, to the Concierge staffer. The distinction
RFC 0010 was drawing is between *interactive cross-butler reasoning* (which
must go through Switchboard fan-out so the target butler's own domain logic
and authority participate) and *deterministic infrastructure extraction*
(which should not spend an LLM session at all). Concierge's tool calls are
the latter: the MCP round-trip is standard Switchboard-routed tool
invocation, but the query behind the tool is the same deterministic,
zero-reasoning SQL RFC 0010 already sanctioned for batch use. The "real-time"
prohibition in RFC 0010 was never about batch-vs-interactive timing per se —
it was about not letting this exception become a bypass for the MCP-only
communication principle when a butler's own reasoning is actually needed. A
system-plane telemetry read needs no butler's reasoning at all; the
Switchboard-routed MCP tool call is itself the compliant path, and the view
underneath it is what keeps that tool call from costing eleven extra LLM
sessions.

### MUST NOT Be Reused When ANY of These Hold

1. **LLM reasoning is needed to extract, transform, or interpret the data.**
2. **Write operations** of any kind — no exceptions.
3. **The source table can carry free-text content and the exception has no
   column allowlist enforced at the view.**
4. **Unbounded key/column access** — the view must project a fixed, narrow
   column set, never `SELECT *` against a content-bearing table.
5. **Domain questions.** A question about a user's own finances, health, or
   relationships is never in scope for this pattern, no matter how it is
   phrased — it stays with the owning domain butler. Concierge's own
   `MANIFESTO.md` and `AGENTS.md` state this boundary explicitly.

## Integration

- **RFC 0002:** MCP-only inter-butler communication remains the default;
  Concierge's dashboard-chat-facing tool calls are ordinary Switchboard-routed
  MCP calls. Only the *data extraction underneath* one specific tool
  implementation bypasses per-butler MCP fan-out, exactly as RFC 0010
  established.
- **RFC 0006:** The views and the view-scoped `SELECT` grant are implemented
  as an Alembic migration within the existing multi-chain migration model,
  scoped to the `concierge` butler chain (`roster/concierge/migrations/`)
  rather than the shared `core` chain — the views live in the schema that
  consumes them, matching RFC 0010's `general.v_briefing_contributions`
  precedent rather than core_055/core_125's `public`-schema placement.
- **RFC 0010:** This RFC is a direct extension. Every guardrail RFC 0010
  established still applies; RFC 0030 adds the column-allowlist guardrail
  and documents the batch-vs-real-time distinction above.
- **`openspec/specs/database-security/spec.md`:** The `butler_concierge_rw`
  role and its view-scoped `SELECT` grant are a concrete instance of this
  spec's role-enforcement model, scoped even tighter than a typical
  public-table grant — the role has no privilege at all on the tables the
  view reads, only on the view.
- **`openspec/specs/staffer-archetype/spec.md`:** Concierge is a fourth
  concrete staffer (after switchboard, messenger, QA), validating the
  archetype's extensibility claim with `cross_butler_access = ["*"]` backed
  by database-level view grants rather than live MCP connectivity to every
  butler.

## Alternatives Considered

**Switchboard MCP fan-out to every domain butler.** Architecturally pure,
but costs up to one LLM session per butler per fleet-scoped question — the
same rejection RFC 0010 already made for the daily briefing, at a smaller
but recurring per-question cost instead of a fixed daily cost.

**A `public`-schema view (mirroring core_055's placement).** Rejected in
favor of housing the views in `concierge`'s own schema: Concierge is the
sole consumer, and RFC 0010's original `general.v_briefing_contributions`
precedent — a consumer-owned view, not a shared `public` one — is the closer
architectural fit than core_055/125's QA-staffer `public` views (which
predate a norm of housing single-consumer views in the consumer's schema).

**Exposing `sessions.prompt`/`sessions.result` behind an application-layer
redaction filter instead of omitting them from the view.** Rejected: an
application-layer filter is exactly the "enforced only by application code"
disqualifier RFC 0010's guardrail 5 and this RFC's guardrail 6 both rule
out. Omitting the columns from the view makes the constraint structural —
there is no code path that can accidentally forget to redact, because the
data was never selected.

**A single combined view instead of `v_fleet_sessions` +
`v_fleet_spend`.** Considered simpler, but conflates two different filter
semantics (`v_fleet_sessions` includes running sessions; `v_fleet_spend`
is completed-only, since a running session's token counts are not final).
Splitting them keeps each view's `WHERE` clause honest about what it
represents rather than pushing that distinction onto every caller.
