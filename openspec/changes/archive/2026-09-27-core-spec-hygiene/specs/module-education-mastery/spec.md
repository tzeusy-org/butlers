## MODIFIED Requirements

### Requirement: Mastery status state machine

The `mastery_status` column on `mind_map_nodes` SHALL follow the state machine defined by module-education-mind-map "Mastery status state machine", which is the single source for the set of permitted transitions. The `mastery_record_response()` function MUST evaluate and apply the appropriate state transition after every quiz response, and it applies only these response-driven transitions from that set:

- `unseen` → `diagnosed`: after a diagnostic response is recorded for the node
- `unseen` → `learning`: when a `teach` response is recorded and no prior diagnostic response exists
- `diagnosed` → `learning`: when a `teach` response is recorded, OR when a quiz response reveals poor understanding (quality < 3) on a node in `diagnosed` status (self-correction)
- `learning` → `reviewing`: when a quiz response has `quality >= 3` on a node currently in `learning` status
- `reviewing` → `mastered`: when the mastery threshold is met (see mastery threshold requirement)
- `reviewing` → `learning`: when a quiz response has `quality < 3` on a node currently in `reviewing` status (regression)

#### Scenario: Unseen node transitions to diagnosed on diagnostic response

- **WHEN** a node has `mastery_status = 'unseen'`
- **AND** `mastery_record_response()` is called with `response_type="diagnostic"`
- **THEN** the node's `mastery_status` MUST be updated to `'diagnosed'`

#### Scenario: Unseen node transitions to learning on teach response without prior diagnostic

- **WHEN** a node has `mastery_status = 'unseen'` and has no recorded `diagnostic` responses
- **AND** `mastery_record_response()` is called with `response_type="teach"`
- **THEN** the node's `mastery_status` MUST be updated to `'learning'`

#### Scenario: Diagnosed node transitions to learning on teach response

- **WHEN** a node has `mastery_status = 'diagnosed'`
- **AND** `mastery_record_response()` is called with `response_type="teach"`
- **THEN** the node's `mastery_status` MUST be updated to `'learning'`

#### Scenario: Learning node transitions to reviewing on successful quiz

- **WHEN** a node has `mastery_status = 'learning'`
- **AND** `mastery_record_response()` is called with `quality=3`
- **THEN** the node's `mastery_status` MUST be updated to `'reviewing'`

#### Scenario: Learning node remains learning on failed quiz

- **WHEN** a node has `mastery_status = 'learning'`
- **AND** `mastery_record_response()` is called with `quality=2`
- **THEN** the node's `mastery_status` MUST remain `'learning'`

#### Scenario: Reviewing node regresses to learning on failed review

- **WHEN** a node has `mastery_status = 'reviewing'`
- **AND** `mastery_record_response()` is called with `quality=2`
- **THEN** the node's `mastery_status` MUST be updated to `'learning'`

#### Scenario: Reviewing node remains reviewing on successful review below mastery threshold

- **WHEN** a node has `mastery_status = 'reviewing'`
- **AND** `mastery_record_response()` is called with `quality=4`
- **AND** the mastery threshold conditions are NOT yet met
- **THEN** the node's `mastery_status` MUST remain `'reviewing'`

#### Scenario: Mastered node is not regressed

- **WHEN** a node has `mastery_status = 'mastered'`
- **AND** `mastery_record_response()` is called with `quality=1`
- **THEN** the node's `mastery_status` MUST remain `'mastered'` (mastered nodes are not demoted via this mechanism)

#### Scenario: Diagnosed node does not skip to reviewing

- **WHEN** a node has `mastery_status = 'diagnosed'`
- **AND** `mastery_record_response()` is called with `response_type="review"` and `quality=5`
- **THEN** the node's `mastery_status` MUST NOT be set to `'reviewing'` or `'mastered'`
- **AND** the transition to `reviewing` MUST only occur after the node passes through `learning`
