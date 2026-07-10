# Chronicler API — Spec delta for chronicler-gap-interview-transport

## MODIFIED Requirements

### Requirement: Chronicler Corrections

The API SHALL support owner corrections by creating override or superseding
records without mutating source evidence.

The initial correction endpoint set SHALL include:

- `POST /api/chronicler/episodes/{episode_id}/corrections`
- `POST /api/chronicler/gap-interview/resolve`

`POST /api/chronicler/gap-interview/resolve` is a connector-facing internal
endpoint that applies one one-tap day-close gap-interview answer. It SHALL
delegate to the shared gap-interview resolver so its override/reinforce write
shape and idempotency are identical to the `chronicler_resolve_gap_interview`
MCP tool that calls the same resolver. It exists because the `telegram_bot`
connector runs as the restricted `connector_writer` role and cannot write the
chronicler schema itself; this endpoint runs with the chronicler pool that can.

#### Scenario: Episode correction submitted

- **WHEN** the owner submits a correction for an episode type, title, start time,
  end time, or explanatory note
- **THEN** the API SHALL create an override or superseding derived record
- **AND** it SHALL retain the original source evidence and original derived
  record for audit
- **AND** corrected read views SHALL prefer the active correction
- **AND** the request body SHALL allow only correction fields owned by
  Chronicler: corrected type, title, start time, end time, note, and active
  status

#### Scenario: Correction policy inherited

- **WHEN** a correction is created for an episode or event
- **THEN** the correction record SHALL inherit the stricter effective privacy,
  precision, and retention policy from the target record and source contract

#### Scenario: Correction history returned

- **WHEN** a client requests correction history for an episode
- **THEN** the API SHALL return the ordered correction audit trail without
  exposing source fields that have been tombstoned or precision-reduced

#### Scenario: Invalid correction rejected

- **WHEN** a correction request has invalid timestamps, attempts to change source
  evidence, or violates privacy/retention policy
- **THEN** the API SHALL reject it with a structured `400` response
- **AND** it SHALL NOT create a partial override record

#### Scenario: Gap-interview one-tap answer resolved

- **WHEN** a connector (or the `chronicler_resolve_gap_interview` MCP tool)
  submits a one-tap day-close gap-interview answer to
  `POST /api/chronicler/gap-interview/resolve` with an `interview_id` and an
  `answer` of `confirm`, `correct`, or `dismiss`
- **THEN** the API SHALL delegate to the shared gap-interview resolver so the
  override/reinforce write is identical across the HTTP endpoint and the MCP
  tool
- **AND** the resolution SHALL be idempotent — a duplicate tap for an
  already-answered interview SHALL NOT write a second override or re-nudge the
  routine
- **AND** the endpoint SHALL always return HTTP `200` with a `status` field the
  caller can surface as a toast (`applied`, `already_answered`, or `error`)
- **AND** an unknown or expired `interview_id`, or an unparseable `answer`,
  SHALL return `200` with an `error` status rather than raising a server error

## Source References

- Non-Negotiable Rule 1 (single-owner data sovereignty)
- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 4 (LLM reasoning is ephemeral)
- RFC 0007 (Dashboard and API Surface)
- RFC 0014 (Chronicler Time Butler) §D7 API Surface
