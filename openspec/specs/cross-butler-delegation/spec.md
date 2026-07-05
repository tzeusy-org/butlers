# Cross-Butler Delegation

## Purpose

Defines the cross-butler delegation ledger: how one butler asks a question
that another butler's domain covers, how the target is resolved, how routing
happens, and how the answer is recorded and discovered. Sequenced after the
`memory_catalog` default-on flip (bu-qvnce.15) per the 2026-07-04 JARVIS
pursuit dossier (`docs/redesigns/2026-07-04-jarvis-pursuit.md`). bu-gxmfx
implements v1: post question -> resolve -> route -> record answer -> discover.

## Requirements

### Requirement: Ledger Of Record
Every delegated question SHALL be recorded as exactly one row in
`public.delegation_ledger`, identified by a durable `id`. A question is never
silently dropped: every terminal outcome (`unroutable`, `failed`, `answered`)
and the in-flight state (`pending`, `routed`) SHALL be persisted and readable
back via `ledger_id`.

#### Scenario: Every ask produces a ledger row
- **WHEN** a butler calls `delegate_ask` with a non-empty question
- **THEN** exactly one `public.delegation_ledger` row is created with that
  question and the asking butler's name, and its `id` is returned to the
  caller as `ledger_id`

#### Scenario: A write failure is never silently absorbed
- **WHEN** the ledger insert or a status transition fails at the database
  layer
- **THEN** the failure SHALL propagate to the caller as an explicit error
  response rather than being swallowed or reported as a false success

### Requirement: Domain Resolution Via Shared Catalog
"Whose domain covers this question" SHALL be resolved by querying
`public.memory_catalog` (the same shared discovery index that backs
`GET /api/memory/catalog/search` / Fleet Knowledge search) via a hybrid
(semantic + full-text) search over the question text. No parallel or
butler-local relevance index SHALL be introduced for this resolution.

#### Scenario: Top catalog hit names the target
- **WHEN** `delegate_ask` runs a hybrid catalog search for the question and
  the top hit's `source_butler` (or `source_schema` when `source_butler` is
  unset) names a butler other than the asker
- **THEN** that butler is recorded as the ledger row's `target_butler` and
  dispatch is attempted

#### Scenario: No catalog match is unroutable
- **WHEN** the catalog search returns no hits for the question
- **THEN** the ledger row is recorded with `status = 'unroutable'` and
  `reason = 'no_catalog_match'`, `target_butler` is left null, and no dispatch
  is attempted

#### Scenario: Self-delegation is refused
- **WHEN** the resolved target butler is the same butler that asked the
  question
- **THEN** the ledger row is recorded with `status = 'unroutable'` and
  `reason = 'self_target'`, and no dispatch is attempted

### Requirement: Routing Goes Through The Switchboard
Dispatch of a resolved delegated question to its target butler SHALL go
through the Switchboard's existing `route()` primitive (the same routing
function used by `post_mail`, `correct_route`, and `route_to_butler`) --
never a bespoke point-to-point dispatch path.

#### Scenario: Non-Switchboard asker dispatches via the Switchboard MCP client
- **WHEN** a non-Switchboard butler's `delegate_ask` has resolved a target
  butler
- **THEN** dispatch calls the Switchboard's `route` MCP tool (via the
  butler's `switchboard_client`) with `target_butler`, `tool_name =
  "delegate_receive"`, and the ledger id, question, and asking butler as args

#### Scenario: Switchboard asking itself dispatches in-process
- **WHEN** the Switchboard butler itself calls `delegate_ask`
- **THEN** dispatch calls the underlying `route()` function directly
  in-process (no MCP round trip to itself), with the same routing/eligibility
  checks as any other route dispatch

#### Scenario: A dispatch failure is recorded honestly
- **WHEN** the Switchboard `route()` call errors (target unreachable, stale,
  quarantined, or the tool call itself fails)
- **THEN** the ledger row transitions to `status = 'failed'` with the error
  recorded in `reason`, and the response to the asking butler names the
  failure and marks it `retryable` when the failure looks transient
  (timeout, connection error, Switchboard not connected)

### Requirement: Answering Is Asynchronous And Guarded
Receiving a delegated question SHALL NOT block the dispatching Switchboard
`route()` call on an LLM run. The target butler SHALL be woken to answer via
a scheduled one-shot task, and posting the answer SHALL only succeed for the
row's actual assigned target.

#### Scenario: Receipt schedules an answering task without blocking
- **WHEN** `delegate_receive` is called for a `'pending'` ledger row whose
  `target_butler` matches the receiving butler
- **THEN** it schedules a one-shot task instructing the butler's next spawned
  session to answer and call `delegate_answer`, and returns without waiting
  for that session to run

#### Scenario: Duplicate receipt is not re-scheduled
- **WHEN** `delegate_receive` is called again for a ledger row that is no
  longer `'pending'` (already `'routed'` or `'answered'`)
- **THEN** no new task is scheduled and the response reports the row's
  existing status rather than silently no-op'ing

#### Scenario: Only the assigned target may answer
- **WHEN** `delegate_answer` is called with a `ledger_id` whose row's
  `target_butler` does not match the calling butler, or whose `status` is
  not `'routed'`
- **THEN** no answer is recorded and an explicit error is returned -- never a
  fabricated success

#### Scenario: A valid answer completes the ledger row
- **WHEN** `delegate_answer` is called by the correct `target_butler` for a
  `'routed'` row
- **THEN** the row transitions to `status = 'answered'` with `answer`,
  `answered_at`, and `answering_butler` recorded

### Requirement: Discoverability
Every delegation-ledger row SHALL be discoverable outside the asking and
answering butlers' own sessions, without a per-butler fan-out (the ledger is
one shared `public` table reachable from any pool).

#### Scenario: List recent delegations
- **WHEN** a caller requests `GET /api/delegation/ledger` with optional
  `status`, `asking_butler`, or `target_butler` filters
- **THEN** matching rows are returned most-recent-first with pagination
  metadata, without fanning out to multiple butler pools

#### Scenario: Fetch one delegation by id
- **WHEN** a caller requests `GET /api/delegation/ledger/{id}` for an
  existing row
- **THEN** the full row (including `answer` once present) is returned

#### Scenario: Unknown id is a 404, not an empty success
- **WHEN** a caller requests `GET /api/delegation/ledger/{id}` for an id with
  no matching row
- **THEN** the response is `404 Not Found`, never a `200` with null/empty
  data standing in for "not found"
