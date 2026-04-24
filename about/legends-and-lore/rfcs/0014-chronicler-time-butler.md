# RFC 0014: Chronicler Time Butler

**Status:** Draft
**Date:** 2026-04-24

## Summary

Chronicler is the retrospective Time Butler. It reconstructs the owner's lived
time from evidence already present in the system: Spotify listening sessions,
Steam play activity, OwnTracks location transitions, completed calendar events,
communication metadata, and butler session logs. Chronicler owns a derived
temporal model of overlapping episodes and point-in-time events. It is not a
planner, scheduler, router, or productivity judge.

The design is cost-conservative: Chronicler MUST NOT run an LLM pass over every
ingestion event. Most timeline construction is deterministic projection. LLM
interpretation is sparse and reserved for day-close summaries, ambiguity
resolution, explicit drilldowns, and correction assistance.

This RFC is a draft scope-expansion proposal. It does not amend the current
`about/heart-and-soul/v1.md` scope gate by itself. Until Heart and Soul is
updated, references to Chronicler's first release mean the proposed Chronicler
initial release, not the already-scoped v1 system.

## Motivation

Butlers already collect rich evidence about the owner's life, but that evidence
is spread across source-specific systems. Spotify knows listening, Steam knows
gaming, OwnTracks knows movement, Calendar knows planned events after they pass,
Relationship knows interaction history, and the core session log knows agent
work. None of these sources answers the cross-cutting question:

> What was I doing with my time?

The answer should be evidence-backed, retrospective, and honest about
uncertainty. The owner may have been listening to music while commuting; a chat
message may have arrived during a work block; a calendar meeting may have been
scheduled but not actually attended. A correct model must preserve overlap,
source provenance, and boundary uncertainty instead of forcing life into a
single exclusive activity lane.

## Design

### Butler Identity

Chronicler is a domain butler, not a staffer.

**Why it is a butler:** its domain is lived time, attention, routine, and
retrospection. Its primary user is the owner, and its user-facing questions are
life-domain questions: "Where did my afternoon go?", "How much time did I spend
gaming this week?", "What was happening around 8pm last night?"

**Why it is not a staffer:** it does not route messages, broker system state,
own connectors, enforce ingestion policy, or direct other butlers. It may run
deterministic projection jobs, but implementation style does not define service
type. Staffers serve the agent ecosystem; Chronicler serves the owner's life.

### Retrospective Scope

Chronicler's initial release models lived and past time only.

- Completed calendar events may become evidence after their end time.
- Current activity MAY remain open until source-specific closure rules can
  finalize it.
- Future commitments, agenda planning, forecasting, time blocking, and
  recommendations are out of scope.
- Deadlines and scheduled tasks are out of scope unless they produced actual
  activity evidence.

Chronicler is a historian, not a planner.

### Temporal Primitives

Chronicler maintains two first-class primitives:

1. **Events**: point-in-time facts such as "email received", "Steam achievement
   unlocked", "track changed", or "butler session started".
2. **Episodes**: bounded or open intervals such as "Spotify listening session",
   "train commute", "chat burst", "gaming session", or "agent investigation".

Episodes can overlap. Events can occur inside zero, one, or many episodes.
Chronicler MUST NOT collapse overlapping source activity into a single primary
activity during projection. Primary-activity judgments are presentation or
summary decisions and must preserve provenance.

### Boundary Provenance

Episodes do not always have clean starts or stops. Chronicler MUST distinguish
"the activity ended at this time" from "the system stopped seeing evidence after
this time."

Each episode records boundary semantics:

| Field | Values |
|-------|--------|
| `start_boundary` | `explicit`, `inferred` |
| `end_boundary` | `explicit`, `inferred`, `timeout`, `unknown` |

Examples:

- Spotify session: explicit when a session summary or stop signal is available;
  timeout/inferred when no playback is observed after an idle threshold.
- Steam session: explicit on status/game change; inferred from polling gaps when
  the final stop is not directly observed.
