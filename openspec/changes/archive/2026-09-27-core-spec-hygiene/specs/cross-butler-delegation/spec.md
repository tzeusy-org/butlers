## MODIFIED Requirements

### Requirement: Discoverability
Every delegation-ledger row SHALL be discoverable outside the asking and
answering butlers' own sessions, without a per-butler fan-out (the ledger is
one shared `public` table reachable from any pool).

#### Scenario: List recent delegations
- **WHEN** a caller requests `GET /api/delegation/ledger` with optional
  `status`, `asking_butler`, or `target_butler` filters
- **THEN** matching rows are returned most-recent-first with pagination
  metadata, without fanning out to multiple butler pools

#### Scenario: Fetch one delegation by id
- **WHEN** a caller requests `GET /api/delegation/ledger/{id}` for an
  existing row
- **THEN** the full row (including `answer` once present) is returned

#### Scenario: Unknown id is a 404, not an empty success
- **WHEN** a caller requests `GET /api/delegation/ledger/{id}` for an id with
  no matching row
- **THEN** the response is `404 Not Found`, never a `200` with null/empty
  data standing in for "not found"

#### Scenario: Wake-protocol fields are discoverable, not just the answer
- **WHEN** `GET /api/delegation/ledger` or `GET /api/delegation/ledger/{id}`
  returns a row
- **THEN** the response includes `wake_state`, `wake_key`, `wake_task_id`,
  `wake_task_name`, `wake_updated_at`, and `answer_digest` alongside the
  existing fields, so `callback_failed` and `task_conflict` -- the two
  failure states the wake protocol introduces -- are distinguishable from an
  ordinary answered row over the API, not only via direct database access
- **AND** a row with no v1 wake provenance defaults `wake_state` to
  `"not_applicable"` rather than omitting the field

#### Scenario: Filtering to stuck wake states without a fleet-wide scan
- **WHEN** a caller requests `GET /api/delegation/ledger?wake_stuck=true`
- **THEN** only rows whose `wake_state` is `callback_failed` or
  `task_conflict` are returned, combinable with the existing `status`,
  `asking_butler`, and `target_butler` filters

#### Scenario: Delegation rows are visible on butler detail and the attention surface
- **WHEN** the dashboard renders a butler's detail page or the Overview
  attention list
- **THEN** the butler detail page shows delegated-out and delegated-in rows
  for that butler, with a visually distinct badge for `callback_failed` and
  `task_conflict` rows
- **AND** any fleet-wide row stuck in `callback_failed` or `task_conflict`
  surfaces on the Overview attention list, deep-linking to the asking
  butler's detail page
- **AND** a failed fetch of stuck delegations renders a named degraded
  notice, never a silent "nothing stuck" all-clear
