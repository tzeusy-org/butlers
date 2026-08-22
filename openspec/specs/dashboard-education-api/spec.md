# Education API Write Endpoints

## Purpose

Defines the education butler's dashboard API write/read endpoints: pending reviews, mastery summary, mind map status update, curriculum request submission, and the durable accepted-to-outcome receipt for those requests.

## Requirements

### Requirement: Pending reviews endpoint

The system SHALL expose `GET /api/education/mind-maps/{mind_map_id}/pending-reviews` returning nodes due for spaced repetition review (where `next_review_at <= now()`).

The endpoint SHALL accept an optional `horizon_days` query parameter (integer, `>= 0`). When omitted, only overdue nodes are returned. When set, upcoming reviews due within that many days from now are also included, enabling timeline grouping (Overdue / Today / This Week / Later).

The response SHALL be a JSON array of node objects, each containing: `node_id`, `label`, `ease_factor`, `repetitions`, `next_review_at`, `mastery_status`.

The endpoint SHALL return 404 if the mind map does not exist. The endpoint SHALL return an empty array if no reviews are due.

The endpoint SHALL call the existing `spaced_repetition_pending_reviews(pool, mind_map_id, horizon_days=horizon_days)` tool function without duplicating its SQL logic.

#### Scenario: Reviews due for a mind map with scheduled nodes

- **WHEN** a GET request is made to `/api/education/mind-maps/{id}/pending-reviews`
- **AND** the mind map exists with 3 nodes having `next_review_at` in the past
- **THEN** the response status SHALL be 200
- **AND** the response body SHALL contain exactly 3 node objects with their review metadata

#### Scenario: No reviews due

- **WHEN** a GET request is made to `/api/education/mind-maps/{id}/pending-reviews`
- **AND** the mind map exists but all nodes have `next_review_at` in the future or NULL
- **THEN** the response status SHALL be 200
- **AND** the response body SHALL be an empty array

#### Scenario: Mind map not found

- **WHEN** a GET request is made to `/api/education/mind-maps/{nonexistent-id}/pending-reviews`
- **THEN** the response status SHALL be 404

---

### Requirement: Mastery summary endpoint

The system SHALL expose `GET /api/education/mind-maps/{mind_map_id}/mastery-summary` returning aggregate mastery statistics for a mind map.

The response SHALL be a JSON object containing: `mind_map_id`, `total_nodes`, `mastered_count`, `learning_count`, `reviewing_count`, `unseen_count`, `diagnosed_count`, `avg_mastery_score`, `struggling_node_ids`.

The endpoint SHALL call the existing `mastery_get_map_summary(pool, mind_map_id)` tool function. The endpoint SHALL return 404 if the mind map does not exist.

#### Scenario: Summary for an active mind map

- **WHEN** a GET request is made to `/api/education/mind-maps/{id}/mastery-summary`
- **AND** the mind map has 10 nodes with mixed mastery statuses
- **THEN** the response status SHALL be 200
- **AND** `total_nodes` SHALL equal 10
- **AND** the status counts SHALL sum to `total_nodes`
- **AND** `avg_mastery_score` SHALL be between 0.0 and 1.0

#### Scenario: Mind map not found

- **WHEN** a GET request is made to `/api/education/mind-maps/{nonexistent-id}/mastery-summary`
- **THEN** the response status SHALL be 404

---

### Requirement: Mind map status update endpoint

The system SHALL expose `PUT /api/education/mind-maps/{mind_map_id}/status` accepting a JSON body `{"status": "<new_status>"}` where `new_status` is one of `active`, `completed`, `abandoned`.

The endpoint SHALL call the existing `mind_map_update_status(pool, mind_map_id, status)` tool function. The endpoint SHALL return 404 if the mind map does not exist. The endpoint SHALL return 422 if the status value is not one of the three allowed values.

On success, the endpoint SHALL return the updated mind map object (without nodes/edges).

#### Scenario: Abandon an active mind map

- **WHEN** a PUT request is made to `/api/education/mind-maps/{id}/status` with body `{"status": "abandoned"}`
- **AND** the mind map exists with status `active`
- **THEN** the response status SHALL be 200
- **AND** the returned mind map object SHALL have `status` equal to `abandoned`

#### Scenario: Re-activate an abandoned mind map

- **WHEN** a PUT request is made to `/api/education/mind-maps/{id}/status` with body `{"status": "active"}`
- **AND** the mind map exists with status `abandoned`
- **THEN** the response status SHALL be 200
- **AND** the returned mind map object SHALL have `status` equal to `active`

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

---

### Requirement: Curriculum request receipt lifecycle

Each accepted curriculum request SHALL settle to a terminal outcome on its receipt row, so that a failure of the detached work is visible to the owner rather than only to the log.

