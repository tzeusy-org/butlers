## ADDED Requirements

### Requirement: Memory Reads Preserve Source Uncertainty

The dashboard memory details, registers, and unified inspect search SHALL
render completed absence only when the API has established it. A failed or
degraded source MUST remain visible to the owner rather than being translated
into a calm empty, not-found, or all-clear state.

#### Scenario: Detail pages distinguish 404 from named 503

- **WHEN** EpisodeDetailPage, FactDetailPage, or RuleDetailPage receives a
  completed 404 query result
- **THEN** it MAY render that page's existing not-found presentation
- **WHEN** it receives a 503 with named unavailable memory sources or any other
  query failure
- **THEN** it MUST render a visible error state with the available source
  context and a retry affordance
- **AND** it MUST NOT render the 404 wording or assert that the record is
  absent

#### Scenario: Registers show named degradation beside partial or empty data

- **WHEN** an episode, fact, or rule register response carries non-empty
  `meta.pools_failed`
- **THEN** the register MUST render the named `SourceDegradedNote` vocabulary
  inline with its result state
- **AND** it MUST render that note even when the current page has zero rows
- **AND** it MUST retain the normal true-empty presentation only when the
  completed response has no degraded sources and no matching rows

#### Scenario: Search uses the query boundary rather than a false empty verdict

- **WHEN** a submitted unified memory inspect search is loading
- **THEN** SearchResults MUST use the shared query-boundary loading state and
  MUST NOT render "Nothing in the books."
- **WHEN** that search fails
- **THEN** SearchResults MUST use the query-boundary error state with retry and
  MUST NOT render a no-match verdict
- **WHEN** a completed healthy search has no results
- **THEN** it MUST render the existing no-match wording

#### Scenario: Exact paging is URL-backed and visibly qualified when degraded

- **WHEN** a register or unified inspect search renders a completed page
- **THEN** its range and next/previous availability MUST derive from API
  `meta.total`, `meta.offset`, `meta.limit`, and `meta.has_more`, not from the
  current row count
- **AND** search MUST preserve the existing URL-backed `q` and `kind` state
  while changing `offset` for the next or previous page
- **AND** registers and inspect search MUST consume `meta.pools_failed` through
  typed `MemoryPaginationMeta`, not an unsafe cast or untyped metadata access
- **WHEN** `meta.pools_failed` is non-empty
- **THEN** any displayed total/range MUST identify it as records available from
  reachable sources and appear with the named degradation note
- **AND** it MUST NOT claim an unqualified all-memory total

### Requirement: Accessible Owner Dead-Letter Recovery Control

The episode dossier SHALL surface recovery-safe lifecycle evidence and one
bounded requeue control for an owner-visible dead-letter episode. The control
is a queueing affordance, not an execution control, and client visibility does
not replace server-side owner authorization.

#### Scenario: Episode dossier shows approved lifecycle evidence only

- **WHEN** EpisodeDetailPage renders an episode with consolidation lifecycle
  data
- **THEN** it MUST present the available attempts, sanitized last consolidation
  error, dead-letter reason, and next retry time as understandable dossier
  evidence
- **AND** it MUST NOT render lease holder, lease deadline, raw exception
  traces, runtime output, prompts, or credentials

#### Scenario: Owner can queue only a dead-letter episode

- **WHEN** the dashboard owner views an episode whose
  `consolidation_status='dead_letter'`
- **THEN** the dossier MUST expose one semantic button named to queue the
  episode for the next scheduled write-up
- **AND** the control MUST be keyboard-operable with visible focus and be
  unavailable for `pending`, `failed`, and `consolidated` episodes
- **WHEN** the viewer is not in the owner-authorized dashboard lane
- **THEN** the control MUST be omitted or disabled without implying that the
  client is the authorization authority

#### Scenario: Requeue landing state is explicit and accessible

- **WHEN** the owner activates the dead-letter requeue control
- **THEN** the control MUST expose an in-progress state that prevents duplicate
  submission without moving focus unexpectedly
- **WHEN** the API returns 200
- **THEN** a local announced status MUST state that the episode was queued for
  a future scheduled write-up and that no write-up started now
- **WHEN** the API returns 409, 503, or another failure
- **THEN** a local announced error MUST preserve enough context to retry or
  refresh the dossier and MUST NOT claim the episode was queued
- **AND** success or error announcements MUST use a single appropriate live
  region without duplicate screen-reader announcements

#### Scenario: Recovery UI has no run-now or bulk affordance

- **WHEN** the episode dossier renders recovery controls
- **THEN** it MUST NOT offer a run-now action, a schedule trigger, a bulk
  requeue affordance, or an MCP recovery action
- **AND** activity, entities, and re-embedding surfaces MUST remain outside
  this recovery UI's scope

### Requirement: Fact Commit Footer Gives Accessible Mutation Feedback

The Fact detail commit footer SHALL retain its existing Confirm and Retract
semantics while visibly and accessibly communicating the outcome of each
mutation. An optimistic UI update is not itself a success message.

#### Scenario: Confirm reports a successful or failed landing state

- **WHEN** the owner confirms a fact and the mutation succeeds
- **THEN** the footer MUST announce a concise local confirmation success tied
  to the initiating control
- **WHEN** the confirm mutation fails after an optimistic update
- **THEN** the footer MUST restore the pre-mutation data, announce an actionable
  failure, and leave Confirm available to retry

#### Scenario: Retract reports a successful or failed landing state

- **WHEN** the owner completes the existing Retract confirmation and the
  mutation succeeds
- **THEN** the footer MUST announce a concise local retraction success tied to
  the initiating control
- **WHEN** the retract mutation fails after an optimistic update
- **THEN** the footer MUST restore the pre-mutation data, announce an actionable
  failure, and leave the existing retraction flow retryable

#### Scenario: Mutation messages remain operable without sight

- **WHEN** either fact mutation resolves
- **THEN** its success or failure feedback MUST use a named status or alert
  region, preserve visible keyboard focus, and avoid duplicate announcements
- **AND** color alone MUST NOT be the only indication of the result
