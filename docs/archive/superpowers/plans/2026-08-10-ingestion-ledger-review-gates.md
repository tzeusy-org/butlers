> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** Review-gate hardening shipped via the ingestion-ledger truth-and-replay-safety change.
> **Successor:** `openspec/changes/ingestion-ledger-truth-and-replay-safety`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# Ingestion Ledger Review Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the engineering-review gaps on the ingestion-ledger reliability change with executable safety and presentation regressions, then make the PR merge-ready.

**Architecture:** Production behavior remains unchanged unless a real migrated-database test exposes a defect. The replay-policy test executes the authoritative core transition against the core + switchboard schema, while focused API and React tests pin the externally visible cost-evidence contract. Documentation comments and formatting are kept consistent with the split between `unpriced` and `no_usage`.

**Tech Stack:** Python 3.13, pytest, asyncpg, PostgreSQL migrated-test harness, React, Vitest, TypeScript, Ruff.

## Global Constraints

- Preserve the server-authoritative, fail-closed replay policy: email, missing, false, and ambiguous policy rows must not mutate event state.
- Use a real migrated `core` + `switchboard` database and the actual `ingestion_event_replay_request()` core function for replay-transition coverage; no mocked SQL result is sufficient.
- Exercise both public ingestion events and partitioned filtered events.
- Do not add production dependencies or change production behavior solely to satisfy tests.
- Treat `unpriced` as token usage without a known rate; treat `no_usage` as neither token usage nor a stored cost.
- Run each added test in red before making a corresponding production correction; test-only regressions must still be run against the current implementation.

---

### Task 1: Execute replay policy against a migrated database

**Files:**
- Create: `tests/integration/test_ingestion_replay_policy_db.py`
- Read: `tests/integration/test_cursor_store_settings_jsonb_roundtrip.py`
- Read: `tests/integration/test_ingestion_events_histogram_db.py`
- Read: `src/butlers/core/ingestion_events.py`

**Interfaces:**
- Consumes: `ingestion_event_replay_request(pool, event_id, actor)`.
- Produces: real database coverage proving public and filtered replay transitions only occur with one active, replay-safe registry policy.

- [x] **Step 1: Write failing public-event policy tests**

```python
@pytest.mark.parametrize("policy", ["email", "missing", "unsafe", "ambiguous"])
async def test_public_replay_refuses_unsafe_policy(migrated_pool, policy):
    event_id = await seed_public_failed_event(migrated_pool, channel="telegram_bot")
    await seed_policy_shape(migrated_pool, policy)
    result = await ingestion_event_replay_request(migrated_pool, event_id, actor="test")
    assert result["outcome"] == "unsafe"
    assert await event_status(migrated_pool, event_id) == "failed"
```

- [x] **Step 2: Run the new public-event tests**

Run: `uv run pytest tests/integration/test_ingestion_replay_policy_db.py -q`

Expected: the test file fails until its migrated fixture and seed helpers are complete; a policy refusal must never be asserted through a mock.

- [x] **Step 3: Add migrated fixture, helpers, and real transition coverage**

```python
async def test_public_channel_candidate_can_replay(migrated_pool):
    event_id = await seed_public_failed_event(
        migrated_pool, channel="telegram_bot", provider="telegram"
    )
    await seed_registry(migrated_pool, connector_type="telegram_bot", replay_safe=True)
    result = await ingestion_event_replay_request(migrated_pool, event_id, actor="test")
    assert result["outcome"] == "ok"
    assert await event_status(migrated_pool, event_id) == "ingested"

async def test_filtered_channel_candidate_can_replay(migrated_pool):
    event_id = await seed_filtered_failed_event(
        migrated_pool, connector_type="telegram", source_channel="telegram_user_client"
    )
    await seed_registry(migrated_pool, connector_type="telegram_user_client", replay_safe=True)
    result = await ingestion_event_replay_request(migrated_pool, event_id, actor="test")
    assert result["outcome"] == "ok"
    assert await filtered_status(migrated_pool, event_id) == "replay_pending"
```

- [x] **Step 4: Prove the policy flip race is fail-closed**

