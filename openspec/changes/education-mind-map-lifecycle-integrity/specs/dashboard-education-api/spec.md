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
`{"topic": "<topic>", "goal": "<goal>", "requested_at": "<ISO-8601>", "lease_expires_at": "<ISO-8601>", "request_token": "<uuid>"}`
to the education butler's KV state store under key
`pending_curriculum_request`, then trigger the ephemeral session that starts
the curriculum. On success the endpoint SHALL return 202 Accepted with body
`{"status": "pending", "topic": "<topic>"}`.

**The lock is a lease, not a flag.** The endpoint SHALL return 409 Conflict
only when a `pending_curriculum_request` key is present **and** its
`lease_expires_at` is in the future. A key whose lease has expired SHALL be
treated as absent: the request proceeds and overwrites it. An expired lease
MUST NOT produce a 409.

**The lease SHALL outlive the session it guards, and SHALL be derived from the
model catalog rather than hardcoded.** `lease_expires_at` SHALL be
`requested_at` plus a TTL computed at acquisition time as the longest session
lifetime the triggered work can consume — the maximum `session_timeout_s`
across the enabled model-catalog entries the trigger may route to for its
requested complexity tier, including any fallback entries a single dispatch
may chain through — plus a margin of at least 300 seconds to cover spawn
overhead, tool latency, and the API layer's own release. If no catalog entry
resolves, the TTL SHALL fall back to 2100 seconds (1800 + 300), matching the
`session_timeout_s` default of 1800 at
`src/butlers/api/routers/model_settings.py:81`.

A fixed TTL SHALL NOT be used unless the specification states why that number
exceeds the maximum session lifetime. A lease shorter than the work it guards
fails open at exactly the wrong moment: it expires while the session is still
legitimately working, the owner's next POST is accepted, and two drain
sessions run on one topic — producing two mind maps for one request, which is
the same duplicate-map defect this change exists to clean up. Deriving the TTL
from `session_timeout_s` also means it tracks operator configuration, which is
per-catalog-entry and adjustable, instead of drifting from it silently.

Because the TTL is derived to exceed the session lifetime, in-flight lease
renewal is NOT required. An implementation MAY renew a live lease instead of
deriving the TTL, provided the renewal cannot lapse while the session runs.

**Every acquisition mints a fresh `request_token`.** `request_token` SHALL be
a newly generated UUID, written at acquisition time — including when an expired
lease is reclaimed and overwritten. It identifies which acquisition the lock
currently belongs to.

**Release is the API layer's responsibility, not the session's.** The
dashboard API layer SHALL release the lock when the triggered session
terminates, whatever the outcome — success, tool error, model refusal, timeout,
or exception — as an unconditional release path rather than one conditioned on
what the session did. The release SHALL also run when the session could not be
triggered at all.

**Release SHALL be a token-scoped compare-and-delete, never a blind key
delete.** A releaser holds the `request_token` minted by its own acquisition,
and SHALL delete the `pending_curriculum_request` key only if the token stored
in that key still equals its own. If the stored token differs, or the key is
already absent, the release SHALL be a no-op that does not raise. The compare
and the delete SHALL be atomic with respect to concurrent acquisitions — one
conditional statement, or a read and a delete inside a single transaction. A
read-then-delete that is not atomic reintroduces the race it exists to close.

Token scoping is a safety requirement, not a tidiness one. An unqualified
`state_delete("pending_curriculum_request")` is wrong whenever more than one
acquisition can be in flight across time: session A stalls past its lease, the
owner submits again and acquires lock B, and A's late release then deletes
**B** — leaving two drain sessions running behind a guard that reports itself
free. Token scoping is what makes every release path idempotent *and* safe out
of order, and therefore what allows more than one release path to exist.

**The triggered session SHALL NOT be a release path.** The trigger prompt MUST
NOT instruct the session to clear `pending_curriculum_request`, and no
session-side clear SHALL be relied upon or retained. The only key-deleting tool
reachable from a session is the shared `state_delete(key)` core tool, which
takes no token and would therefore perform precisely the blind delete this
requirement forbids. Independently of that, an instruction in a prompt is a
request to a language model, not a guarantee; the owner's ability to submit
their next curriculum request MUST NOT depend on model obedience.

