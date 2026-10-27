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

The system SHALL expose `POST /api/education/curriculum-requests` accepting a JSON body `{"topic": "<topic>", "goal": "<optional_goal>"}`.

The `topic` field SHALL be required and non-empty (max 200 characters). The `goal` field SHALL be optional (max 500 characters).

Before any detached work begins, the endpoint SHALL insert an immutable receipt row into `education.curriculum_requests` with status `accepted`, the submitted topic and goal, and a generated `id`. That `id` is the request's permanent receipt.

The endpoint SHALL NOT persist a `pending_curriculum_request` KV key. The one-pending-at-a-time guard SHALL be the partial unique index `uq_curriculum_requests_one_open`, which permits at most one receipt in a non-terminal status (`accepted`, `running`). When the insert is refused by that index, the endpoint SHALL return 409 Conflict.

When the receipt store cannot be read or written because the education migration chain is absent, the endpoint SHALL return 503 rather than accepting a request it cannot evidence.

On success, the endpoint SHALL return 202 Accepted with body `{"status": "accepted", "topic": "<topic>", "request_id": "<uuid>"}`. `202` SHALL mean *accepted and recorded* only; it SHALL NOT be treated by any caller as evidence that a curriculum was created or that the owner was contacted.

#### Scenario: Submit a new curriculum request

- **WHEN** a POST request is made to `/api/education/curriculum-requests` with body `{"topic": "Python", "goal": "Learn web development with Flask"}`
- **AND** no non-terminal receipt exists
- **THEN** the response status SHALL be 202
- **AND** the response body SHALL contain `{"status": "accepted", "topic": "Python"}` and a `request_id`
- **AND** a receipt row SHALL exist in `education.curriculum_requests` with status `accepted`, the topic and goal, and no outcome evidence

#### Scenario: Receipt precedes detached work

- **WHEN** a curriculum request is accepted
- **THEN** the receipt row SHALL be persisted before the detached curriculum task is created

#### Scenario: Submit request without goal

- **WHEN** a POST request is made with body `{"topic": "Linear Algebra"}`
- **AND** no non-terminal receipt exists
- **THEN** the response status SHALL be 202
- **AND** the receipt row SHALL have `goal` set to null

#### Scenario: Duplicate request while one is in flight

- **WHEN** a POST request is made to `/api/education/curriculum-requests`
- **AND** a receipt already exists in status `accepted` or `running`
- **THEN** the response status SHALL be 409
- **AND** the response body SHALL indicate a curriculum request is already pending

#### Scenario: Receipt store unavailable

- **WHEN** a POST request is made and `education.curriculum_requests` does not exist
- **THEN** the response status SHALL be 503

#### Scenario: Empty topic

- **WHEN** a POST request is made with body `{"topic": ""}`
- **THEN** the response status SHALL be 422

#### Scenario: Topic exceeds length limit

- **WHEN** a POST request is made with a `topic` longer than 200 characters
- **THEN** the response status SHALL be 422
