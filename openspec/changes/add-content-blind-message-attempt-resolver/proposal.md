## Why

The dashboard already assigns every user message an immutable client `message_id`, but after a lost new-conversation SSE event the client cannot resolve that ID without message text, a known conversation ID, an unbounded history scan, or a mutating recovery control. A narrow content-blind read is required before browser draft recovery can classify the attempt after reload without risking duplicate ingress.

This draft proposes the backend contract for exact owner review. PR #4056 is a pending client-policy consumer, not adopted authority, and neither proposal authorizes implementation.

## What Changes

- Add `GET /api/butlers/{name}/conversation-turns/{message_id}` beside the existing message-scoped Stop and recovery resource family.
- Return HTTP 200 only when the exact user message belongs to `{name}` and its sanitized durable turn projection is complete. The `ApiResponse` data contains exactly `message_id`, `conversation_id`, `outcome`, and `version`.
- Return an explicit HTTP 404 `MESSAGE_ATTEMPT_NOT_OBSERVED` only after the authoritative shared source was read successfully and no matching `(butler, message_id, role=user)` row existed at that observation.
- Return HTTP 503 `MESSAGE_ATTEMPT_SOURCE_UNAVAILABLE` when the shared source, trusted turn projection, or complete outcome/version cannot be read. Missing or incomplete projection never masquerades as absence.
- Treat absence as point-in-time evidence only. It never licenses retry, replay, a new message identity, or a claim that an in-flight request cannot persist later.
- Reuse the existing dashboard network/API-key authority and server-derived owner principal. Accept no caller-supplied owner identity and reveal no cross-butler match.
- Keep the read content-blind and side-effect-free: no message/body/page-context selection, text search, history scan, draft upload, second ledger/identity, Stop, retry, replay, provider/LLM call, audit/data write, or schema change.

### Proposed owner policy

Approve the exact resource route and three-outcome HTTP contract above. `outcome` reuses the owner-facing durable-turn state vocabulary from the active `durable-dashboard-terminal-action-recovery` change. `version` is a deterministic opaque V1 digest of the allowed message ID, conversation ID, outcome, and the trusted projection's `updated_at`, used only for equality/change detection rather than clock ordering.

The resolver must be implemented only after that active change lands a reusable trusted projection of `dashboard_turn.state` and `updated_at`. Current `message_get_by_id()` is forbidden because it selects message content, and current `dashboard_turn_dispatch_status()` is insufficient because it exposes control-oriented fields/outcomes and no projection version.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-conversations`: add a content-blind, exact-message resolver within the existing message-scoped conversation-turn resource family.

## Impact

- Future resolver implementation touches the dashboard conversation router, response/error models, a new content-blind shared-data lookup helper, API inventory documentation, and focused backend tests. Client typing and consumption remain separately allocated under the approved PR #4056 policy.
- The primary-key message lookup joins `public.dashboard_conversations` only to enforce the requested butler namespace and reuses the trusted sanitized turn projection. It requires no content column, full-text index, pagination, new table, or migration.
- The existing `ApiKeyMiddleware` remains the transport authority. `authenticated_principal()` remains the server-held single-user principal source; no identity arrives in query, body, or headers beyond the existing API-key mechanism.
- The active `durable-dashboard-terminal-action-recovery` turn projection is an implementation prerequisite. This proposal does not amend, duplicate, or claim it has landed.
- No test files change in this spec-only draft (`Tests: +0 ~0 -0`).

## Out of Scope

- Implementing the resolver, its client consumer, the active durable turn projection, or PR #4056.
- Returning message text, title, role, page context, target butler/kind, request/session/route IDs, timestamps other than the sanitized projection version, raw ingress state, reason/error text, terminal-action details, credentials, or provider data.
- Server-side draft persistence, draft content upload, body-derived lookup, full-text search, conversation-history scans, list/bulk resolution, or a second message/attempt identity.
- Retry, replay, Stop, ingress recovery, routing, notification, provider/LLM calls, audit/data mutations, database schema changes, or new authorization/session models.
- Treating a successful absence observation as proof against a concurrent or later first persistence, or as authority for any write.
- Baseline spec sync, archive, approval, downstream readiness, merge, activation, deployment, or live data access.
