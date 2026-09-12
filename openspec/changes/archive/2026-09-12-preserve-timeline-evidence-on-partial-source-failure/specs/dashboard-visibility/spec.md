## ADDED Requirements

### Requirement: Timeline partial-source evidence

The Timeline SHALL preserve every event returned by reachable sources while
making any unavailable Timeline subread explicit. Its response metadata SHALL
retain the existing `degraded_sources: string[]` contract and SHALL
additively expose `degraded_butlers: string[]` for named failed session
fan-out pools. A non-empty degraded list means the displayed evidence is
partial and SHALL NOT be described as a complete fleet history, a genuine
empty state, or an exhausted historical boundary.

#### Scenario: Partial session fan-out names failed butlers

- **WHEN** the Timeline session fan-out succeeds for at least one requested
  butler and fails for one or more other requested butlers
- **THEN** the response preserves events from reachable butlers
- **AND** `meta.degraded_sources` retains `sessions`
- **AND** `meta.degraded_butlers` names the failed session pools
- **AND** the Timeline renders the generic partial-source state plus the named
  unavailable butlers without claiming a complete fleet history

#### Scenario: Failed butler facets remain unavailable rather than empty

- **WHEN** the Timeline's butler-facet reader fails
- **THEN** the Timeline renders a named butler-facet-unavailable state with a
  retry control
- **AND** it SHALL NOT render "No butlers available" as though the failed
  reader completed successfully
- **AND** Timeline rows, source facets, and other reachable controls remain
  usable

#### Scenario: Failed saved-view reader remains unavailable rather than empty

- **WHEN** the Timeline's custom saved-view reader fails
- **THEN** the built-in views and current Timeline filters remain usable
- **AND** the page renders a named saved-views-unavailable state with a retry
  control
- **AND** it SHALL NOT describe the failed reader as having no custom saved
  views

#### Scenario: Failed Load older retries the same historical boundary

- **WHEN** the operator requests an older Timeline page and that request fails
- **THEN** the already rendered events remain visible
- **AND** the Timeline renders a named older-page-unavailable state with a
  retry control
- **AND** the retry sends the same cursor as the failed request
- **AND** the Timeline SHALL NOT advance or erase the cursor, claim the end of
  history, or replace the committed snapshot with a live-head refresh

### Requirement: Pinned session error excerpt states

The Sessions pinned strip SHALL distinguish the bounded session-detail query
state for each recent failed session. A detail read that is loading, fails, or
succeeds with a null error field SHALL be visibly distinct; an unavailable
detail is not evidence that the session has no error detail.

#### Scenario: Loading error excerpt is not presented as null detail

- **WHEN** a pinned recent-failure detail query is pending
- **THEN** that row identifies its error detail as loading
- **AND** it SHALL NOT render "no error detail" before a successful response

#### Scenario: Failed error excerpt offers row-local retry

- **WHEN** one pinned recent-failure detail query fails
- **THEN** its row remains visible with a named unavailable-detail state
- **AND** that row offers a keyboard-operable retry control for its own detail
  query
- **AND** other pinned rows retain their independent loaded or loading states

#### Scenario: Loaded null error detail remains an honest known-null value

- **WHEN** a pinned recent-failure detail query succeeds and its `error` field
  is null
- **THEN** that row renders the known-null "no error detail" state
- **AND** it does not render a loading or unavailable state
