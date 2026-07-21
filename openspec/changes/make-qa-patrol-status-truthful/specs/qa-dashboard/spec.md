## ADDED Requirements

### Requirement: QA Patrol Status Semantics

The QA dashboard SHALL treat the existing `public.qa_patrols.status` vocabulary
as an explicit cross-layer contract. The canonical accepted values are `running`,
`clean`, `findings_dispatched`, `error`, `skipped_overlap`, and `suppressed`; this
change SHALL NOT add a status value or alter how the QA patrol loop chooses one.

#### Scenario: Canonical patrol-status filter validation

- **WHEN** an operator requests `GET /api/qa/patrols` with `status` equal to one of
  the six canonical values
- **THEN** the API accepts the filter and returns matching patrol records
- **AND** when `status` is any other value, the API returns HTTP 422 naming the
  rejected value and the canonical vocabulary

#### Scenario: Persisted unknown status remains observable

- **WHEN** a patrol list or patrol-detail read returns a persisted status outside
  the canonical vocabulary
- **THEN** the API preserves that raw response value for read-only presentation
- **AND** it SHALL NOT coerce that value to `clean`, reject the whole response, or
  mutate the patrol record

#### Scenario: Summary derives an explicit unknown-status condition

- **WHEN** `GET /api/qa/summary` selects a latest completed patrol whose persisted
  `status` is outside the canonical vocabulary
- **THEN** `last_patrol.status` preserves that raw value for forensic API consumers
- **AND** `staffer_status` is `unknown_patrol_status`, not `healthy`, `unknown`, or
  `error`
- **AND** the endpoint SHALL NOT add or normalize a persisted patrol status or change
  patrol-dispatch policy

#### Scenario: Total overview status presentation

- **WHEN** the QA overview renders a recent patrol
- **THEN** its status dot and accessible patrol-link name use these semantics:
  - `clean`: label `clean`, healthy green
  - `findings_dispatched`: label `findings dispatched`, amber attention
  - `suppressed`: label `findings suppressed`, amber warning
  - `error`: label `patrol error`, destructive red
  - `running`: label `patrol running`, explicit muted non-success
  - `skipped_overlap`: label `patrol skipped due to overlap`, explicit muted
    non-success
- **AND** no supported non-clean status uses the healthy green token

#### Scenario: Unknown patrol status fails closed in the overview

- **WHEN** the QA overview receives a future, malformed, or corrupt patrol-status
  value
- **THEN** it renders the label `unknown patrol status` with a destructive status
  dot
- **AND** it SHALL NOT render healthy green or a clean label

#### Scenario: Patrol detail uses the same human status label

- **WHEN** an operator opens a QA patrol detail
- **THEN** its metadata caption renders the same human-readable status label as
  the overview mapping for every canonical value
- **AND** an unknown persisted value renders `unknown patrol status`, not the raw
  storage identifier or a clean label

#### Scenario: QA butler patrol cadence uses the total status presentation

- **WHEN** an operator opens the QA butler detail's recent-patrol cadence stripe
- **THEN** each status badge renders the same human-readable label as the shared
  patrol-status mapping for every canonical value
- **AND** only `clean` is green; `findings_dispatched` and `suppressed` use amber
  attention semantics; `error` and an unknown persisted value are destructive; and
  `running` and `skipped_overlap` remain explicit muted non-success states
- **AND** an unknown persisted value renders `unknown patrol status`, never its raw
  storage identifier or a healthy presentation

#### Scenario: Status meaning is accessible without motion

- **WHEN** an operator reaches a recent-patrol link by keyboard or assistive
  technology
- **THEN** its accessible name contains the human status label and finding count,
  while the visual dot remains decorative
- **AND** status changes use no added animation or pulse, so reduced-motion users
  receive the same immediate state information

#### Scenario: Polling remains presentation-only

- **WHEN** dashboard polling returns a newer patrol row while another patrol is
  running or completing
- **THEN** the overview and detail render the latest returned status through the
  same mapping
- **AND** neither view changes patrol status, dispatches work, or changes
  suppression policy
