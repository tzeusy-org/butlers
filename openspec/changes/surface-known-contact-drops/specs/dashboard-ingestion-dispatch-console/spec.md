## ADDED Requirements

### Requirement: Filters Opener Surfaces Drops From Known Contacts

The Filters verdict opener SHALL report outstanding positive known-contact drops from `GET /api/ingestion/events/dropped-known` as a link to the existing filtered-events door. Positive counts SHALL remain lower bounds when another source is unavailable. An unavailable or failed aggregate SHALL render "gate harm unknown" together with any readable positive count and SHALL NOT render the all-clear line. Loading SHALL remain an accessible loading state without an all-clear. Overall availability SHALL require a readable filtered-event window and a successful authoritative registry/classification read proving every applicable Gmail runtime has fresh admitted classification. Only executable runtime-instance rows with current heartbeat/instance/classification evidence may establish availability; checkpoint, archived, deleted or foreign connector rows SHALL NOT provide that authority, and an unclassified Gmail row SHALL remain unknown. Missing, legacy, invalid or stale evidence and total/partial read failure SHALL remain unavailable. True no-applicable and successful loaded-empty states SHALL be available only after positive authoritative reads. The aggregate SHALL preserve positive counts over unanswered marked drops (`filtered` or `replay_failed`) while treating uncertain Gmail drops in the requested 1h/24h/7d window as incomplete; a later successful refresh SHALL NOT retroactively certify them. Queuing replay SHALL NOT erase uncertainty; an existing successful replay outcome or departure from the requested window may resolve it. Filtered-store failure SHALL return HTTP 200 with unavailable and zero counts, explicitly marked unreadable rather than a truthful empty result. Historical classification SHALL be evaluated at its captured drop-time UTC independently of provider dates or delayed flush; received_at SHALL still select the requested window. Successful local queries or loaded historical rows SHALL NOT replace missing, unknown or expired current admission; an entirely unobserved failed publication SHALL NOT be claimed as immediate server knowledge while prior admitted evidence is still fresh within the300-second heartbeat and900-second classification bounds.

ID: REQ-dashboard-ingestion-dispatch-console-003
Source: bu-q7vx1q.43 criteria 2/3/4; surface-known-contact-drops; docs/api_and_protocols/response-conventions.md
Scope: v1-mandatory

#### Scenario: Known-contact drops are named with a door

- **WHEN** the aggregate reports 3 outstanding drops
- **THEN** the opener renders "3 dropped from people you know" linking to filtered events
- **AND** no all-clear line is rendered

#### Scenario: Unavailable aggregate is unknown, not clear

- **WHEN** the aggregate request fails or returns `available=false`
- **THEN** the opener renders "gate harm unknown"
- **AND** no all-clear line is rendered

#### Scenario: Aggregate read failure degrades honestly

- **WHEN** the filtered-event read raises on the server
- **THEN** the endpoint returns HTTP 200 with `available=false` and zero counts
- **AND** the counts are marked unreadable and do not establish a truthful empty result

#### Scenario: Historical uncertain drops survive recovery

- **WHEN** an unavailable-classification Gmail drop is persisted and the current account later loads successfully
- **THEN** the window remains unavailable while that unresolved drop is in range
- **AND** readable positive known-drop counts remain visible as lower bounds
- **AND** a loaded historical row after query success with failed publication cannot certify unknown current admission or rewrite that earlier uncertain row

#### Scenario: Partial availability preserves positives

- **WHEN** the filtered count read succeeds with a positive count while registry/classification evidence fails or is incomplete
- **THEN** the endpoint preserves the positive count but sets overall availability false
- **AND** the opener renders both the existing count door and "gate harm unknown"

#### Scenario: All applicable accounts must be available

- **WHEN** one applicable Gmail runtime is unloaded, stale, legacy or missing valid capabilities among otherwise loaded accounts
- **THEN** the complete aggregate is unavailable
- **AND** excluded checkpoint/archived/deleted/foreign rows cannot manufacture availability

#### Scenario: Authoritative absence and loaded empty have positive controls

- **WHEN** a successful authoritative registry read proves no applicable Gmail runtime or all applicable accounts are loaded-empty with fresh admitted evidence
- **THEN** a readable zero-drop and zero-uncertainty window is available
- **AND** only that proven healthy zero may produce the calm opener

#### Scenario: Legacy and malformed historical drops are uncertain

- **WHEN** an unanswered Gmail row has absent, legacy or malformed classification metadata
- **THEN** it does not prove the historical window complete
- **AND** any valid positive marker still contributes its lower-bound count

#### Scenario: Loading never reads as calm

- **WHEN** the actual dropped-known query is loading before usable evidence exists
- **THEN** the opener exposes its accessible loading state
- **AND** the all-clear line is absent

#### Scenario: Replay queue does not certify a drop

- **WHEN** an uncertain Gmail drop changes to replay_pending without a successful replay
- **THEN** window classification remains unavailable
- **AND** only an existing replay_complete outcome removes that unresolved drop from uncertainty
