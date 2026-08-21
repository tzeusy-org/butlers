## MODIFIED Requirements

### Requirement: Mind map status update endpoint

The system SHALL expose `PUT /api/education/mind-maps/{mind_map_id}/status`
accepting a JSON body `{"status": "<new_status>"}` where `new_status` is one of
`active`, `completed`, `abandoned`.

`draft` SHALL NOT be an accepted request value: a mind map enters `draft` only
through its creation path, and the dashboard MUST NOT be able to manufacture
one. Read responses SHALL nonetheless be able to carry `status = 'draft'`,
because draft maps are real rows the dashboard lists and can abandon.

The endpoint SHALL call the existing
`mind_map_update_status(pool, mind_map_id, status)` tool function. The endpoint
SHALL return 404 if the mind map does not exist. The endpoint SHALL return 422
if the status value is not one of the three allowed request values.

The endpoint SHALL return 409 when the requested transition is rejected by the
mind map lifecycle rules rather than by request validation — in particular when
the target status is `active` and the mind map has zero nodes, and when the
transition itself is not permitted (for example `draft` → `completed`). The
409 body SHALL state which rule rejected the transition, so the dashboard can
explain the refusal rather than reporting a generic failure. The endpoint MUST
NOT translate a lifecycle rejection into a 200 with an unchanged status.

On success, the endpoint SHALL return the updated mind map object (without
nodes/edges).

#### Scenario: Abandon an active mind map

- **WHEN** a PUT request is made to `/api/education/mind-maps/{id}/status` with body `{"status": "abandoned"}`
- **AND** the mind map exists with status `active`
- **THEN** the response status SHALL be 200
- **AND** the returned mind map object SHALL have `status` equal to `abandoned`

#### Scenario: Abandon a draft mind map

- **WHEN** a PUT request is made to `/api/education/mind-maps/{id}/status` with body `{"status": "abandoned"}`
- **AND** the mind map exists with status `draft` and zero nodes
- **THEN** the response status SHALL be 200
- **AND** the returned mind map object SHALL have `status` equal to `abandoned`

#### Scenario: Re-activate an abandoned mind map

- **WHEN** a PUT request is made to `/api/education/mind-maps/{id}/status` with body `{"status": "active"}`
- **AND** the mind map exists with status `abandoned` and at least one node
- **THEN** the response status SHALL be 200
- **AND** the returned mind map object SHALL have `status` equal to `active`

#### Scenario: Activating a zero-node mind map is refused

- **WHEN** a PUT request is made to `/api/education/mind-maps/{id}/status` with body `{"status": "active"}`
- **AND** the mind map exists with zero nodes
- **THEN** the response status SHALL be 409
- **AND** the response body SHALL state that a curriculum with no concepts cannot be activated
- **AND** the mind map's stored `status` SHALL be unchanged

#### Scenario: Draft is rejected as a request value

- **WHEN** a PUT request is made to `/api/education/mind-maps/{id}/status` with body `{"status": "draft"}`
- **THEN** the response status SHALL be 422

#### Scenario: Invalid status value

- **WHEN** a PUT request is made to `/api/education/mind-maps/{id}/status` with body `{"status": "paused"}`
- **THEN** the response status SHALL be 422

#### Scenario: Mind map not found

- **WHEN** a PUT request is made to `/api/education/mind-maps/{nonexistent-id}/status` with body `{"status": "abandoned"}`
- **THEN** the response status SHALL be 404

---

### Requirement: Curriculum request submission endpoint

The system SHALL expose `POST /api/education/curriculum-requests` accepting a
JSON body `{"topic": "<topic>", "goal": "<optional_goal>"}`.

The `topic` field SHALL be required and non-empty (max 200 characters). The
`goal` field SHALL be optional (max 500 characters).

The endpoint SHALL write a JSON payload
`{"topic": "<topic>", "goal": "<goal>", "requested_at": "<ISO-8601>", "lease_expires_at": "<ISO-8601>"}`
to the education butler's KV state store under key
`pending_curriculum_request`, then trigger the ephemeral session that starts
the curriculum. On success the endpoint SHALL return 202 Accepted with body
`{"status": "pending", "topic": "<topic>"}`.

