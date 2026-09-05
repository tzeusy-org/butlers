> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** Home Assistant transport/wellness reliability landed at the connector boundary the plan preserves.
> **Successor:** `openspec/specs/connector-home-assistant/spec.md`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# Home Assistant Transport and Wellness Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Home Assistant event delivery honest under WebSocket subscription failure, preserve reliable wellness promotion despite ordinary HA noise suppression, and recover daily measurements across outages and unchanged values.

**Architecture:** Keep the connector as the transport owner and keep Switchboard as the only ingress boundary. The WebSocket path becomes ready only after all required event subscriptions acknowledge; REST fallback remains active until that point. Health-shaped events get a deterministic wellness projection independent of whether the ordinary `home_assistant` channel is policy-skipped, and measurement history uses a durable high-water mark for catch-up and deduplication.

**Tech Stack:** Python 3.12, asyncio, aiohttp, FastAPI health endpoint, Prometheus client, pytest/pytest-asyncio, PostgreSQL-backed connector checkpoints, `ingest.v1` MCP submission.

## Global Constraints

- Preserve the existing `home_assistant` connector boundary: transport-specific code remains in `src/butlers/connectors/`.
- Preserve RFC 0003’s Switchboard-only ingress and the `wellness/home_assistant` envelope pairing.
- Do not remove or broaden the global `source_channel=home_assistant` skip rule; ordinary HA noise must remain suppressible.
- A connector health `healthy` result requires an authenticated, acknowledged event path or a demonstrably successful fallback path; a TCP/WebSocket connection alone is insufficient.
- Use stable, low-cardinality logs and metrics; never log access tokens, raw health payloads, or sensitive sensor values.
- Write failing tests before production code for each behavior change; use focused pytest first and expand only after the focused scope is green.
- Do not edit `.env.dev`, credential records, live Docker state, or external Home Assistant configuration in this change.

---

### Task 1: Make WebSocket readiness and fallback health truthful

**Owner:** Main agent

**Files:**
- Modify: `src/butlers/connectors/home_assistant.py:180-680` for WebSocket subscription readiness and reconnect handling.
- Modify: `src/butlers/connectors/home_assistant.py:1367-1430` for connector health state and transport callbacks.
- Create: `tests/connectors/test_home_assistant_transport_contract.py`.
- Test: `tests/connectors/test_home_assistant_rest_fallback.py` and `tests/connectors/test_home_assistant_connector.py` for compatibility regressions.

**Interfaces:**
- `_subscribe_events()` returns a readiness boolean and records which required subscriptions acknowledged.
- The existing `on_connected` callback fires only after authentication, message-loop startup, and all required subscription acknowledgements succeed.
- Failed subscription setup is treated as a failed transport attempt, so the existing reconnect counter and REST fallback controller receive the failure signal.
- Reconnect supervision owns one task, rechecks readiness after backoff, and awaits that task during shutdown so concurrent loops cannot replace the active socket.
- `HAConnector._get_health_state()` reports degraded until the connector has received the complete-ready callback; the health JSON exposes the readiness bit without exposing credentials or payloads.

- [ ] **Step 1: Add a failing test for incomplete subscription readiness.**

  Build a fake WebSocket command responder where `state_changed` times out while the other subscription commands succeed. Assert that `_subscribe_events()` reports not-ready, the client does not invoke the connected callback, and the connector remains degraded.

- [ ] **Step 2: Run the focused transport test and verify it fails for the readiness reason.**

  Run: `uv run pytest tests/connectors/test_home_assistant_transport_contract.py -q --tb=short`

- [ ] **Step 3: Implement the smallest lifecycle change.**

  Track subscription readiness separately from authentication, clear it when the socket closes, make subscription failure tear down the unusable stream and feed the existing reconnect/fallback signals, and move the connected callback behind the successful subscription result. Keep message-loop correlation alive while subscription commands await their results. Own the reconnect task, make its starter idempotent, recheck connection state after backoff, and cancel/await it during shutdown.

- [ ] **Step 4: Add the recovery and health regression tests.**

  Cover successful all-subscription readiness, failed reconnect subscription setup, fallback remaining active until readiness, readiness clearing on disconnect, the health endpoint’s degraded result before readiness, and singleton reconnect-task ownership through shutdown.

- [ ] **Step 5: Run the focused transport and existing HA tests.**

  Run: `uv run pytest tests/connectors/test_home_assistant_transport_contract.py tests/connectors/test_home_assistant_rest_fallback.py tests/connectors/test_home_assistant_connector.py tests/connectors/test_home_assistant_reorder_buffer.py -q --tb=short`

- [ ] **Step 6: Run Ruff on the touched Python files and commit the transport slice.**

  Run: `uv run ruff check src/butlers/connectors/home_assistant.py tests/connectors/test_home_assistant_transport_contract.py tests/connectors/test_home_assistant_rest_fallback.py tests/connectors/test_home_assistant_connector.py`

  Commit: `fix: make home assistant transport readiness truthful`

---

### Task 2: Preserve wellness promotion when ordinary HA ingress is skipped

**Owner:** Sol High subagent (`gpt-5.6-sol`, high reasoning)

