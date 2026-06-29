# Chronicler Intent / Evidence / Activity

## Purpose

Reframe chronicler projection around three layers — Intent (planned), Evidence
(raw signals, consumed not owned), and Activity (inferred, counted) — so that
only corroborated activity counts as lived time, every activity carries a
confidence and an evidence chain, and the chronicler can synthesize durable
insights within its own schema.

## ADDED Requirements

### Requirement: Three-Layer Episode Classification

Every chronicler episode SHALL be classified into exactly one layer: `intent`,
`evidence`, or `activity`. Only `activity`-layer episodes are counted by any
time or balance aggregate.

#### Scenario: Calendar block is intent

- **WHEN** a calendar event instance is projected
- **THEN** the resulting episode is classified `intent`
- **AND** it is excluded from all time/balance aggregate totals
- **AND** it remains queryable for display as a planned block

#### Scenario: Inferred block is activity

- **WHEN** a deterministic projector emits an inferred block (e.g. Exercise)
- **THEN** the resulting episode is classified `activity`
- **AND** it is included in time/balance aggregate totals

#### Scenario: Raw signal is evidence

- **WHEN** a raw point signal (GPS point, HR sample) is projected
- **THEN** it is classified `evidence`
- **AND** it is excluded from time/balance totals
- **AND** it remains linkable as an `evidence_ref` of an activity

### Requirement: Calendar Is Never Counted; Activities Counted On Their Own Merits

Aggregates SHALL count the `activity` layer only; a calendar `intent` block is
dropped wholesale from every total and SHALL never produce a "calendar" lane.
"Corroboration" is therefore automatic, not a matching step: when a real activity
overlaps a calendar block, the *activity* is counted on its own merits under its
own lane, and the calendar block still contributes nothing. There SHALL be no
calendar→activity attributor in the counting path. (Dropping a calendar block
that an activity actively *contradicts* — e.g. "gym 9am" vs GPS-at-home — is
conflict resolution performed at day-close, not in the counting path; see
"Day-Close Reconciliation".)

#### Scenario: Uncorroborated calendar block contributes zero

- **WHEN** a 5-hour calendar block has no overlapping activity in its window
- **THEN** the block contributes 0 seconds to every aggregate lane
- **AND** no "calendar" lane appears in the aggregate

#### Scenario: Activity overlapping a calendar block is what counts

- **WHEN** a GPS-dwell + resolved-participant `social` activity overlaps a
  calendar block
- **THEN** the `social` activity's time is counted under the `social` lane on its
  own merits
- **AND** the calendar block itself still contributes 0 seconds
- **AND** no time is attributed to a "calendar" lane

### Requirement: Activity Lane Taxonomy

Activity aggregates SHALL roll up into life-balance lanes:
`sleep`, `exercise`, `work`, `play`, `social`, `travel`, `eat`, `rest`. Source
types such as music, gaming, and calendar SHALL NOT appear as top-level lanes.
The complete mapping from existing episode types to lanes SHALL be defined and
each lane covered by a test:

| Existing episode type / source | Lane |
|---|---|
| `google_health` sleep | `sleep` |
| `google_health` workout, inferred exercise (HR+GPS+cadence) | `exercise` |
| `core.sessions` work | `work` |
| Spotify listening, Steam play | `play` |
| comms bursts (gmail/telegram/whatsapp/discord), co-presence | `social` |
| `owntracks` movement | `travel` |
| meals | `eat` |
| idle/at-home presence with no other activity | `rest` |

Existing projected source episodes (sleep, workout, listening, play, work,
movement, meal) ARE the `activity`-layer rows for their lane; Tier-1 projectors
do not re-emit duplicates of them — they add `confidence`/`evidence_refs` and
emit only genuinely *new* inferred activities (e.g. an exercise block inferred
from HR+GPS when no workout episode exists). Where both a source episode and an
inferred candidate cover the same block, day-close reconciliation merges them.

#### Scenario: Music and gaming roll into Play

- **WHEN** Spotify listening and Steam play activities are aggregated
- **THEN** their time appears under the `play` lane
- **AND** neither `music` nor `gaming` appears as a top-level lane

#### Scenario: Every lane has a covering mapping

- **WHEN** an episode of each mapped source type is aggregated
- **THEN** it rolls up under the lane named in the mapping table
- **AND** meals map to `eat`, workouts to `exercise`, and movement to `travel`

### Requirement: Activity Confidence From Independent Corroboration

Every `activity` episode SHALL carry a `confidence` of `high`, `medium`, or
`low`, derived from the count of independent evidence kinds corroborating it.
Low-confidence activities are still counted in totals but are flagged.

#### Scenario: Multiple independent signals yield high confidence

- **WHEN** an Exercise activity is corroborated by elevated heart rate, GPS
  dwell at a gym, and step cadence (three independent kinds)
- **THEN** its confidence is `high`

#### Scenario: Single weak signal yields low confidence

- **WHEN** an activity is supported by a single weak/ambiguous signal
- **THEN** its confidence is `low`
- **AND** it is still included in aggregate totals
- **AND** it is eligible to be surfaced as a correction prompt