#### Scenario: Submit a new curriculum request

- **WHEN** a POST request is made to `/api/education/curriculum-requests` with body `{"topic": "Python", "goal": "Learn web development with Flask"}`
- **AND** no pending curriculum request exists
- **THEN** the response status SHALL be 202
- **AND** the response body SHALL contain `{"status": "pending", "topic": "Python"}`
- **AND** the KV store SHALL contain key `pending_curriculum_request` with the topic, goal, `requested_at`, a `lease_expires_at` derived from the catalog session timeout plus margin, and a freshly generated `request_token`

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
- **THEN** the API layer SHALL delete `pending_curriculum_request`, scoped to the `request_token` its own acquisition minted
- **AND** a subsequent POST SHALL receive 202, not 409

#### Scenario: Lock is released when the triggered session fails

- **WHEN** the triggered curriculum session terminates with an error, a timeout, or an exception
- **THEN** the API layer SHALL delete `pending_curriculum_request`, scoped to its own `request_token`
- **AND** a subsequent POST SHALL receive 202, not 409

#### Scenario: A superseded release does not delete the current lock

- **WHEN** acquisition A stalls past its lease and the owner's next POST reclaims the lock, minting `request_token` B
- **AND** acquisition A's session then terminates and its release runs
- **THEN** the release SHALL compare `request_token` A against the stored token B, find no match, and delete nothing
- **AND** `pending_curriculum_request` SHALL still hold acquisition B's payload
- **AND** a POST while lease B is live SHALL still receive 409

#### Scenario: Release when the key is already absent is a no-op

- **WHEN** a release runs for a `request_token` whose key is no longer present
- **THEN** the release SHALL do nothing and SHALL NOT raise

#### Scenario: The trigger prompt contains no lock-clearing instruction

- **WHEN** the prompt sent to the ephemeral curriculum session is inspected
- **THEN** it SHALL contain no instruction to call `state_delete(key="pending_curriculum_request")` or otherwise clear the lock
- **AND** the lock SHALL still be released on session termination by the API layer

#### Scenario: Daemon restart mid-session does not wedge the owner

- **WHEN** the butler daemon restarts while a curriculum session is in flight, so no API-layer release ever runs
- **AND** a POST is made after `lease_expires_at` has passed
- **THEN** the stale lease SHALL be reclaimed and overwritten
- **AND** the overwritten payload SHALL carry a newly generated `request_token`
- **AND** the response status SHALL be 202

#### Scenario: The lease outlives a long-running session

- **WHEN** a curriculum request is submitted and the catalog entry the trigger routes to has `session_timeout_s = 1800`
- **THEN** `lease_expires_at` SHALL be at least 2100 seconds after `requested_at`
- **AND** a POST made 20 minutes later, while that session is still running, SHALL receive 409, not 202

#### Scenario: Raising the operator's session timeout lengthens the lease

- **WHEN** an operator raises `session_timeout_s` on the catalog entry the trigger routes to
- **AND** a curriculum request is then submitted
- **THEN** the computed TTL SHALL grow with it, so the lease still exceeds the maximum session lifetime
- **AND** the TTL SHALL NOT be a value fixed independently of the catalog

#### Scenario: Trigger unreachable releases the lock immediately

- **WHEN** the API layer cannot reach the education butler to trigger the session
- **THEN** it SHALL release `pending_curriculum_request` under its own `request_token` rather than leaving the lease to expire
- **AND** the owner SHALL be able to retry immediately

#### Scenario: Empty topic

- **WHEN** a POST request is made with body `{"topic": ""}`
- **THEN** the response status SHALL be 422

#### Scenario: Topic exceeds length limit

- **WHEN** a POST request is made with a `topic` longer than 200 characters
- **THEN** the response status SHALL be 422