**Files:**
- Modify: `src/butlers/connectors/home_assistant.py:2232-2317` for the policy/promotion ordering seam only.
- Modify: `src/butlers/connectors/home_assistant_wellness.py` only if a narrowly scoped classifier helper is required; preserve the strict weight rule.
- Modify: `roster/health/tools/wellness_ingest.py` only if the existing provider arm lacks a required contract; do not add a second storage path.
- Modify: `openspec/specs/connector-home-assistant/spec.md` only if the existing global-skip and wellness-promotion requirements need an explicit reconciliation.
- Test: `tests/connectors/test_home_assistant_global_skip.py` and any new focused wellness-policy test file.

**Interfaces:**
- A health-shaped numeric weight event matching `device_class=weight` plus `kg`/`lb` remains eligible for a `wellness/home_assistant` envelope even when the ordinary `home_assistant` envelope is skipped.
- Ordinary non-health HA events retain the current `global_rule:skip:source_channel` behavior.
- The wellness submission is the only submission attempted when the ordinary channel is skipped; checkpoint advancement and success/error accounting remain correct.

- [ ] **Step 1: Add failing tests for a weight event under global skip and a non-health event under global skip.**
- [ ] **Step 2: Run the focused tests and verify the new weight case fails before implementation.**
- [ ] **Step 3: Refactor the dispatcher so deterministic wellness classification is evaluated before the ordinary-channel skip, without widening the ordinary HA route.**
- [ ] **Step 4: Add assertions for exactly one wellness submission, no ordinary submission, no LLM classification, and preserved non-health skip behavior.**
- [ ] **Step 5: Run the focused global-skip/wellness tests and Ruff, then commit the policy slice.**

The subagent must not modify the WebSocket client lifecycle, fallback controller, or connector health methods owned by Task 1. It must report any unavoidable integration conflict instead of silently changing those regions.

---

### Task 3: Add durable measurement-history recovery for daily HA readings

**Owner:** Sol High subagent (`gpt-5.6-sol`, high reasoning)

**Files:**
- Modify or create: `src/butlers/connectors/home_assistant_rest.py` and adjacent connector-owned support code for history retrieval and a durable per-entity high-water mark.
- Modify: `src/butlers/connectors/home_assistant.py` only at the non-transport dispatcher/integration seam required to consume history-derived state changes.
- Modify: `roster/health/tools/wellness_ingest.py` only if the existing provider-agnostic idempotency contract needs a test-preserving adjustment.
- Test: new focused history/catch-up tests under `tests/connectors/` and the existing HA wellness/ingestion tests.

**Interfaces:**
- History-derived weight readings use the HA measurement timestamp as `valid_at`, not connector observation time.
- A same-value reading on a later day is a distinct fact; a duplicate delivery for the same entity/timestamp is a no-op.
- WebSocket and REST/history overlap is at-least-once and replay-safe.
- History retrieval failure is visible in connector health/logs and never advances the high-water mark.

- [ ] **Step 1: Add failing tests for outage catch-up, unchanged-value next-day readings, duplicate replay, and failed history fetch preserving the cursor.**
- [ ] **Step 2: Run the focused history tests and verify the new behavior fails before implementation.**
- [ ] **Step 3: Implement the smallest connector-owned history reader and cursor persistence using existing async HTTP/database patterns.**
- [ ] **Step 4: Wire history-derived weight readings through the existing deterministic wellness envelope and Health fact path.**
- [ ] **Step 5: Add low-cardinality metrics/logs for history poll success, failure, emitted measurements, and cursor age.**
- [ ] **Step 6: Run the focused history/HA suites and Ruff, then commit the recovery slice.**

The subagent must not modify the WebSocket readiness/fallback lifecycle owned by Task 1. If the history implementation requires a shared dispatcher edit, keep it limited to the named non-transport integration seam and document the exact hunk for reconciliation.

---

### Combined verification

- [ ] Rebase or cherry-pick the Sol High policy/history commit(s) onto the transport branch and resolve only the documented dispatcher overlap.
- [ ] Review the combined diff for scope, contract, and secret-handling hygiene.
- [ ] Run: `uv run pytest tests/connectors/test_home_assistant_transport_contract.py tests/connectors/test_home_assistant_rest_fallback.py tests/connectors/test_home_assistant_connector.py tests/connectors/test_home_assistant_global_skip.py tests/connectors/test_home_assistant_measurement_history.py tests/connectors/test_home_assistant_reorder_buffer.py -q --tb=short`
- [ ] Run: `uv run ruff check src/butlers/connectors/home_assistant.py src/butlers/connectors/home_assistant_rest.py src/butlers/connectors/home_assistant_wellness.py roster/health/tools/wellness_ingest.py tests/connectors/`
- [ ] Run: `uv run ruff format --check src/butlers/connectors/home_assistant.py src/butlers/connectors/home_assistant_rest.py src/butlers/connectors/home_assistant_wellness.py roster/health/tools/wellness_ingest.py tests/connectors/`
- [ ] Verify no live runtime, credential, or `.env.dev` files changed.
- [ ] Push the branch and open a PR against `main`; do not push directly to `main`.
