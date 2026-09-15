## ADDED Requirements

### Requirement: Machine-class Timeline presentation

The Timeline API SHALL attach a presentation-only `machine_class` of `owner`,
`heartbeat`, or `maintenance` to every event. Session classification SHALL use
only exact structured `trigger_source` values from the reviewed presentation
taxonomy; it SHALL NOT inspect prompt text or classify all `schedule:*` values
as maintenance. The API SHALL retain `is_heartbeat`, set to true exactly when
`machine_class` is `heartbeat`, for compatibility.

#### Scenario: Exact maintenance taxonomy classifies a maintenance session

- **WHEN** a session has an exact reviewed maintenance trigger source such as
  `schedule:consolidation` or `schedule:memory_decay_sweep`
- **THEN** its Timeline event has `machine_class` equal to `maintenance`
- **AND** its safe summary remains derived by the structured summary boundary
- **AND** its legacy `is_heartbeat` value is false

#### Scenario: Unknown or owner-value schedule remains owner activity

- **WHEN** a session has an unknown, malformed, or ordinary scheduled trigger
  source, including a suffix of a known maintenance source
- **THEN** its Timeline event has `machine_class` equal to `owner`
- **AND** the session remains visible in the default Timeline lens

#### Scenario: Existing heartbeat compatibility remains intact

- **WHEN** a session has a recognised heartbeat trigger source
- **THEN** its Timeline event has `machine_class` equal to `heartbeat`
- **AND** `is_heartbeat` remains true

### Requirement: Internal maintenance Timeline lens

The Timeline SHALL default to an owner-focused lens that suppresses only
maintenance events whose `data.success` is exactly `true`. A `data.success`
value of `false` SHALL be a failed run; `null` SHALL be a running run; and a
missing or nonboolean value SHALL be unknown. Running and unknown maintenance
events SHALL remain visible in the default lens. The Timeline SHALL offer a
keyboard-operable Internal control with a visible pressed state and accessible
name; `internal=1` SHALL enable the lens. When enabled, the Timeline SHALL
render maintenance events as expandable, per-butler rollups within their hour
group, using only loaded event data for the displayed count. Each expanded run
SHALL present its strict outcome state and SHALL NOT label a running or unknown
run as completed. Failed maintenance sessions SHALL remain visible as errors
when the Internal lens is disabled.

#### Scenario: Default Timeline hides successful maintenance activity

- **WHEN** the Timeline is loaded without `internal=1`
- **THEN** successful maintenance events do not render as ordinary Timeline
  rows
- **AND** owner and heartbeat behavior remains unchanged
- **AND** a failed maintenance event remains visible as an error

#### Scenario: Internal Timeline lens exposes an expandable maintenance rollup

- **WHEN** the operator enables the Internal lens
- **THEN** maintenance events for the same butler and hour render as one
  rollup with their exact loaded-event count and failed-run count
- **AND** the rollup can be expanded by keyboard to inspect its safe event
  summaries

#### Scenario: Running and unknown maintenance remains truthful in both lenses

- **WHEN** a maintenance event has `data.success` equal to `null`, absent, or
  nonboolean
- **THEN** the default Timeline renders it as ordinary activity rather than
  suppressing it
- **AND** an expanded Internal rollup labels `null` as running and absent or
  nonboolean values as unknown, never completed