### Requirement: Evidence Chain Exposed Per Activity

Every `activity` episode SHALL expose its corroborating evidence as
`evidence_refs[]`, each naming the source and signal that supports it.

#### Scenario: Activity drill-down lists its evidence

- **WHEN** a client requests the evidence chain for an activity
- **THEN** the response lists each supporting signal with its source name
- **AND** an activity with no surviving evidence refs is reported as
  uncorroborated

### Requirement: Deterministic Candidate Projection

Tier-1 projectors SHALL emit candidate `activity` episodes from evidence without
invoking an LLM. Candidates MAY overlap or conflict; reconciliation is deferred
to day-close.

#### Scenario: Candidate emitted without LLM

- **WHEN** a deterministic projector runs on its cadence
- **THEN** it emits candidate activities from evidence rules
- **AND** no LLM is invoked during projection

### Requirement: Deterministic Reconciliation Core With LLM Narration

Candidate dedup and intent-vs-evidence conflict resolution SHALL be performed by
a **pure, deterministic function** (time-overlap merge of same-lane candidates;
drop a calendar intent whose window is contradicted by activity evidence) that
is unit-testable without an LLM. The once-daily day-close LLM SHALL only *narrate
over* the reconciled result (labels ambiguous blocks, writes prose) and SHALL
remain the only LLM invocation in the projection path. Aggregate correctness
SHALL NOT depend on LLM output.

#### Scenario: Conflicting intent dropped deterministically

- **WHEN** a calendar intent says "gym 9am" but location evidence places the
  owner at home during that window
- **THEN** the deterministic reconciler drops the gym block (it is not counted)
- **AND** this holds without invoking the LLM
- **AND** the narrative does not assert attendance

#### Scenario: Duplicate candidates merged deterministically

- **WHEN** two sources emit overlapping same-lane candidates for one lived block
- **THEN** the deterministic reconciler merges them into one activity that links
  the evidence from both sources
- **AND** the merged block is counted once

### Requirement: Layer Stamped On Every Projection Write

Every projection adapter SHALL stamp `layer` on insert for all newly-projected
rows — calendar → `intent`, lived-activity sources → `activity`, raw point
signals → `evidence` — not only on a one-time backfill. The `layer` column SHALL
have a conservative non-null default that never causes uncounted activity or
counted intent.

#### Scenario: Freshly-projected calendar block is intent

- **WHEN** the calendar adapter projects a new event after this change ships
- **THEN** the stored episode has `layer = intent`
- **AND** it is excluded from lived-time totals

#### Scenario: Freshly-projected activity is counted

- **WHEN** an activity-source adapter projects a new episode after this change
- **THEN** the stored episode has `layer = activity`
- **AND** it is included in lived-time totals

### Requirement: Comms Projected Into Social

A deterministic adapter SHALL project already-ingested message activity
(Gmail / Telegram / WhatsApp / Discord) into `social` activities, resolving
participants via `relationship.entity_facts`.

#### Scenario: Message burst becomes a Social activity

- **WHEN** a sustained message exchange with a resolved participant occurs
- **THEN** a `social` activity is emitted naming that participant
- **AND** participant identity is resolved through `relationship.entity_facts`,
  not a chronicler-local contact store

#### Scenario: Unresolved participant degrades gracefully

- **WHEN** a message burst's participant cannot be resolved to an entity
- **THEN** a `social` activity is still emitted with an unattributed participant
- **AND** the activity confidence reflects the missing resolution

### Requirement: Memory Write-Back Within Own Schema

The chronicler SHALL synthesize durable insights into its own schema via the
memory module, and MAY propose entity-enrichment facts to the `relationship`
butler over MCP. It SHALL NOT write directly to another butler's schema, ingest
external data, or notify the owner.

#### Scenario: Insight written to own schema

- **WHEN** day-close synthesizes a durable insight (e.g. accumulating sleep debt)
- **THEN** the insight is written to the chronicler's own memory tables with
  `source=chronicler` provenance and a confidence
- **AND** no other butler's schema is written directly

#### Scenario: Entity enrichment proposed over MCP

- **WHEN** repeated co-presence resolves to a person worth recording
- **THEN** the chronicler proposes the fact to `relationship` over MCP
- **AND** it does not write `entity_facts` directly

#### Scenario: Low-confidence block scheduled for re-reconciliation

- **WHEN** a block remains low-confidence at day-close
- **THEN** a self-reminder is recorded so a later day-close re-reconciles it
  after evidence backfill
- **AND** the owner is not notified

## Source References

- `butler-chronicler/spec.md` §4.8 (No Per-Event LLM Invocation), §4.15
  (Calendar Scheduled Blocks Are Not Attendance Assertions), §4.4 (Owner-Only
  Adapter Entity Attribution).
- Non-Negotiable Rules (vision.md): schema isolation; MCP-only inter-butler
  communication.
- RFC 0014 (Chronicler Time Butler).
