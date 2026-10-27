## MODIFIED Requirements

### Requirement: Butler Identity and Wire Contract

The Chronicler butler SHALL expose a stable identity as a domain butler (not
a staffer). Identity details that may drift (model, cron minutes, module list,
concurrency caps) are owned by `roster/chronicler/butler.toml` per
`about/heart-and-soul/vision.md` Rule 5 and are NOT mirrored here. The
Chronicler daemon SHALL NOT spawn reasoning sessions and SHALL NOT be a
routing target; goal invocations reach its retrospective-time capabilities
through the broker within their grants.

Source: RFC 0038 D1, D2
Scope: v1-mandatory

#### Scenario: Stable wire identity

- **WHEN** the butler is loaded from `roster/chronicler/butler.toml`
- **THEN** it SHALL have `name = "chronicler"`
- **AND** its database binding SHALL be `[butler.db]` with `name = "butlers"` and `schema = "chronicler"`
- **AND** its runtime role SHALL resolve to `butler_chronicler_rw`

#### Scenario: Domain butler type, not staffer

- **WHEN** the daemon classifies the Chronicler butler
- **THEN** it SHALL be a butler-typed agent whose retrospective-time
  capabilities goal invocations reach through the broker within their grants
- **AND** it SHALL NOT be a staffer (it has no ingress routing authority,
  no connector ownership, and no infrastructure role)

### Requirement: Retrospective-Only Scope

The Chronicler butler SHALL reconstruct past time from already-captured
evidence and SHALL NOT plan, schedule, dispatch, ingest externally, or send
proactive notifications. The sanctioned owner-facing messages are the
existing once-daily retrospective day-close summary (a scheduled recap, not a
proactive notification) and the opted-in correction prompts in the amendment
below. No other owner-facing messages are permitted.

**Amendment (memory write-back):** the Chronicler MAY synthesize durable
insights into a private memory schema it owns (`chronicler_mem`) via the memory
module, and its day-close obligation goal MAY propose entity-enrichment facts by
calling the `relationship` butler's proposal capability through the broker
within the obligation's declared capability set; the Chronicler daemon SHALL NOT
call another butler's model-reachable tools directly. These derived write-backs add no new owner-facing message, do not
constitute ingestion or scheduler authority, and never write another butler's
schema directly. `chronicler_mem` is a bounded, module-private schema owned by
the Chronicler, kept distinct from the domain `chronicler.episodes` table; it
is not generic cross-schema access.

**Amendment (opted-in correction prompts, owner decision bu-idc5mx.8 = A):**
opted-in correction prompts are the second sanctioned owner-facing surface.
Today the only one is the day-close gap interview ask
(`REQ-butler-chronicler-008`). It sends at most one prompt per closed day, passes
the owner's delivery-preferences and quiet-hours gate, and takes its answer
through the Chronicler corrections contract. "Opted-in" means it runs only
while its schedule is enabled and the owner's Telegram delivery is configured,
and only when the owner's delivery preferences allow it. It is a retrospective correction
request, not a plan, schedule or nudge. Any other owner-facing message,
including a new kind of correction prompt, remains forbidden unless its own
requirement sanctions it.

ID: REQ-butler-chronicler-011
Source: RFC 0038 D1, D2, D7; owner decision bu-idc5mx.8 (2026-10-24); bu-whhll.12; REQ-butler-chronicler-008
Scope: v1-mandatory

#### Scenario: No ingestion ownership

- **WHEN** a passive timestamped event arrives (Spotify playback change,
  Steam game start, OwnTracks point, Home Assistant state change, Google
  Health reading)
- **THEN** Chronicler SHALL NOT receive the event directly
- **AND** the event SHALL be handled by its owning domain butler's ingest
  path, never by Chronicler
- **AND** Chronicler's projection of that event SHALL run asynchronously
  on schedule, reading only from the owning domain's approved read surface

#### Scenario: No scheduling or planning

- **WHEN** a goal works on a request to "schedule X" or "plan Y"
- **THEN** no Chronicler capability SHALL offer to schedule or plan it
- **AND** Chronicler's tool surface SHALL NOT include scheduling tools

#### Scenario: Synthesized insight stays within own schema

- **WHEN** the Chronicler synthesizes a durable insight at day-close
- **THEN** it is written only to the Chronicler's own `chronicler_mem` schema
- **AND** no external data is ingested and the owner is not notified

#### Scenario: Cross-butler enrichment is an MCP proposal