- OwnTracks dwell: explicit on leave transition; inferred from later location
  updates when no leave event exists.
- Chat/email burst: inferred from inactivity threshold.
- Butler session: explicit via `completed_at`; timeout/error via session status.

### Cost Model

Chronicler uses three tiers:

| Tier | Name | Cost | Use |
|------|------|------|-----|
| Tier 0 | Direct projection | No LLM | Typed events/sessions already expose temporal structure. |
| Tier 1 | Deterministic aggregation | No LLM | Group source records into bursts, dwell windows, or sessions. |
| Tier 2 | Sparse interpretation | LLM | Summaries, ambiguity resolution, explicit drilldowns, and correction assistance. |

Chronicler MUST NOT depend on an LLM pass for every ingestion event. Projection
runs asynchronously from persisted evidence and is checkpointed/idempotent.

### Switchboard Routing Boundary

Switchboard routes to Chronicler only for explicit user requests. Passive
activity awareness is not a routing competition between Chronicler and domain
butlers.

Examples:

- "What did I do yesterday afternoon?" routes to Chronicler.
- "How much time did I spend gaming this week?" routes to Chronicler.
- A Spotify playback event is not routed to Chronicler by classification; it is
  consumed by Chronicler's projection job from persisted evidence.
- A Spotify event may still serve Lifestyle as taste evidence through Lifestyle's
  own contracts.

### Source Ownership

Chronicler owns temporal projection, not source truth.

- Spotify/Steam/Lifestyle own media and hobby meaning.
- OwnTracks/Travel/Home own movement and place semantics.
- Relationship owns interaction meaning and contact context.
- Health owns physiological and wellness meaning.
- Calendar owns event management.
- Core owns session records and ingestion lineage.

Chronicler reads compatible evidence and creates derived events/episodes with
source references. It does not mutate source records or overwrite specialist
domain truth.

### Evidence Access Topology

Chronicler APIs, dashboard queries, and Chronicler LLM sessions read
Chronicler-owned derived tables only. Source evidence is imported by deterministic
projection jobs, not by ad hoc cross-schema queries during interactive requests.

Each source uses the approved adapter evidence path for this draft:

1. **Read-only adapter path:** Chronicler reads source-specific evidence through
   migration-tracked, least-privilege read-only views/grants. Adapter queries are
   deterministic batch work and must use guard patterns such as `to_regclass(...)`
   when optional source schemas/tables may be absent.
2. **Canonical evidence path:** deferred. A shared temporal evidence write
   surface requires a later RFC/spec defining table ownership, write authority,
   ACLs, provenance, retention, and migration contract before any source writes
   to it.

Adapters must not depend on `public.ingestion_events` alone for semantic
projection. `public.ingestion_events` is lineage metadata; source-specific raw
or summarized evidence must be explicitly named in each adapter contract.

### Correction and Override

Chronicler's initial release supports owner corrections. Corrections do not
rewrite source evidence. They create override records or superseding derived
episodes with provenance.

Examples:

- "That was not commuting; I was walking to lunch."
- "The music session continued until 10:15."
- "This calendar event did not happen."

The original source evidence remains queryable. Corrected views prefer active
overrides while preserving the audit trail.

### Privacy and Retention Inheritance

Chronicler projections inherit source privacy constraints. Projected records
must carry privacy tier, source reference, and retention/precision metadata.
Sensitive sources such as location and communications must not be retained at a
higher precision or for a longer period than the source contract permits unless
a specific Chronicler retention policy is documented and accepted.

When source evidence is purged, Chronicler may retain lower-precision derived
episodes only if the retained fields satisfy the source privacy policy. The
projection contract must define what survives, what is tombstoned, and what
source references become non-dereferenceable.

### Chronicler Compatibility Contract

Once this RFC is accepted, future timestamped sources MUST declare Chronicler
compatibility in their proposal/spec or explicitly state that they carry no
time-bearing evidence. A compatible source emits or exposes evidence that
Chronicler can project without bespoke LLM interpretation.

