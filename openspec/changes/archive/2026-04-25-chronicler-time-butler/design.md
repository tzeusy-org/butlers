## Context

The project already has the raw material for lived-time reconstruction:

- `public.ingestion_events` records canonical connector events and request IDs.
- Spotify and Steam connectors produce typed activity events and session-like
  summaries.
- OwnTracks produces location and transition events.
- Calendar projections know completed scheduled blocks.
- Passive interaction sync and message inbox data expose communication bursts.
- Core session logs expose butler/agent work with start and completion times.
- The context bus exposes temporary situation signals.

The missing layer is a domain owner for the owner's lived time: one place that
can preserve temporal evidence, create overlapping episodes, support correction,
and answer retrospective questions without requiring a new LLM pass over every
source event.

## Goals / Non-Goals

**Goals:**
- Model lived/past time as overlapping episodes plus point events.
- Preserve source provenance and boundary uncertainty.
- Include correction/override in the Chronicler initial release.
- Keep routine projection deterministic and idempotent.
- Reserve LLM usage for sparse interpretation: summaries, ambiguity resolution,
  explicit drilldowns, and correction assistance.
- Define a compatibility contract for future timestamped sources.

**Non-Goals:**
- Future schedule/agenda views.
- Real-time activity tracking guarantees.
- One exclusive "primary activity" per time slot at storage time.
- Routing all passive events through Switchboard to Chronicler.
- Rewriting source-domain records.
- `/timeline` UX design.
- Claiming the existing operational `/timeline` route.

## Decisions

### D1: New domain butler named Chronicler

Chronicler is a butler because it serves a life domain: lived time, attention,
routine, and retrospection. It is not a staffer because it does not route
messages, broker infrastructure state, own connectors, or direct other butlers.

### D2: Retrospective-only initial release

Chronicler's initial release models only past/lived time. This is a proposed
scope expansion; it does not amend the already-scoped v1 system until Heart and
Soul is updated. Calendar events become evidence after they end. Open/current
evidence may remain unfinalized until closure policies resolve it. Future
planning belongs to calendar/scheduling capabilities, not Chronicler.

### D3: Events and episodes are separate primitives

Events are point-in-time facts. Episodes are intervals. Both carry source,
provider, type, title/summary, metadata, and source references. Episodes can
overlap, and events can link to episodes as starts, ends, supports,
occurs_during, or contradicts.

### D4: Boundary provenance is mandatory

Episode starts and ends can be explicit or inferred. End boundaries can also be
timeout-based or unknown. This prevents the timeline from implying certainty
when the source only stopped producing evidence.

### D5: Projection is tiered by cost

- Tier 0 direct projection creates rows from typed source facts.
- Tier 1 deterministic aggregation groups source records into bursts, dwell
  windows, sessions, or intervals.
- Tier 2 sparse LLM interpretation is reserved for high-value cases.

No background process may require LLM interpretation of every ingestion event.

### D6: Switchboard routes explicit requests only

Switchboard routes "what did I do yesterday?" to Chronicler. It does not route
every Spotify, Steam, OwnTracks, email, or chat event to Chronicler. Passive
construction happens from persisted evidence streams and projection jobs.

### D7: Corrections are overlays, not source rewrites

Owner corrections produce override records or superseding derived episodes.
Source evidence remains intact and queryable. Corrected views prefer active
overrides while retaining audit history.

### D8: Future sources declare Chronicler compatibility

New timestamped sources should include a compatibility note in their
proposal/spec. The note tells Chronicler whether the source emits events,
episodes, or both; how boundaries are determined; how source references work;
what taxonomy mappings apply; and whether projection uses a canonical evidence
contract or a Chronicler-owned adapter.

### D9: Chronicler reads derived tables at request time

Chronicler APIs, dashboard queries, and LLM sessions read Chronicler-owned
derived tables. Cross-source evidence reads happen only inside deterministic
projection jobs through migration-tracked read-only views/grants. The canonical
evidence write path is deferred until a separate RFC/spec defines shared table
ownership, ACLs, write authority, provenance, retention, and migration contract.
This keeps interactive requests inside the normal butler isolation model and
limits RFC 0010-style exceptions to auditable batch projection.

## Data Model Sketch