- **WHEN** the Chronicler's day-close goal has a candidate entity fact worth sharing
- **THEN** it is proposed by a call to `relationship`'s proposal capability through the broker, within the day-close obligation's declared capability set
- **AND** the Chronicler daemon makes no direct call to `relationship`'s model-reachable tools
- **AND** the Chronicler does not write `entity_facts` directly

#### Scenario: Only the sanctioned surfaces reach the owner

- **WHEN** the Chronicler sends anything to the owner
- **THEN** it SHALL be either the once-daily day-close summary or the opted-in
  gap interview ask, which sends at most one prompt per closed day
- **AND** no other proactive, coaching or correction message SHALL be sent

### Requirement: No Per-Event LLM Invocation

Chronicler projection adapters SHALL NOT invoke an LLM on a per-event basis.
Tier 2 interpretation that uses a model SHALL NOT run in a session spawned by
the Chronicler daemon: the scheduled day-close SHALL run as an obligation goal
whose capability set is declared in git, and every other Tier 2 path SHALL be a
goal invocation unless RFC 0038 D18 names it as a model function.

Source: RFC 0038 D2, D5, D18; RFC 0014
Scope: v1-mandatory

#### Scenario: Projection adapter path forbids LLM

- **WHEN** a projection adapter runs under its scheduled job
- **THEN** it SHALL NOT make any LLM call
- **AND** guardrail tests SHALL assert the no-LLM invariant

#### Scenario: Bounded Tier 2 interpretation

- **WHEN** a Tier 2 interpretation path runs (day-close, drilldown,
  ambiguity resolution, correction assistance)
- **THEN** its input bundle SHALL be bounded to a fixed maximum size
- **AND** the call SHALL preserve provenance in output
- **AND** the call SHALL be a single model invocation (for day-close, the lead
  of an obligation goal), not a fan-out of invocations or children over events

#### Scenario: Day-close prose stays human-readable and local-day bound

- **WHEN** the scheduled day-close interpretation produces a candidate
  human-facing summary for a closed local day
- **THEN** timestamps SHALL be described in the owner's configured timezone
- **AND** raw source references, connector row IDs, truncation flags, and other
  machine-only provenance fields SHALL NOT be printed by default
- **AND** machine provenance SHALL remain available to the cache/staleness path
  from tool-call results rather than depending on prose citations
- **AND** the candidate SHALL carry a structured `date_label` equal to the
  owner-timezone local day represented by its cache key and cache window
- **AND** the cache key SHALL bind that date to the exact resolved owner IANA
  timezone, so different local-day windows cannot share a cache row or writer
  lock
- **AND** writer serialization SHALL retain the actual date and exact timezone
  tuple rather than treating a fixed-width hash as identity
- **AND** the cache writer SHALL use a documented deterministic shape predicate
  to admit only owner-facing retrospective prose, not a model judgment or a
  second LLM call

#### Scenario: Execution trace or date mismatch is not cacheable prose

- **WHEN** a day-close candidate is empty, lacks or mismatches its structured
  `date_label`, contains a machine role/tool-call/protocol payload, serialized
  object, code fence, or documented execution-planning/planning-verb preamble,
  or otherwise fails the deterministic shape predicate
- **THEN** Chronicler SHALL classify the candidate as invalid
- **AND** it SHALL NOT create or replace a renderable day-close cache entry
- **AND** it SHALL NOT invoke an LLM to repair, classify, or rewrite the
  candidate

#### Scenario: Invalid persisted cache is contained on read

- **WHEN** a cache reader encounters a previously persisted day-close row that
  fails the same deterministic admission predicate or local-day binding
- **THEN** it SHALL treat the row as invalid rather than fresh or stale
- **AND** it SHALL retain the row for audit/recovery without returning its prose
  to an owner-facing caller
- **AND** a covered, available briefing SHALL use deterministic fallback copy
  instead of the invalid prose

## REMOVED Requirements

### Requirement: Switchboard Routing Boundary

**Reason**: RFC 0038 D1 and the Hard cutover delete model classification and route dispatch, so Switchboard no longer routes any message to Chronicler or to another butler.
**Migration**: Owner retrospective questions are admitted as owner goals (REQ-goal-orchestration-008) whose lead calls Chronicler read capabilities; recommendation and scheduling requests use the owning domain's capabilities; passive events stay with their owning domain's ingest path and are projected on schedule, as Retrospective-Only Scope requires.