**The lock is a lease, not a flag.** `lease_expires_at` SHALL be
`requested_at` plus a bounded TTL of 15 minutes. The endpoint SHALL return 409
Conflict only when a `pending_curriculum_request` key is present **and** its
`lease_expires_at` is in the future. A key whose lease has expired SHALL be
treated as absent: the request proceeds and overwrites it. An expired lease
MUST NOT produce a 409.

**Release is the API layer's responsibility, not the session's.** The
dashboard API layer SHALL release the lock when the triggered session
terminates, whatever the outcome — success, tool error, model refusal, timeout,
or exception — as an unconditional release path rather than one conditioned on
what the session did. The release SHALL also run when the session could not be
triggered at all.

The ephemeral session MAY additionally call
`state_delete(key="pending_curriculum_request")` as an early release, and that
call MUST be idempotent. It MUST NOT be the only release path, and the skill
or trigger prompt MUST NOT be relied upon as the release mechanism. An
instruction in a prompt is a request to a language model, not a guarantee; the
owner's ability to submit their next curriculum request MUST NOT depend on
model obedience.

#### Scenario: Submit a new curriculum request

- **WHEN** a POST request is made to `/api/education/curriculum-requests` with body `{"topic": "Python", "goal": "Learn web development with Flask"}`
- **AND** no pending curriculum request exists
- **THEN** the response status SHALL be 202
- **AND** the response body SHALL contain `{"status": "pending", "topic": "Python"}`
- **AND** the KV store SHALL contain key `pending_curriculum_request` with the topic, goal, `requested_at`, and a `lease_expires_at` 15 minutes after `requested_at`

#### Scenario: Submit request without goal

- **WHEN** a POST request is made with body `{"topic": "Linear Algebra"}`
- **AND** no pending curriculum request exists
- **THEN** the response status SHALL be 202
- **AND** the KV store entry SHALL have `goal` set to null

#### Scenario: Duplicate request while one is pending

- **WHEN** a POST request is made to `/api/education/curriculum-requests`
- **AND** a `pending_curriculum_request` key exists whose `lease_expires_at` is in the future
- **THEN** the response status SHALL be 409
- **AND** the response body SHALL indicate a curriculum request is already pending

#### Scenario: Lock is released when the triggered session succeeds

- **WHEN** the triggered curriculum session completes successfully
- **THEN** the API layer SHALL delete `pending_curriculum_request`
- **AND** a subsequent POST SHALL receive 202, not 409

#### Scenario: Lock is released when the triggered session fails

- **WHEN** the triggered curriculum session terminates with an error, a timeout, or an exception
- **THEN** the API layer SHALL delete `pending_curriculum_request`
- **AND** a subsequent POST SHALL receive 202, not 409

#### Scenario: Lock is released when the session ignores the release instruction

- **WHEN** the triggered curriculum session completes without ever calling `state_delete(key="pending_curriculum_request")`
- **THEN** the API layer SHALL still delete the key on session termination
- **AND** a subsequent POST SHALL receive 202, not 409

#### Scenario: Session-side early release is idempotent

- **WHEN** the triggered session calls `state_delete(key="pending_curriculum_request")` and the API layer subsequently runs its own release on termination
- **THEN** the second release SHALL be a no-op
- **AND** neither release SHALL raise

#### Scenario: Daemon restart mid-session does not wedge the owner

- **WHEN** the butler daemon restarts while a curriculum session is in flight, so no API-layer release ever runs
- **AND** a POST is made more than 15 minutes after `requested_at`
- **THEN** the stale lease SHALL be reclaimed and overwritten
- **AND** the response status SHALL be 202

#### Scenario: Trigger unreachable releases the lock immediately

- **WHEN** the API layer cannot reach the education butler to trigger the session
- **THEN** it SHALL delete `pending_curriculum_request` rather than leaving the lease to expire
- **AND** the owner SHALL be able to retry immediately

#### Scenario: Empty topic

- **WHEN** a POST request is made with body `{"topic": ""}`
- **THEN** the response status SHALL be 422

#### Scenario: Topic exceeds length limit

- **WHEN** a POST request is made with a `topic` longer than 200 characters
- **THEN** the response status SHALL be 422
