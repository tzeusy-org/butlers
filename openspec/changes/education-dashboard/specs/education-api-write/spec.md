## ADDED Requirements

### Requirement: Pending reviews endpoint

The system SHALL expose `GET /api/education/mind-maps/{mind_map_id}/pending-reviews` returning nodes due for spaced repetition review (where `next_review_at <= now()`).

The response SHALL be a JSON array of node objects, each containing: `id`, `mind_map_id`, `label`, `mastery_score`, `mastery_status`, `next_review_at`, `last_reviewed_at`, `ease_factor`, `repetitions`.

The endpoint SHALL return 404 if the mind map does not exist. The endpoint SHALL return an empty array if no reviews are due.

The endpoint SHALL call the existing `spaced_repetition_pending_reviews(pool, mind_map_id)` tool function without duplicating its SQL logic.

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

The endpoint SHALL write a JSON payload `{"topic": "<topic>", "goal": "<goal>", "requested_at": "<ISO-8601>"}` to the education butler's KV state store under key `pending_curriculum_request`. If a pending request already exists (key is present), the endpoint SHALL return 409 Conflict.

On success, the endpoint SHALL return 202 Accepted with body `{"status": "pending", "topic": "<topic>"}`.

#### Scenario: Submit a new curriculum request

- **WHEN** a POST request is made to `/api/education/curriculum-requests` with body `{"topic": "Python", "goal": "Learn web development with Flask"}`
- **AND** no pending curriculum request exists
- **THEN** the response status SHALL be 202
- **AND** the response body SHALL contain `{"status": "pending", "topic": "Python"}`
- **AND** the KV store SHALL contain key `pending_curriculum_request` with the topic and goal

#### Scenario: Submit request without goal

- **WHEN** a POST request is made with body `{"topic": "Linear Algebra"}`
- **AND** no pending curriculum request exists
- **THEN** the response status SHALL be 202
- **AND** the KV store entry SHALL have `goal` set to null

#### Scenario: Duplicate request while one is pending

- **WHEN** a POST request is made to `/api/education/curriculum-requests`
- **AND** a `pending_curriculum_request` key already exists in the KV store
- **THEN** the response status SHALL be 409
- **AND** the response body SHALL indicate a curriculum request is already pending

#### Scenario: Empty topic

- **WHEN** a POST request is made with body `{"topic": ""}`
- **THEN** the response status SHALL be 422

#### Scenario: Topic exceeds length limit

- **WHEN** a POST request is made with a `topic` longer than 200 characters
- **THEN** the response status SHALL be 422
