## Context

At baseline `8ecee3e5e1cf0f35b0eac8b3030257df62d5db5c`, both dashboard chat request models accept a client-generated immutable `message_id`. `_persist_dashboard_user_message()` passes that ID to `message_create_idempotent()`, whose first write inserts `public.dashboard_messages.id` before SSE streaming and whose conflicts reuse the original row or reject changed butler/conversation/role/content semantics.

The data layer already has `message_get_by_id()`, but it selects `content`, `page_context`, tool/result attribution, errors, and other fields. It cannot back a content-blind resolver. Conversation message reads require a known conversation ID and bounded pagination; message search reads content by design. Message-scoped Stop and the active change's `retry-ingress` are mutations, not status reads.

`public.dashboard_conversations.butler_name` is the stored namespace that binds a message to the requested butler. `public.dashboard_messages.id` and the conversation foreign key are indexed identities, so exact resolution needs no search index or table change.

The active `durable-dashboard-terminal-action-recovery` change proposes the owner-facing `dashboard_turn.state` and `updated_at` projection in REQ-dashboard-conversations-002/005. That projection is not implemented at this baseline. Current `dashboard_turn_dispatch_status()` is a control helper: it exposes internal control outcomes and no projection version. This resolver must wait for and reuse the trusted owner-facing projection rather than read private control tables or invent a second mapping.

Dashboard access remains governed by localhost/Tailscale isolation and optional `ApiKeyMiddleware`; the only server-held single-user principal seam is `authenticated_principal()`. PR #4056 is a draft consumer design and supplies no authority to build this endpoint.

Open PR #3960 touches `src/butlers/api/conversations.py`, the conversation tests, and the `dashboard-conversations` baseline for a distinct conversation/reply-target identity split. It contains no exact-message attempt resolver, so it is not duplicate allocation; it is foreign file overlap that a future implementation must serialize behind and recheck. This proposal adds a uniquely named ADDED requirement and does not modify that PR's requirement blocks.

## Goals / Non-Goals

**Goals:**

- Resolve one exact `(butler, immutable message_id)` without reading message content.
- Return only the identity and sanitized durable outcome/version needed for client reconciliation.
- Distinguish an authoritative point-in-time absence observation from source or projection unavailability.
- Preserve existing authentication, principal, message idempotency, and durable-turn contracts.
- Make the route read-only, bounded, cache-safe, and independently testable.

**Non-Goals:**

- Provide a list, search, history, body fetch, or server-side draft service.
- Decide client retry timing, automatic replay, or browser-draft behavior.
- Add or modify the durable-turn state machine, persistence tables, database functions, or authorization model.
- Audit the read, invoke another service, or perform any state change.

## Decisions

### Decision 1: Add GET to the existing message-scoped turn resource

The interface is:

```text
GET /api/butlers/{name}/conversation-turns/{message_id}
```

This is the read counterpart to the existing message-scoped Stop route and the active change's proposed recovery route. It keeps `{name}` as the established butler namespace and `message_id` as the one immutable turn identity. A new top-level search endpoint or body-bearing POST would weaken discoverability, cache/method semantics, and the exact-resource model.

`message_id` is a FastAPI UUID path parameter. Invalid UUIDs retain normal request-validation behavior and never reach the database. Every post-authentication resolver outcome emitted by the route (200 found, 404 not observed, and 503 unavailable) includes `Cache-Control: no-store` because absence can change and durable outcomes advance. The middleware's pre-route 401 and the framework's pre-handler 422 remain under their existing contracts and are outside this route-owned header guarantee.

### Decision 2: Use a dedicated content-blind identity query

Add a narrowly named data-layer helper that issues one indexed query equivalent to:

```sql
SELECT m.id AS message_id, m.conversation_id
FROM public.dashboard_messages AS m
JOIN public.dashboard_conversations AS c ON c.id = m.conversation_id
WHERE m.id = $1
  AND m.role = 'user'
  AND c.butler_name = $2
```