```sql
CREATE TABLE chronicler.events (
    id UUID PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    occurred_at TIMESTAMPTZ NOT NULL,
    source_channel TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    source_ref JSONB NOT NULL,
    privacy_tier TEXT NOT NULL DEFAULT 'standard',
    retention_policy JSONB,
    precision_policy JSONB,
    source_ref_status TEXT NOT NULL DEFAULT 'active',
    tombstoned_at TIMESTAMPTZ,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chronicler.episodes (
    id UUID PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    episode_type TEXT NOT NULL,
    source_channel TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    start_boundary TEXT NOT NULL,
    end_boundary TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_refs JSONB NOT NULL,
    privacy_tier TEXT NOT NULL DEFAULT 'standard',
    retention_policy JSONB,
    precision_policy JSONB,
    source_ref_status TEXT NOT NULL DEFAULT 'active',
    tombstoned_at TIMESTAMPTZ,
    metadata JSONB,
    superseded_by UUID REFERENCES chronicler.episodes(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chronicler.episode_events (
    episode_id UUID NOT NULL REFERENCES chronicler.episodes(id),
    event_id UUID NOT NULL REFERENCES chronicler.events(id),
    relation TEXT NOT NULL,
    PRIMARY KEY (episode_id, event_id, relation)
);

CREATE TABLE chronicler.episode_overrides (
    id UUID PRIMARY KEY,
    episode_id UUID NOT NULL REFERENCES chronicler.episodes(id),
    corrected_type TEXT,
    corrected_title TEXT,
    corrected_started_at TIMESTAMPTZ,
    corrected_ended_at TIMESTAMPTZ,
    note TEXT,
    privacy_tier TEXT NOT NULL,
    retention_policy JSONB,
    precision_policy JSONB,
    tombstoned_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The implementation may refine names and constraints, but it must preserve the
core semantics: event/episode separation, overlap support, source references,
boundary provenance, confidence, privacy/precision/retention metadata,
tombstone state, and override history.

## Evidence Access and Initial Source Adapters

`public.ingestion_events` is lineage metadata and is not sufficient by itself
for source projection. Each adapter must name the actual durable evidence it
reads, its retention assumptions, and its idempotency key.

Initial release adapters:

- **Core sessions:** canonical session records only; process logs are TTL
  diagnostics and are not source truth.
- **Calendar:** completed calendar instances where `ends_at < evaluation_time`,
  excluding cancelled events and deduplicating provider projections using the
  calendar workspace strategy.
- **Spotify:** durable session summaries only. Fine-grained track timelines are
  deferred until the connector/source contract guarantees durable track-change
  evidence.

Deferred until source contracts exist:

- **Steam:** current evidence may be aggregate deltas rather than true play
  sessions; treat as inferred only after a source contract is written.
- **OwnTracks:** dwell/commute projection needs explicit privacy, precision, and
  retention rules before Chronicler persists derived location episodes.
- **Email/chat:** metadata bursts need source-specific retention and participant
  rules before projection.

## Chronicler Compatibility Contract

Future timestamped sources must include a section like this, or explicitly state
that they are not time-bearing:

```yaml
chronicler_compatibility:
  source_name: fitbit
  source_kind: module
  supported_outputs: both
  time_fields:
    event: occurred_at
    episode: [started_at, ended_at]
  boundary_semantics:
    workout: explicit
    sleep: explicit_or_inferred
    step_burst: inferred
  source_ref_format: "fitbit:<resource_type>:<id>"
  taxonomy_mapping:
    workout: health.exercise
    sleep: rest.sleep
    step_burst: mobility.walking
  confidence_semantics: "1.0 for Fitbit bounded activities; lower for inferred bursts"
  privacy_tier: sensitive
  idempotency_key: "fitbit:<resource_type>:<id>:<timestamp>"
  projection_path: chronicler_adapter
```

The contract is advisory for existing sources until they are brought under a
specific adapter. It is mandatory for new timestamped sources after this change
is accepted. Concurrent draft source changes that predate RFC 0014, such as
Google Health, need an explicit retrofit or deferral task once Chronicler is
accepted. The contract prevents future modules from becoming invisible to
Chronicler or requiring ad hoc LLM interpretation.

## Risks / Trade-offs

**[Cross-source read complexity]** Chronicler needs evidence from many places.
Mitigation: the proposed initial release uses deterministic adapters and
migration-tracked read access. Where cross-schema reads are needed, reuse the RFC
0010 guardrails only for read-only, deterministic, batch access.

**[Taxonomy sprawl]** Every source may invent types. Mitigation: start with a
fixed event/episode taxonomy and source-specific subtypes.

**[False certainty]** Inferred boundaries can look factual. Mitigation:
boundary provenance and confidence are mandatory and must be exposed in APIs.

**[Token creep]** Summarization can expand into continuous interpretation.
Mitigation: background projection is Tier 0/Tier 1 by default; Tier 2 is limited
to scheduled summaries, explicit drilldowns, ambiguity resolution, and
correction assistance.

**[Domain ownership confusion]** Lifestyle, Health, Relationship, and Travel may
also care about the same evidence. Mitigation: Chronicler owns temporal shape,
not domain truth.
