# Chronicler Source Compatibility

## Purpose

Defines the contract future timestamped sources must provide so Chronicler can
project lived-time evidence without bespoke LLM interpretation or ad hoc
cross-schema access.

## ADDED Requirements

### Requirement: Timestamped Source Compatibility Declaration

Every new timestamped source proposal SHALL declare Chronicler compatibility or
explicitly state that it is not time-bearing.

#### Scenario: Time-bearing source declares compatibility

- **WHEN** a future source such as Fitbit proposes workout, sleep, heart-rate, or step evidence
- **THEN** its proposal/spec SHALL include a `chronicler_compatibility` section
- **AND** that section SHALL define source name, source kind, supported outputs, time fields, boundary semantics, source reference format, taxonomy mapping, confidence semantics, privacy tier, idempotency key, and projection path

#### Scenario: Non-time-bearing source opts out

- **WHEN** a proposed source carries no meaningful observed or effective time evidence
- **THEN** its proposal/spec SHALL state that it is not time-bearing
- **AND** no Chronicler adapter SHALL be required for that source

### Requirement: Compatibility Fields

The Chronicler compatibility declaration SHALL provide the fields Chronicler
needs for deterministic projection.

#### Scenario: Required fields present

- **WHEN** a compatibility declaration is reviewed
- **THEN** it SHALL include:
  - `source_name`
  - `source_kind`
  - `supported_outputs`
  - `time_fields`
  - `boundary_semantics`
  - `source_ref_format`
  - `taxonomy_mapping`
  - `confidence_semantics`
  - `privacy_tier`
  - `idempotency_key`
  - `projection_path`

#### Scenario: Projection path selected

- **WHEN** a compatibility declaration sets `projection_path`
- **THEN** the value SHALL be either `canonical_evidence` or `chronicler_adapter`
- **AND** `chronicler_adapter` SHALL mean Chronicler owns a deterministic adapter reading an approved source surface
- **AND** `canonical_evidence` SHALL NOT be used until a separate RFC/spec defines shared table ownership, write authority, ACLs, provenance, retention, and migration contract

#### Scenario: Concurrent source proposal predates Chronicler acceptance

- **WHEN** a timestamped source proposal predates acceptance of RFC 0014
- **THEN** that source SHALL be listed for compatibility retrofit or explicit deferral before Chronicler claims it as an adapter source

### Requirement: Privacy and Retention Declaration

Compatibility declarations SHALL specify how source privacy and retention apply
to Chronicler projections.

#### Scenario: Sensitive source includes retention policy

- **WHEN** a source declares `privacy_tier = sensitive`
- **THEN** the declaration SHALL specify raw evidence retention, projected evidence retention, allowed precision after source purge, and tombstone behavior

#### Scenario: Source purge behavior defined

- **WHEN** source records may expire or be deleted
- **THEN** the compatibility declaration SHALL define whether Chronicler deletes, tombstones, or lower-precision-retains derived records

### Requirement: Deterministic Projection Compatibility

Compatible sources SHALL expose enough structured evidence for routine
projection without LLM interpretation.

#### Scenario: Source supports episode projection

- **WHEN** a source declares `supported_outputs = episodes` or `both`
- **THEN** it SHALL expose started-at semantics, ended-at semantics or closure policy, boundary confidence, and stable idempotency keys

#### Scenario: Source supports event projection

- **WHEN** a source declares `supported_outputs = events` or `both`
- **THEN** it SHALL expose observed/effective timestamp semantics, event type mapping, source reference, privacy tier, and stable idempotency keys

#### Scenario: Source lacks deterministic evidence

- **WHEN** a source cannot expose enough structured evidence for deterministic projection
- **THEN** it SHALL NOT be marked Chronicler-compatible
- **AND** any future Chronicler support SHALL require a separate source contract change

## Source References

- Non-Negotiable Rule 1 (single-owner data sovereignty)
- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 7 (transport is connector responsibility)
- RFC 0003 (Switchboard routing and ingestion)
- RFC 0006 (Database schema isolation)
- RFC 0010 (Cross-Butler Briefing Exception)
- RFC 0014 (Chronicler Time Butler, Draft)