The SELECT list is the privacy boundary. It contains no `content`, `page_context`, title, source, request/session ID, error, tool call, token count, or timestamp. The helper must not call `message_get_by_id()` and redact afterward; body access that is later discarded is still body access.

Wrong-butler and assistant-row matches return the same no-row result as a nonexistent ID. This prevents the route from disclosing a cross-butler conversation. The message and conversation primary/foreign-key indexes make the lookup bounded; there is no pagination, text predicate, or history scan.

### Decision 3: Read identity and the trusted projection in one consistent snapshot

The route acquires `credential_shared_pool()` and opens a read-only repeatable-read transaction. Inside that snapshot it runs the exact identity query, then resolves the corresponding sanitized durable-turn projection through the reusable seam delivered by `durable-dashboard-terminal-action-recovery`.

- No matching identity in a successful snapshot is authoritative absence at that observation.
- A matching identity with a complete safe projection is found.
- Pool acquisition failure, query failure, projection failure, missing projection, invalid outcome, or absent projection `updated_at` is source unavailable.

The transaction uses no `FOR UPDATE` and performs no function with mutation semantics. A single snapshot keeps `conversation_id`, outcome, and version internally consistent while allowing the original send transaction to commit before or after the observation.

Implementation is blocked until the active durable change lands a reusable trusted projection with the exact owner-facing state vocabulary, sanitized mapping, `updated_at`, and update-on-state-change guarantee. This change does not implement that prerequisite, read private `public.dashboard_conversation_turns` fields directly, or substitute `dashboard_turn_dispatch_status()`.

### Decision 4: Use exact found, absent, and unavailable wire outcomes

Found returns HTTP 200 in the repository's `ApiResponse` envelope. `meta` is the standard empty object with no extensions, and `data` is a closed model with exactly:

```json
{
  "message_id": "uuid",
  "conversation_id": "uuid",
  "outcome": "pending_ingress | pending_reconciliation | completed | failed | cancelled | pending_cancellation | retryable_error | rejected | ambiguous",
  "version": "v1.<base64url-sha256>"
}
```

`outcome` is copied from the existing owner-facing `dashboard_turn.state`; it is never the raw ingress state or control-helper outcome. `version` is `v1.` plus unpadded base64url SHA-256 over the canonical UTF-8 serialization of `{message_id, conversation_id, outcome, updated_at}` with keys in that fixed order and UUID/timestamp values in their canonical string forms. It is deterministic, content-blind, and changes when the allowed outcome or projection timestamp changes. Clients use it only as opaque equality/change evidence, not as an ordering oracle.

Authoritative absence returns HTTP 404 in the standard error envelope with fixed code `MESSAGE_ATTEMPT_NOT_OBSERVED` and fixed message `Message attempt not observed.` It echoes no alternate identity or source detail. HTTP 404 means only that the readable snapshot contained no exact `(butler, message_id, role=user)` row.

Source or projection unavailability returns HTTP 503 with fixed code `MESSAGE_ATTEMPT_SOURCE_UNAVAILABLE` and fixed message `Message attempt source unavailable.` A found message without a complete projection is 503, not 404 or a guessed outcome. Raw exceptions stay server-side and content-blind.

### Decision 5: Preserve existing dashboard authority and server principal

`ApiKeyMiddleware` already covers every `/api/*` route other than the two health paths. With `DASHBOARD_API_KEY` configured, a missing or wrong key returns 401 before the resolver executes. With it unset, the established localhost/Tailscale network boundary remains authoritative. The resolver adds no bearer token, per-message token, cookie, session, or public exception.

The route accepts only `{name}` and `{message_id}`. It has no owner/actor/principal/user query, body, or new header. Any internal principal context comes from `authenticated_principal()` and is neither used as a caller assertion nor returned; that helper names the established single-user principal and is not a substitute for `ApiKeyMiddleware`. Because this is a read-only route and audit writes are out of scope, the principal is not persisted.

