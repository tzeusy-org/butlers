## 1. Shape Artifacts

- [ ] 1.1 Add `roster/chronicler/MANIFESTO.md` defining Chronicler as the retrospective Time Butler. `[Spec: butler-chronicler / Chronicler Butler Identity]`
- [ ] 1.2 Add Chronicler schema/role bootstrap (`chronicler`, `butler_chronicler_rw`, managed schema list, grants/default privileges) before enabling roster startup. `[Spec: butler-chronicler / Evidence Access Topology]`
- [ ] 1.3 Update Heart and Soul scope explicitly before treating Chronicler as part of the scoped v1 system. `[Spec: butler-chronicler / Retrospective Scope]`
- [ ] 1.4 Add `roster/chronicler/butler.toml` with butler type, initial identity, and no staffer permissions after role/schema bootstrap exists. `[Spec: butler-chronicler / Chronicler Butler Identity]`

## 2. Database and Projection Model

- [ ] 2.1 Add Chronicler schema migrations for events, episodes, episode-event links, episode overrides, projection checkpoints, and source adapter state. `[Spec: butler-chronicler / Point Events, Overlapping Episodes, Episode-Event Links, Corrections and Overrides, Projection Checkpoints and Adapter State]`
- [ ] 2.2 Add typed models for events, episodes, boundary provenance, source references, privacy metadata, precision metadata, retention metadata, tombstone/source-ref state, and overrides. `[Spec: butler-chronicler / Episode Boundary Provenance, Privacy and Retention Inheritance]`
- [ ] 2.3 Add idempotent upsert helpers for point events and episodes with materialized idempotency keys. `[Spec: butler-chronicler / Point Events / Point event idempotency]`
- [ ] 2.4 Add corrected-view helpers that apply active overrides without mutating source evidence. `[Spec: butler-chronicler / Corrections and Overrides]`

## 3. Evidence Access and Initial Source Adapters

- [ ] 3.1 Implement projection access surfaces using canonical evidence records or migration-tracked read-only views/grants, including session and calendar read surfaces with optional-schema guards. `[Spec: butler-chronicler / Evidence Access Topology]`
- [ ] 3.2 Implement core session-record projection; do not use TTL process logs as source truth. `[Spec: butler-chronicler / Initial Source Adapters / Core sessions projected]`
- [ ] 3.3 Implement completed-calendar-instance projection with status filtering and provider deduplication. `[Spec: butler-chronicler / Initial Source Adapters / Completed calendar instance projected]`
- [ ] 3.4 Establish a durable Spotify session-summary evidence contract/table/view with retention and idempotency semantics, or keep Spotify projection deferred. `[Spec: butler-chronicler / Initial Source Adapters / Spotify session summary projected]`
- [ ] 3.4.1 Implement Spotify session-summary projection only after durable start/end evidence exists. `[Spec: butler-chronicler / Initial Source Adapters / Spotify session summary projected]`
- [ ] 3.5 Defer Steam, OwnTracks, communication-burst, fine-grained Spotify track, Home Assistant, live-listener, and Fitbit-like adapters until source compatibility contracts exist. `[Spec: butler-chronicler / Initial Source Adapters / Deferred source lacks contract]`
- [ ] 3.6 Add projection checkpoints and idempotency tests for each implemented adapter. `[Spec: butler-chronicler / Projection Checkpoints and Adapter State, Point Events]`

## 4. Interpretation Tiers

- [ ] 4.1 Implement Tier 0 direct projection paths with no LLM dependency. `[Spec: butler-chronicler / Sparse Interpretation Cost Boundary / Tier 0 direct projection]`
- [ ] 4.2 Implement Tier 1 deterministic aggregation paths with no LLM dependency. `[Spec: butler-chronicler / Sparse Interpretation Cost Boundary / Tier 1 deterministic aggregation]`
- [ ] 4.3 Implement Tier 2 sparse interpretation entry points for day-close summaries, explicit drilldowns, ambiguity resolution, and correction assistance. `[Spec: butler-chronicler / Sparse Interpretation Cost Boundary / Tier 2 sparse interpretation]`
- [ ] 4.4 Add guardrail tests proving adapters do not invoke LLMs per ingestion event. `[Spec: butler-chronicler / Sparse Interpretation Cost Boundary]`

## 5. Switchboard Routing Boundary

- [ ] 5.1 Update Switchboard classification metadata so explicit retrospective time-review requests route to Chronicler. `[Spec: butler-switchboard / Explicit Chronicler Routing Boundary]`
- [ ] 5.2 Add routing tests for "what did I do yesterday", "how much time did I spend listening", music recommendation, scheduling, and passive timestamped events. `[Spec: butler-switchboard / Explicit Chronicler Routing Boundary]`
- [ ] 5.3 Add overlap tests proving Lifestyle/Relationship/etc. remain domain targets while Chronicler consumes compatible evidence by projection. `[Spec: butler-chronicler / Switchboard Routing Boundary / Domain overlap preserved]`

## 6. Chronicler Compatibility Contract

- [ ] 6.1 Document the compatibility declaration template in source/spec authoring guidance. `[Spec: chronicler-source-compatibility / Timestamped Source Compatibility Declaration]`
- [ ] 6.2 Add compatibility declarations for initial implemented sources and explicit deferral notes for sources that are not ready, including a retrofit/deferral note for the concurrent Google Health draft if it still exists. `[Spec: chronicler-source-compatibility / Compatibility Fields]`
- [ ] 6.3 Add a checklist or lint/test helper that flags new timestamped source specs without a Chronicler compatibility section or explicit not-time-bearing statement. `[Spec: chronicler-source-compatibility / Timestamped Source Compatibility Declaration]`
- [ ] 6.4 Add privacy/retention review checks for sensitive sources. `[Spec: chronicler-source-compatibility / Privacy and Retention Declaration]`

## 7. API and Future Timeline Consumer

- [ ] 7.1 Add Chronicler read APIs under `/api/chronicler/*` for querying events/episodes by time range, source, type, confidence, and privacy tier. `[Spec: chronicler-api / Chronicler Temporal Reads]`
- [ ] 7.2 Add correction/override API endpoints under `/api/chronicler/*`. `[Spec: chronicler-api / Chronicler Corrections]`
- [ ] 7.3 Expose source refs, boundary semantics, confidence, privacy/precision/retention state, and tombstone state in API responses. `[Spec: chronicler-api / Chronicler Response Provenance]`
- [ ] 7.4 Do not claim the existing operational `/timeline` route without a separate dashboard/API spec amendment. `[Spec: chronicler-api / Operational Timeline Route Preserved]`

## 8. Verification

- [ ] 8.1 Add migration tests for Chronicler schema, indexes, idempotency keys, privacy/precision/retention columns, and tombstone/source-ref state. `[Spec: butler-chronicler / Point Events, Overlapping Episodes, Privacy and Retention Inheritance]`
- [ ] 8.2 Add adapter unit tests using representative source fixtures. `[Spec: butler-chronicler / Initial Source Adapters]`
- [ ] 8.3 Add integration tests for overlapping episodes and point events inside episodes. `[Spec: butler-chronicler / Overlapping Episodes, Episode-Event Links]`
- [ ] 8.4 Add correction/override tests. `[Spec: butler-chronicler / Corrections and Overrides]`
- [ ] 8.5 Add Switchboard routing-boundary tests. `[Spec: butler-switchboard / Explicit Chronicler Routing Boundary]`