The receipt SHALL carry, in addition to `id`, `topic` and `goal`: `status` (one of `accepted`, `running`, `completed`, `failed`), `session_id`, `mind_map_id`, `calibration_ready_at`, `failure_reason`, `requested_at`, `triggered_at`, `settled_at`, and `updated_at`.

The detached task SHALL stamp `status = running` and `triggered_at` before handing the request to the butler's `trigger` MCP tool, and SHALL await that tool to completion.

The task SHALL settle `status = completed` only when the triggered session reported success **and** a mind map created at or after `triggered_at` is found. `session_id` SHALL be recorded from the trigger result, `mind_map_id` from that correlation, and `calibration_ready_at` SHALL be set only when the correlated mind map's teaching flow has reached `diagnosing` or a later state.

The task SHALL settle `status = failed` with a stable `failure_reason` on every other exit path: `trigger_unreachable` when the butler could not be reached, `session_error` when the session reported its own failure, and `no_curriculum_created` when a session exited cleanly without producing a curriculum. A clean session exit SHALL NOT by itself settle `completed`.

Settlement SHALL be idempotent: the first terminal write SHALL win, and a later or duplicate settle SHALL be a no-op rather than a contradicting outcome.

A receipt that remains non-terminal for longer than the abandonment timeout SHALL be settled to `failed` with `failure_reason = "timed_out"`, releasing the pending guard. This sweep SHALL run on every submit and every status read, so that an API restart that kills an in-flight task cannot strand the guard.

The database SHALL enforce that a terminal status carries `settled_at`, that a non-terminal status does not, and that `failed` carries a `failure_reason`.

#### Scenario: Successful curriculum settles with evidence

- **WHEN** the triggered session reports success and a mind map was created after `triggered_at`
- **THEN** the receipt SHALL settle `status = completed` with `session_id`, `mind_map_id`, and `settled_at` recorded

#### Scenario: Trigger cannot reach the butler

- **WHEN** the MCP client or `trigger` call raises
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "trigger_unreachable"`

#### Scenario: Session reports its own failure

- **WHEN** the `trigger` result reports `success: false`
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "session_error"`
- **AND** the receipt SHALL retain the `session_id` of the failed session

#### Scenario: Session exits without creating a curriculum

- **WHEN** the triggered session reports success but no mind map was created at or after `triggered_at`
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "no_curriculum_created"`

#### Scenario: Settlement is idempotent

- **WHEN** a receipt already holds a terminal status
- **AND** a second settle is attempted with a different status
- **THEN** the receipt SHALL retain its original terminal status and evidence

#### Scenario: Abandoned receipt is swept

- **WHEN** a receipt has been non-terminal for longer than the abandonment timeout
- **AND** a curriculum request is submitted or a receipt status is read
- **THEN** the abandoned receipt SHALL settle `status = failed` with `failure_reason = "timed_out"`
- **AND** a new curriculum request SHALL be accepted

---

### Requirement: Curriculum request status read

The system SHALL expose `GET /api/education/curriculum-requests/{request_id}` and `GET /api/education/curriculum-requests/latest`, both returning `{"receipts_available": <bool>, "receipt": <receipt|null>}`.

`receipts_available: false` SHALL mean the receipt store could not be read (for example, the education migration chain is absent). Callers SHALL render that as status unavailable, never as "no request in flight".

`receipts_available: true` with `receipt: null` SHALL mean the store is readable and no matching request exists.

`GET /curriculum-requests/{request_id}` SHALL return 404 for an unknown request ID and 422 for a malformed one. Both reads SHALL be read-only with respect to request outcomes; the only write they perform is the abandonment sweep.

#### Scenario: Read a terminal receipt

- **WHEN** a GET request is made to `/api/education/curriculum-requests/{request_id}` for a completed request
- **THEN** the response status SHALL be 200
- **AND** the body SHALL contain `receipts_available: true` and a receipt with `status: "completed"`, `session_id`, `mind_map_id`, and `settled_at`

#### Scenario: Read a failed receipt

- **WHEN** a GET request is made for a failed request
- **THEN** the receipt SHALL carry the terminal `failure_reason`

#### Scenario: Unknown request ID

- **WHEN** a GET request is made for a request ID that does not exist
- **THEN** the response status SHALL be 404

#### Scenario: Malformed request ID

- **WHEN** a GET request is made with a path segment that is not a UUID
- **THEN** the response status SHALL be 422

#### Scenario: Receipt store unavailable

- **WHEN** `education.curriculum_requests` does not exist
- **THEN** the response status SHALL be 200
- **AND** the body SHALL contain `receipts_available: false` and `receipt: null`

#### Scenario: No request has ever been made

- **WHEN** a GET request is made to `/api/education/curriculum-requests/latest`
- **AND** the store is readable and empty
- **THEN** the body SHALL contain `receipts_available: true` and `receipt: null`