### Decision 6: Absence is observation, never write authority

A 404 can race the original create/send before its first message insert commits. It therefore conveys neither “this ID never will exist” nor “a new ID is safe.” The response contains no `retryable`, `replay_safe`, terminal, or expiry flag.

A later GET may return found for the same immutable ID. The resolver never creates the row, opens a turn, claims ingress, invokes retry/Stop, or selects the body to compare it. Client behavior remains separately governed; this proposal only ensures the backend result cannot itself be mistaken for replay authority.

### Decision 7: Repeated reads are side-effect-free and version coherent

Repeated GETs against the same projection return the same four data values. When the trusted durable projection changes outcome, its `updated_at` version must also change, and the next GET returns both together from one snapshot. No process-local SSE map or client-supplied state may override them.

No audit row, telemetry event with identity, notification, cache mutation, database lock for update, provider call, MCP call, or LLM session is created. Low-cardinality request metrics may record only route template, HTTP class, and categorical result (`found`, `not_observed`, `source_unavailable`, `unauthorized`); logs and traces omit message and conversation IDs, outcome version, raw exceptions, and source details.

## Risks / Trade-offs

- **[Risk] Callers may treat 404 as permission to resend with a new ID.** → Use `NOT_OBSERVED` language, omit replay flags, and pin the point-in-time/no-write semantics in contract and tests.
- **[Risk] A message row may exist briefly before its durable turn projection.** → Return 503 until the trusted projection is complete; never collapse it to absence or guessed pending.
- **[Risk] A direct existing helper would read private content unnecessarily.** → Add a dedicated two-column identity query and a query-shape/privacy test.
- **[Risk] Wrong-butler probing could reveal cross-domain identity.** → Apply butler ownership in the query and return the identical absence envelope.
- **[Risk] The active durable projection changes before implementation.** → Serialize implementation behind its landing and revalidate the exact outcome/version contract rather than copying draft internals now.
- **[Risk] `updated_at` could fail to version an outcome transition.** → Treat update-on-state-change as a prerequisite and return 503 when a complete version cannot be supplied.

## Migration Plan

1. Wait for `durable-dashboard-terminal-action-recovery` to land its trusted message `dashboard_turn` projection and verify its state/version guarantees on the implementation base.
2. Add the closed response model, fixed error mapping, dedicated content-blind identity helper, and read-only route without schema or data migration.
3. Add focused authorization, privacy/query-shape, found/absence/unavailable, race, idempotence/version, and no-side-effect tests.
4. Update the dashboard API inventory and, only after both specs are owner-approved, let the separately allocated PR #4056 implementation consume the resolver.
5. Run focused tests, repository guards, and terminal hosted CI on the exact implementation head.

Rollback removes the GET route, response model, helper, and client call. There is no stored state, schema, cache, migration, or backfill to reverse.

## Owner Review Gate

Owner approval is required for these exact proposed choices:

1. `GET /api/butlers/{name}/conversation-turns/{message_id}` as the exact resource.
2. Found HTTP 200 with only `message_id`, `conversation_id`, sanitized `outcome`, and opaque projection `version`.
3. Point-in-time authoritative absence as HTTP 404 `MESSAGE_ATTEMPT_NOT_OBSERVED`, with no replay implication.
4. Any source, trusted-projection, completeness, or version failure as HTTP 503 `MESSAGE_ATTEMPT_SOURCE_UNAVAILABLE`.
5. Existing dashboard network/API-key authority and server-derived principal, with no new identity input or audit write.
6. A dedicated content-blind identity query and trusted projection reuse, with no body read or private-control fallback.
7. Implementation dependency on the landed active durable-turn projection; PR #4056 remains a pending, separately approved consumer.

Until the owner approves the exact artifact digest or commit, this resolver remains a draft proposal and no implementation or downstream client work is ready.
