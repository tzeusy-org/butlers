## ADDED Requirements

### Requirement: Content-Blind Exact Message Attempt Resolution

The dashboard API SHALL expose `GET /api/butlers/{name}/conversation-turns/{message_id}` as a read-only resolver under the existing dashboard access authority. A found response SHALL identify only the exact immutable user message, its conversation, the sanitized owner-facing durable-turn outcome, and that projection's version. Authoritative point-in-time absence and source/projection unavailability SHALL be distinct error outcomes. Every found, absence, and unavailable response SHALL carry `Cache-Control: no-store`. The resolver SHALL NOT read or return message content, infer identity from content, mutate state, or authorize retry or replay.

ID: REQ-dashboard-conversations-009
Source: heart-and-soul/vision.md § What Butlers Is Not (Not an experiment); dashboard-conversations § Message Data Model and Durable Dashboard Turn Control; durable-dashboard-terminal-action-recovery REQ-dashboard-conversations-002 and REQ-dashboard-conversations-005; design.md Decisions 1-7
Scope: v1-mandatory

#### Scenario: Exact matching user message returns the content-blind projection

- **WHEN** an authorized request supplies butler B and immutable message ID M, and one user message M belongs to a conversation owned by B with a complete trusted durable-turn projection
- **THEN** the API returns HTTP 200 `ApiResponse` with data containing exactly `message_id`, `conversation_id`, `outcome`, and `version`, plus the standard empty `meta: {}` with no extensions
- **AND** `message_id` equals M, `conversation_id` is M's stored parent, and `outcome` is the existing sanitized owner-facing durable-turn state
- **AND** `version` is a deterministic opaque V1 digest of `message_id`, `conversation_id`, `outcome`, and the trusted projection's `updated_at`, and clients treat it only as equality/change evidence
- **AND** the response carries `Cache-Control: no-store`

#### Scenario: Found outcome vocabulary stays within the durable read model

- **WHEN** the exact message attempt is found
- **THEN** `outcome` is exactly one of `pending_ingress`, `pending_reconciliation`, `completed`, `failed`, `cancelled`, `pending_cancellation`, `retryable_error`, `rejected`, or `ambiguous`
- **AND** raw ingress/control outcomes such as `ready`, `dispatch`, `active`, `finished`, `missing`, or `conflict` are never returned

#### Scenario: Wrong butler cannot reveal a cross-butler match

- **WHEN** message ID M exists but belongs to a conversation whose owning butler differs from `{name}`
- **THEN** the API returns the same HTTP 404 `MESSAGE_ATTEMPT_NOT_OBSERVED` envelope used when no matching user message exists
- **AND** the response does not reveal the actual conversation or butler

#### Scenario: Authoritative point-in-time absence is explicit

- **WHEN** the shared conversation source is read successfully in one consistent snapshot and no `(butler={name}, message_id=M, role=user)` row exists
- **THEN** the API returns HTTP 404 with fixed code `MESSAGE_ATTEMPT_NOT_OBSERVED`
- **AND** the fixed error text contains no message body, search fragment, conversation identity, alternate butler, raw exception, or source detail
- **AND** the observation means only that M was absent in that snapshot
- **AND** the response carries `Cache-Control: no-store`

#### Scenario: Absence never licenses a new attempt or replay

- **WHEN** a client receives `MESSAGE_ATTEMPT_NOT_OBSERVED` while the original create/send request may still be in flight
- **THEN** the response provides no replay-safe, retryable, terminal, or never-persisted claim
- **AND** the resolver performs no insertion, retry, ingress recovery, Stop, or dispatch
- **AND** a later first persistence of the same immutable message ID may be returned as found by a later GET
- **AND** the client must retain the same message identity and follow a separately approved recovery policy

#### Scenario: Missing or incomplete turn projection is unavailable

- **WHEN** the exact user message exists but its trusted durable-turn projection is absent, incomplete, invalid, or cannot supply both sanitized `outcome` and `version`
- **THEN** the API returns HTTP 503 with fixed code `MESSAGE_ATTEMPT_SOURCE_UNAVAILABLE`
- **AND** it does not return HTTP 200 with guessed/default state or HTTP 404 absence
- **AND** it does not fall back to private control fields, message content, an SSE/process-local map, or a mutating status control
- **AND** the response carries `Cache-Control: no-store`

#### Scenario: Shared source failure is unavailable

- **WHEN** the shared pool, exact identity query, consistent snapshot, or trusted projection read fails
- **THEN** the API returns HTTP 503 with fixed code `MESSAGE_ATTEMPT_SOURCE_UNAVAILABLE`
- **AND** the error contains no raw database, credential, message, request, session, route, provider, or exception detail
- **AND** the response carries `Cache-Control: no-store`

#### Scenario: Existing dashboard authorization runs before resolution

- **WHEN** dashboard API-key authentication is enabled and the request omits or supplies an incorrect key
- **THEN** the existing middleware returns HTTP 401 `UNAUTHORIZED` before the resolver queries message existence or turn state
- **AND** the response reveals no attempt-existence information

#### Scenario: Resolver accepts no caller owner identity

- **WHEN** the resolver handles a request under the existing single-user dashboard authority
- **THEN** any principal context is derived from the server-held `authenticated_principal()` seam
- **AND** the route accepts no owner, actor, principal, identity, or user parameter in path, query, body, or a new header
- **AND** no principal value is included in the response

#### Scenario: Resolution is read-only and idempotent

- **WHEN** the same authorized GET is repeated while the durable projection is unchanged
- **THEN** it returns the same four data fields and version without inserting, updating, deleting, locking for mutation, or invoking another process
- **AND** it opens no MCP, LLM, provider, connector, retry, replay, Stop, audit-write, or notification path

#### Scenario: Later durable state returns a later projection version

- **WHEN** the trusted durable-turn projection for M changes after a prior found response
- **THEN** a later GET returns its current sanitized outcome and current projection version
- **AND** no stale process-local or client-supplied outcome overrides the durable source

#### Scenario: Invalid message identity is rejected before data access

- **WHEN** `{message_id}` is not a valid UUID
- **THEN** normal request validation rejects it without querying the shared source
