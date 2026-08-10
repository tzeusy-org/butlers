## ADDED Requirements

### Requirement: URL-backed stalled approvals lane

The Approvals Trust Console SHALL treat its `state` query parameter as the
source of truth for the rail lane. The default rail SHALL read the waiting
flat approvals state, and `/approvals?state=stalled` SHALL read the existing
flat stalled state whose rows satisfy exactly `status = approved` and
`execution_result = null`. The dashboard SHALL NOT persist `stalled` as an
approval status.

The lane control and its rows SHALL use native keyboard-operable elements,
show visible focus, and expose the active lane semantically. Selecting a
dossier from the stalled lane SHALL retain `state=stalled` in its URL.

#### Scenario: Direct stalled deep link opens the stalled rail

- **WHEN** the owner navigates directly to `/approvals?state=stalled`
- **THEN** the Trust Console requests the flat approvals endpoint with
  `state=stalled`
- **AND** the rail labels and displays only the returned stalled rows rather
  than the waiting queue.

#### Scenario: Direct stalled dossier remains reachable beyond the rail page

- **WHEN** the owner navigates to `/approvals/{id}?state=stalled` and the
  approval is stalled but falls outside the current bounded flat-result page
- **THEN** the Trust Console verifies and displays that stalled dossier via
  its existing detail endpoint
- **AND** it does not render a pending or invalid direct id as a dossier in
  the stalled lane.

#### Scenario: Stalled radar has a truthful drill-down destination

- **WHEN** the flat response reports one or more stalled actions in
  `meta.stalled_count`
- **THEN** the stalled verdict clause is a keyboard-operable link to
  `/approvals?state=stalled`
- **AND** it does not fabricate a link to a particular approval id from the
  aggregate count.

#### Scenario: Stalled lane does not expose pending-decision shortcuts

- **WHEN** the owner is viewing the stalled lane
- **THEN** its rail remains navigable by the existing keyboard movement
  controls
- **AND** it does not register approval, denial, or defer keyboard verbs for
  the selected stalled row.

### Requirement: Safe retry refreshes confirmed approval state

The dashboard SHALL reuse the existing Retry dispatch action only when an
approval has `status = approved` and an explicitly null `execution_result`.
It SHALL NOT render Retry for an executed failure, any non-approved status, or
an unknown/missing execution result. The Retry control SHALL remain pending
without locally removing the row while its server request is in flight.

After a successful server response, the dashboard SHALL invalidate all flat
approval query variants, the affected approval dossier, approval history, and
approval metrics so the count, lane, history, and dossier reconcile from
server-authoritative data. It SHALL not optimistically remove the row or
invalidate those views when the retry request fails.

#### Scenario: Retry stays bounded to an approved action without a result

- **WHEN** a stalled row has `status = approved` and
  `execution_result = null`
- **THEN** the dashboard renders the existing Retry dispatch control
- **AND** when the same row has a non-null or missing execution result, or a
  different status, the dashboard renders no Retry control.

#### Scenario: Confirmed retry reconciles every approval read

- **WHEN** the owner activates Retry dispatch and the server returns a
  successful response
- **THEN** the UI reports whether dispatch ran from that response
- **AND** it invalidates the waiting and stalled flat views, history, the
  affected dossier, and metrics after that completion.

#### Scenario: Failed retry leaves the server-authoritative row visible

- **WHEN** the Retry dispatch request fails
- **THEN** the dashboard reports the returned error without claiming a
  dispatch cause
- **AND** it does not optimistically remove the row or invalidate the
  approval read caches.