Each source compatibility note MUST specify:

| Field | Meaning |
|-------|---------|
| `source_name` | Stable source name, e.g. `fitbit`, `spotify`, `owntracks`. |
| `source_kind` | `connector`, `module`, `butler`, or `core`. |
| `supported_outputs` | `events`, `episodes`, or `both`. |
| `time_fields` | Point timestamp or interval fields exposed by the source. |
| `boundary_semantics` | Whether start/end boundaries are explicit, inferred, timeout-based, or unknown. |
| `source_ref_format` | Stable reference Chronicler can store and later dereference. |
| `taxonomy_mapping` | Event/episode type mappings into Chronicler's taxonomy. |
| `confidence_semantics` | How source confidence should be interpreted. |
| `privacy_tier` | Sensitivity/retention expectations for the source evidence. |
| `idempotency_key` | Stable key for projection upserts. |
| `projection_path` | `canonical_evidence` or `chronicler_adapter`. |

Two integration paths are recognized, but only the adapter path is in scope for
this draft:

1. **Adapter path:** Chronicler owns a deterministic source adapter that reads
   source-specific tables/events and projects them into Chronicler events and
   episodes.
2. **Canonical evidence path:** deferred until a separate RFC/spec defines the
   shared table, ACL/write matrix, provenance, retention, and migration contract.

For the proposed initial release, existing sources may use adapters because
their current contracts differ. Projection ownership stays with Chronicler either
way: Fitbit owns health truth; Chronicler owns lived-time projection.

### Initial Source Set

The proposed initial release includes only sources with durable, lower-ambiguity
evidence contracts:

- Butler/agent sessions from canonical session records.
- Completed calendar instances, deduplicated using the calendar workspace
  projection semantics.
- Spotify session summaries where durable start/end evidence exists.

Steam play deltas, OwnTracks dwell/commute episodes, fine-grained Spotify track
timelines, communication bursts, Home Assistant, live-listener, and future
Fitbit-like sources are deferred until their source evidence contracts explicitly
define durable time fields, closure semantics, privacy/retention behavior, and
idempotency.

## Integration

- **RFC 0003:** Switchboard routing remains the entry point for explicit user
  requests. Chronicler does not compete for passive source events.
- **RFC 0006:** Chronicler has its own schema. Any cross-schema reads must use
  migration-tracked, least-privilege access.
- **RFC 0009:** Context signals can be projected as time evidence, but
  Chronicler does not replace the context bus.
- **RFC 0010:** Deterministic cross-source batch reads may reuse the
  cost-justified exception pattern only when the reuse criteria hold: read-only,
  deterministic, batch, auditable, and materially cheaper than MCP fan-out.
- **RFC 0011:** Proactive insights remain brokered by Switchboard. Chronicler may
  propose insights later, but the proposed initial release does not require
  proactive recommendations.
- **RFC 0007 / dashboard visibility:** `/timeline` is already an operational
  system-event route. This RFC does not claim that route for Chronicler. A
  future dashboard proposal must either choose a distinct user-facing route or
  explicitly amend the existing dashboard timeline contract.

## Alternatives Considered

**Put timeline ownership in General.** Rejected because General is already the
catch-all memory surface. Lived time is a rich domain that deserves its own
manifesto and boundaries.

**Put timeline ownership in Lifestyle.** Rejected because Lifestyle owns taste,
media, food, entertainment, and hobbies. Chronicler needs to include work,
movement, calendar, communication, agent work, and health-adjacent sources.

**Make Chronicler a staffer.** Rejected because its primary user is the owner,
not the agent ecosystem. Projection-heavy internals do not make a domain butler
infrastructure.

**Route every source event to Chronicler through Switchboard.** Rejected because
it creates ambiguous routing competition with domain butlers and would increase
LLM cost. Chronicler consumes persisted evidence asynchronously instead.

**LLM-interpret every ingestion event.** Rejected as too expensive and
unnecessary. Typed source events and deterministic aggregation provide most of
the useful temporal structure.
