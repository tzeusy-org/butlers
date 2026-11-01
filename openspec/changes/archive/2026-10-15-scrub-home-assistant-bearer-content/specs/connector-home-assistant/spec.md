## MODIFIED Requirements

### Requirement: Filtered Event Persistence

The connector SHALL persist filtered events per the base connector contract.

ID: REQ-connector-home-assistant-001
Source: connector-base-spec Bearer material never persists; bu-svqpzo.11 approved connector-local security correction
Scope: v1-mandatory

#### Scenario: HA-specific filter reasons
- **WHEN** an HA event is filtered
- **THEN** the `filter_reason` SHALL include the filter layer and specifics: `"domain_excluded:<domain>"` for layer 1, `"insignificant_delta:<device_class>:<delta>"` for layer 2, `"discretion_ignore"` for layer 3
- **AND** the `full_payload` SHALL contain the complete HA event data for potential replay, except for bearer material withheld under "HA bearer-content custody"; the connector SHALL NOT restore that material during replay

#### Scenario: Global ingestion policy pre-check for non-person domains
- **WHEN** a non-`person` domain event passes all three local filter layers
- **THEN** the connector SHALL evaluate the shared `global`-scope `IngestionPolicyEvaluator` locally, before calling Switchboard `ingest()`
- **AND** if the resolved action is `skip`, the connector SHALL self-persist the ordinary event to `connectors.filtered_events` with `filter_reason` `"global_rule:skip:<rule_type>"` (per `connector-filtered-events`) and SHALL NOT call `ingest()` for the `home_assistant` channel
- **AND** that ordinary-channel skip SHALL NOT suppress an independently eligible deterministic `wellness` promotion
- **AND** any other resolved action (e.g. `pass_through`) SHALL proceed to `ingest()` unchanged
- **AND** `person` domain events SHALL always proceed to `ingest()` regardless of the global policy, since they feed the presence/history evidence table

## ADDED Requirements

### Requirement: HA bearer-content custody

The Home Assistant connector SHALL apply the existing shared ingestion bearer scrubber to every text-bearing old/new state value and recursively nested attribute value before that content enters the connector's reorder queue, filtering/discretion context, outgoing envelope, filtered-event buffer, person-history table, or wellness submission. This requirement SHALL use the shared detector's existing recognized OTP, reset-link, and magic-link behavior, without HA-specific patterns or aggressive bare-number detection. It SHALL withhold recognized bearer content in `payload.normalized_text`, every duplicate in `payload.raw`, and HA replay submissions before submission; identity, timestamps, numeric values, enum states, channel choice, and event membership SHALL retain their existing semantics. The security exception SHALL override complete-content/raw-fidelity promises only for recognized bearer content from this connector; it SHALL NOT change the shared filtered-event status, transaction, codec, or replay contracts.

ID: REQ-connector-home-assistant-002
Source: about/heart-and-soul/security.md Credentials Never Persist; connector-base-spec Bearer material never persists; bu-svqpzo.11
Scope: v1-mandatory

#### Scenario: Text state and nested attributes reach ingestion safely
- **WHEN** WebSocket or REST-fallback state changes contain a recognized bearer in an old/new state or any nested attribute value
- **THEN** the connector SHALL scrub a copied event before normalization, filtering/discretion, and all downstream content consumers
- **AND** every outgoing ordinary or wellness envelope SHALL withhold the bearer in normalized text and all raw copies
- **AND** the existing detector SHALL also evaluate each available state under its own friendly-name and unit context, and the actual assembled HA transition, before discretion can retain it; its recognized redactions SHALL protect complete matching state/attribute values and lexical copies in quoted or prose echoes, including duplicate raw states, without enabling bare-number detection on unrelated numeric sensors; leading numeric punctuation, internal punctuation, signs, and unit symbols SHALL remain part of a distinct ordinary value, so fractional decimals, decimal amounts and compound identifiers containing selected digits SHALL remain unchanged; projection SHALL use bounded scans or fail closed without retaining raw content
- **AND** the input event, source/event/sender identity, timestamps, idempotency keys, and per-channel dedup semantics SHALL remain unchanged

#### Scenario: Filtered and replayed HA content remains withheld
- **WHEN** an HA event is excluded by its domain, significance, discretion, or ordinary-channel global policy
- **THEN** the HA persistence adapter SHALL withhold recognized bearers before calling the existing filtered-event buffer
- **AND** replay of object or legacy JSON-string payloads SHALL withhold recognized bearers before the submission callback, including normalized text and duplicate raw content
- **AND** the existing statuses, batch bound, row locks, callback ordering, and transaction outcomes SHALL remain unchanged
- **AND** no replay operation SHALL restore a withheld bearer or rewrite historical rows as a backfill

#### Scenario: Person history and recorder recovery share the boundary
- **WHEN** the connector writes an accepted person event to `connectors.home_assistant_history` or emits a recorder-backed wellness measurement
- **THEN** the state and recursive attribute content SHALL already have passed the same shared scrubber
- **AND** the direct person-history and wellness helpers SHALL defend their own content boundary as well
- **AND** entity identity, presence enum, recorded time, numeric measurement, measurement timestamp, and per-entity recovery cursor semantics SHALL be preserved

#### Scenario: Ordinary numeric and enum content stays exact
- **WHEN** HA content contains ordinary readings, enum states, order/date/postal values, or other text that the existing shared detector does not recognize as bearer content
- **THEN** the adapter SHALL preserve those values byte-for-byte in the unchanged envelope and persistence representations
- **AND** no aggressive bare-number mode, allowlist change, notification capability, or new detector SHALL be introduced
- **AND** REST comparison SHALL retain the original transient provider snapshots so two different bearer-bearing state values are not collapsed into one observed event by placeholder equality

#### Scenario: Scrubber refusal cannot expose the event
- **WHEN** shared scrubbing raises while admitting an event or preparing a direct content sink
- **THEN** the affected content SHALL NOT be submitted, buffered, or written to history in raw form
- **AND** event-admission or recorder-recovery refusal SHALL not advance the corresponding checkpoint or high-water mark
- **AND** diagnostics SHALL use fixed content-free messages without raw input, detector exception text, or detector traceback disclosure
- **AND** a replay callback failure SHALL produce the existing `replay_failed` outcome with a fixed content-free error detail; cancellation SHALL retain its existing propagation semantics

#### Scenario: Transport diagnostics do not echo malformed content
- **WHEN** a Home Assistant WebSocket text frame is malformed JSON
- **THEN** the connector SHALL log a fixed warning and skip it without logging a raw frame prefix

#### Scenario: Scope is the connector's event consumers
- **WHEN** the connector applies this bearer-content boundary
- **THEN** it SHALL not change provider authentication, event subscriptions, allowlists, notification support, shared detector policy, historical rows, or other connectors
- **AND** it SHALL not claim that a separate module-owned HA client/cache/snapshot writer or the shared payload/error-custody investigations have been remediated

## Source References

- [Security doctrine](../../../../../../about/heart-and-soul/security.md)
- [Existing shared bearer contract](../../../../../specs/connector-base-spec/spec.md)
- [Approved HA-only design](../../design.md)