```python
async with lock_policy_row_for_update(migrated_pool, connector_type="telegram_bot") as lock:
    replay = asyncio.create_task(
        ingestion_event_replay_request(migrated_pool, event_id, actor="test")
    )
    await wait_until_replay_waits_on_registry_lock(migrated_pool, lock)
    await lock.execute("UPDATE switchboard.connector_registry SET replay_safe = false ...")
await lock.commit()
assert (await replay)["outcome"] == "unsafe"
assert await event_status(migrated_pool, event_id) == "failed"
```

- [x] **Step 5: Run the complete integration file**

Run: `uv run pytest tests/integration/test_ingestion_replay_policy_db.py -q`

Expected: PASS with real migrations and no dev-database writes.

### Task 2: Pin cost-evidence presentation and serialized API semantics

**Files:**
- Modify: `frontend/src/components/ingestion/timeline/TimelineLedger.test.tsx`
- Modify: `tests/api/test_ingestion_events.py`
- Modify: `frontend/src/api/types.ts`

**Interfaces:**
- Consumes: timeline rollup `cost`, `unpriced_session_count`, and `no_usage_session_count`; list event session `cost_evidence`.
- Produces: UI and API regressions that distinguish no usable price from no usage.

- [x] **Step 1: Write the missing no-usage footer test**

```tsx
it("keeps no-usage session coverage visible when no subtotal is known", () => {
  renderLedger({ cost: null, no_usage_session_count: 2 });
  expect(screen.getByTestId("ledger-footer")).toHaveTextContent("2 no usage");
  expect(screen.getByTestId("ledger-footer")).not.toHaveTextContent("—");
  expect(screen.getByTestId("ledger-footer")).not.toHaveTextContent("unpriced");
});
```

- [x] **Step 2: Run the targeted React test**

Run: `npm test -- --run src/components/ingestion/timeline/TimelineLedger.test.tsx`

Expected: the new test fails if the no-usage footer branch is absent or renders an unpriced/em-dash fallback.

- [x] **Step 3: Add API serialization assertions and clarify type comments**

```python
assert item["sessions"][0]["cost_evidence"] == "unpriced"
```

```ts
/** Token-using sessions omitted from `cost_usd` because their price is unavailable. */
unpriced_session_count?: number;
```

- [x] **Step 4: Run the focused API and React tests**

Run: `uv run pytest tests/api/test_ingestion_events.py -q && npm test -- --run src/components/ingestion/timeline/TimelineLedger.test.tsx`

Expected: PASS; the API payload exposes the computed cost evidence and the footer distinguishes `no_usage` from `unpriced`.

### Task 3: Remove mechanical merge blockers and verify the branch

**Files:**
- Modify: `tests/core/test_ingestion_events.py` (Ruff formatting only if needed)
- Verify: `frontend/src/components/ingestion/timeline/TimelineLedger.test.tsx` (the
  corrected coverage-only expectation landed with Task 2)

**Interfaces:**
- Consumes: existing `formatCostEvidence` behavior.
- Produces: a host-CI-compatible regression expectation and format-clean Python test file.

- [x] **Step 1: Confirm the stale hosted assertion failed against current behavior**

Run: `npm test -- --run src/components/ingestion/timeline/TimelineLedger.test.tsx`

Observed before Task 2: the obsolete `—` expectation failed when the renderer
correctly emitted coverage-only `2 unpriced`.

- [x] **Step 2: Correct only the stale assertion**

```tsx
expect(rollup).toHaveTextContent("2 unpriced");
expect(rollup).not.toHaveTextContent("—");
```


The correction is committed with Task 2. The remaining formatter work is Task
3's implementation step.

- [x] **Step 3: Format the Python test file and run targeted checks**

Run: `uv run ruff format tests/core/test_ingestion_events.py && uv run ruff format --check tests/core/test_ingestion_events.py && git diff --check`

Expected: PASS with no whitespace errors or stale cost display expectation.

### Task 4: Make replay-policy update targets locally explicit

**Files:**
- Modify: `src/butlers/core/ingestion_events.py`
- Test: `tests/integration/test_ingestion_replay_policy_db.py`

