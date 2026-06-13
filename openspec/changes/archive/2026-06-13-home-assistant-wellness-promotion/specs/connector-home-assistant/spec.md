# Connector Home Assistant — Wellness Promotion Delta

## ADDED Requirements

### Requirement: Wellness channel promotion for health-shaped sensor events

The connector SHALL classify each `state_changed` event that survives the
three-layer filter pipeline against a deterministic, metadata-driven wellness
rule table (matching on `device_class`, `unit_of_measurement`, and optional
entity-id tokens — never vendor or integration names). Events matching a rule
SHALL be emitted on the `wellness` channel with
`source.provider = "home_assistant"` IN ADDITION TO the unchanged
`home_assistant` channel emission. Classification SHALL involve no LLM call.

#### Scenario: Blood-pressure reading promoted

- **WHEN** a `state_changed` event for an entity with
  `unit_of_measurement = "mmHg"` and entity_id containing the token
  `systolic` survives the filter pipeline
- **THEN** the connector SHALL emit the usual `home_assistant`-channel envelope
- **AND** SHALL additionally emit a `wellness`-channel envelope with
  `source.provider = "home_assistant"`, the same `external_event_id`
  (`ha:{entity_id}:{time_ms}`), and a
  `payload.raw.wellness_measurement` object containing `metric`
  (`blood_pressure_systolic`), numeric `value`, `unit`, `valid_at`
  (the event's `time_fired`), and `source_entity_id`
- **AND** `payload.normalized_text` SHALL be a human-readable rendering of the
  measurement

#### Scenario: Non-health sensor not promoted

- **WHEN** a `state_changed` event matches no wellness rule (e.g. a
  `device_class = "temperature"` room sensor, which is deliberately absent
  from the default rule table)
- **THEN** the connector SHALL emit only the `home_assistant`-channel envelope
- **AND** SHALL NOT emit on the `wellness` channel

#### Scenario: Non-numeric state not promoted

- **WHEN** an entity matches a wellness rule but its new state is non-numeric
  (`unknown`, `unavailable`, or unparseable)
- **THEN** the connector SHALL NOT emit a `wellness`-channel envelope for that
  event
- **AND** SHALL count the skip in the classifier metrics

#### Scenario: New health sensor requires no configuration

- **WHEN** a new HA entity appears whose metadata matches an existing wellness
  rule (e.g. a second blood-pressure cuff from a different vendor)
- **THEN** its readings SHALL be promoted with no connector configuration,
  code, or restart required beyond the entity existing in HA

### Requirement: Wellness classifier configuration

The classifier SHALL be configured via environment variables consistent with
the connector's existing config surface: `HA_WELLNESS_PROMOTION_ENABLED`
(default `true`), `HA_WELLNESS_RULES_EXTRA` (JSON list of
`{device_class?, unit?, entity_token?, metric}` rules appended to the default
table), and `HA_WELLNESS_ENTITY_DENYLIST` (comma-separated entity_ids never
promoted). The default rule table SHALL cover blood pressure
(systolic/diastolic via `mmHg` + token), weight (`weight` device_class or
`kg`/`lb` with weight device_class), heart rate (`bpm`), blood glucose
(`mg/dL`, `mmol/L`), and steps (`steps`); it SHALL NOT include ambient-ambiguous
signatures (temperature, humidity, bare `%`).

#### Scenario: Promotion disabled

- **WHEN** `HA_WELLNESS_PROMOTION_ENABLED=false`
- **THEN** the connector SHALL emit no `wellness`-channel envelopes
- **AND** `home_assistant`-channel behavior SHALL be unchanged

#### Scenario: Owner extends the rule table

- **WHEN** `HA_WELLNESS_RULES_EXTRA` contains
  `[{"entity_token": "spo2", "unit": "%", "metric": "spo2"}]` and a matching
  entity reports a numeric state
- **THEN** the connector SHALL promote that reading with
  `metric = "spo2"`

#### Scenario: Denylisted entity never promoted

- **WHEN** an entity_id appears in `HA_WELLNESS_ENTITY_DENYLIST`
- **THEN** its events SHALL never be emitted on the `wellness` channel even if
  they match a rule

### Requirement: Promotion observability

The connector SHALL export Prometheus counters for classifier outcomes
(promoted, skipped-non-numeric, denylisted) labeled by `metric`, and the
existing per-envelope submission metrics SHALL distinguish the two channels.

#### Scenario: Promoted reading visible in metrics

- **WHEN** a reading is promoted to the `wellness` channel
- **THEN** the promotion counter for its `metric` label SHALL increment
- **AND** the envelope submission metrics SHALL attribute the submission to the
  `wellness` channel

### Requirement: Checkpoint unification across channels

The connector checkpoint SHALL remain keyed by `(provider, endpoint_identity)`
only. One HA event SHALL advance the checkpoint exactly once, after all of its
channel emissions have been submitted.

#### Scenario: Replay after dual emission

- **WHEN** the connector restarts and replays events already submitted on both
  channels
- **THEN** re-submissions SHALL be deduplicated by the Switchboard
  per-channel dedup key
- **AND** no duplicate ingestion events SHALL be recorded on either channel
