## 1. Reply channel (conversation_reply)

- [x] 1.1 `conversation_reply_create` / `message_find_reply_since` DB-layer functions in `src/butlers/api/conversations.py`.
- [x] 1.2 `conversation_reply` MCP tool (`src/butlers/core_tools/_conversation_reply.py`), registered unconditionally in `_dispatcher.py`.

## 2. Poller

- [x] 2.1 Rewrite `_stream_conversation_response` to poll `message_find_reply_since` instead of the routed butler's `sessions` row.
- [x] 2.2 `_lookup_timed_out_session_id` best-effort session link for the `SESSION_TIMEOUT` event.

## 3. Sticky routing

- [x] 3.1 `conversation_set_routed_butler` (idempotent, first-route-wins) + migration `core_153` adding `routed_butler`.
- [x] 3.2 `send_message` pins follow-ups to `routed_butler` when set on a Switchboard-addressed conversation.

## 4. Tests

- [x] 4.1 Unit tests: `conversation_reply` tool (invalid id, missing pool, success, not-found, persistence failure, best-effort request_id).
- [x] 4.2 Unit tests: `conversation_reply_create` / `conversation_set_routed_butler` / `message_find_reply_since` (mocked pool).
- [x] 4.3 Unit tests: `_lookup_timed_out_session_id`; SSE-level reply/sticky-routing/timeout tests.
- [x] 4.4 Real-Postgres integration tests for the `conversation_reply` write path (`tests/integration/test_conversation_reply_db.py`).

## 5. Spec

- [x] 5.1 OpenSpec delta against `dashboard-conversations` (this change).

## 6. Quality gates

- [x] 6.1 `ruff check` / `ruff format --check` on touched files.
- [x] 6.2 Targeted pytest (`tests/api/test_conversations.py`, `tests/core_tools/test_conversation_reply.py`, `tests/integration/test_conversation_reply_db.py`) + migration-integrity (`tests/config/test_migrations.py`) + full suite before PR.