**Interfaces:**
- Consumes: the public `ie` and filtered `fe` update aliases in
  `ingestion_event_replay_request()`.
- Produces: explicit `ie.source_channel` and `fe.source_channel` email guards
  alongside the shared one-safe-policy predicate.

- [x] **Step 1: Establish a real-DB refactor baseline**

Run: `uv run pytest tests/integration/test_ingestion_replay_policy_db.py -q`

Expected: PASS before the readability-only refactor; this confirms the public
and filtered transition semantics that must remain unchanged.

- [x] **Step 2: Split the target-qualified email guards from the common safe-policy guard**

```python
_REPLAY_POLICY_SAFE_GUARD = """
AND COALESCE(
    (SELECT COUNT(*) = 1 AND BOOL_AND(replay_safe IS TRUE) FROM replay_policy),
    FALSE
)
"""

_INGESTED_REPLAY_POLICY_PREDICATE = f"""
AND LOWER(COALESCE(ie.source_channel, '')) <> 'email'
{_REPLAY_POLICY_SAFE_GUARD}
"""
_FILTERED_REPLAY_POLICY_PREDICATE = f"""
AND LOWER(COALESCE(fe.source_channel, '')) <> 'email'
{_REPLAY_POLICY_SAFE_GUARD}
"""
```

- [x] **Step 3: Use the matching explicit predicate in each update and rerun coverage**

Run: `uv run pytest tests/integration/test_ingestion_replay_policy_db.py -q && uv run ruff check src/butlers/core/ingestion_events.py && uv run ruff format --check src/butlers/core/ingestion_events.py`

Expected: PASS; public and filtered behavior is unchanged while no SQL fragment
relies on unqualified `source_channel` resolution.

### Task 5: Prove filtered replay fails closed across a policy-flip race

**Files:**
- Modify: `tests/integration/test_ingestion_replay_policy_db.py`

**Interfaces:**
- Consumes: `_seed_filtered_event`, `_seed_registry`, `_locked_registry_row`,
  and `ingestion_event_replay_request()`.
- Produces: a real PostgreSQL lock-race regression for the distinct
  `UPDATE connectors.filtered_events AS fe` mutation path.

- [x] **Step 1: Add the filtered policy-flip test before altering test helpers**

```python
async def test_filtered_replay_fails_closed_when_policy_flips_while_locked(pool):
    event_id = await _seed_filtered_event(
        pool, connector_type="telegram", source_channel="telegram_user_client"
    )
    await _seed_registry(
        pool,
        connector_type="telegram_user_client",
        endpoint_identity=_FILTERED_ENDPOINT,
        replay_safe=True,
    )
    async with _locked_registry_row(
        pool,
        connector_type="telegram_user_client",
        endpoint_identity=_FILTERED_ENDPOINT,
    ) as locked_policy:
        replay = asyncio.create_task(ingestion_event_replay_request(pool, event_id))
        await _wait_until_replay_waits_on_registry_lock(
            pool, update_relation="connectors.filtered_events"
        )
        await locked_policy.execute("UPDATE ... SET replay_safe = FALSE ...")
    assert (await replay)["outcome"] == "unsafe"
    assert await _filtered_status(pool, event_id) == "filtered"
```

- [x] **Step 2: Run the new filtered-race test and make its lock observer target-aware**

Run: `uv run pytest -n 0 tests/integration/test_ingestion_replay_policy_db.py::test_filtered_replay_fails_closed_when_policy_flips_while_locked -q -rA`

Expected: the test must prove the replay task waits on PostgreSQL's registry
lock before flipping policy, then passes only when the persisted filtered row
remains `filtered`.

- [x] **Step 3: Run the full migrated replay-policy file**

Run: `uv run pytest tests/integration/test_ingestion_replay_policy_db.py -q -rA && uv run ruff check tests/integration/test_ingestion_replay_policy_db.py && uv run ruff format --check tests/integration/test_ingestion_replay_policy_db.py && git diff --check`

Expected: PASS with real public and filtered atomic-race coverage.
