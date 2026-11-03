"""Tests for the consolidation_runs audit table + write-on-completion (bu-awo8k.1).

Covers:
  (a) Unit — core_119 migration structure, revision wiring, the additive-only
      (no-drop) guarantee, and source-text guards.
  (b) Unit — write-on-completion logic in run_consolidation:
        - _record_consolidation_run emits the expected INSERT and is best-effort.
        - run_consolidation writes one audit row per successfully consolidated
          (tenant, butler) group, with the group's counts.

The table's privileges on the real core chain (append-only for butler roles,
core_308) are pinned in tests/migrations/test_consolidation_runs_grants.py.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

import pytest

from butlers.modules.memory import consolidation as consolidation_module
from butlers.testing.fake_pool import AsyncCtx

# ---------------------------------------------------------------------------
# Migration file path / loader
# ---------------------------------------------------------------------------

_CORE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "core"
    / "core_119_consolidation_runs.py"
)


def _load_core():
    spec = importlib.util.spec_from_file_location("core_119", _CORE_MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# (a) Unit — migration structure and source-text guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCore119Structure:
    def test_migration_wiring(self):
        """core_119 exists, declares its revision/down_revision chain, and is callable."""
        assert _CORE_MIGRATION_PATH.exists(), f"Migration file not found: {_CORE_MIGRATION_PATH}"
        mod = _load_core()
        assert mod.revision == "core_119"
        assert mod.down_revision == "core_118"  # chains from the current core head
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)

    def test_creates_table_with_all_contract_columns(self):
        src = _CORE_MIGRATION_PATH.read_text()
        assert "CREATE TABLE IF NOT EXISTS public.consolidation_runs" in src
        for column in (
            "id",
            "butler",
            "consolidated_at",
            "episodes_processed",
            "facts_produced",
            "facts_updated",
            "rules_created",
            "confirmations_made",
            "errors",
        ):
            assert column in src, f"contract column {column!r} missing from migration"

    def test_additive_only_no_drops_in_upgrade(self):
        # ADDITIVE-ONLY: upgrade() must not DROP or ALTER any existing table.
        src = _CORE_MIGRATION_PATH.read_text()
        upgrade_src = src.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
        assert "DROP TABLE" not in upgrade_src
        assert "ALTER TABLE" not in upgrade_src
        assert "DROP COLUMN" not in upgrade_src

    def test_grant_uses_best_effort_role_guard(self):
        src = _CORE_MIGRATION_PATH.read_text()
        # Tolerates DBs missing some roles, like sibling public-table migrations.
        assert "pg_roles WHERE rolname" in src

    def test_downgrade_drops_only_the_new_table(self):
        src = _CORE_MIGRATION_PATH.read_text()
        downgrade_src = src.split("def downgrade()", 1)[1]
        assert "DROP TABLE IF EXISTS public.consolidation_runs" in downgrade_src
        # Must not touch any pre-existing memory table.
        assert "episodes" not in downgrade_src
        assert "facts" not in downgrade_src


# ---------------------------------------------------------------------------
# Fakes for the write-on-completion logic
# ---------------------------------------------------------------------------


_FakeTransaction = AsyncCtx


_FakeAcquire = AsyncCtx


class _FakeConn:
    """Connection used during the FOR UPDATE SKIP LOCKED claim phase."""

    def __init__(self, claim_rows: list[dict], statements: list[tuple]):
        self._claim_rows = claim_rows
        self._statements = statements
        self.executes: list[tuple] = []

    def is_in_transaction(self):
        return True

    def transaction(self):
        return _FakeTransaction(None)

    async def fetch(self, query, *args, **kwargs):
        if "last_consolidation_error=" in query:
            self._statements.append((query, args))
            return [
                dict(
                    row,
                    consolidation_status="failed",
                    consolidation_attempts=row["consolidation_attempts"] + 1,
                )
                for row in self._claim_rows
            ]
        return self._claim_rows

    async def execute(self, *args, **kwargs):
        self.executes.append(args)
        return "UPDATE 0"


class _FakePool:
    """Records every execute() call so audit-row INSERTs can be asserted."""

    def __init__(self, claim_rows: list[dict], *, execute_should_raise: bool = False):
        self.executes: list[tuple] = []
        self._conn = _FakeConn(claim_rows, self.executes)
        self.fetch_results: list[list[dict]] = []
        self._execute_should_raise = execute_should_raise

    def acquire(self):
        return _FakeAcquire(self._conn)

    async def fetch(self, *args, **kwargs):
        # existing-facts / existing-rules dedup context fetches — empty is fine.
        return []

    async def execute(self, query: str, *args, **kwargs):
        self.executes.append((query, args))
        if self._execute_should_raise:
            raise RuntimeError("simulated audit-write failure")
        return "INSERT 0 1"

    def consolidation_run_inserts(self) -> list[tuple]:
        return [
            (query, args)
            for (query, args) in self.executes
            if "INSERT INTO public.consolidation_runs" in query
        ]


class _FakeSpawnerResult:
    def __init__(
        self,
        *,
        success: bool = True,
        output: str | None = "{}",
        error: str | None = None,
    ):
        self.success = success
        self.output = output
        self.error = error


class _FakeSpawner:
    def __init__(
        self,
        *,
        success: bool = True,
        output: str | None = "{}",
        error: str | None = None,
    ):
        self.calls = 0
        self.trigger_sources: list[str] = []
        self.success = success
        self.output = output
        self.error = error

    async def trigger(self, *, prompt: str, trigger_source: str):
        self.calls += 1
        self.trigger_sources.append(trigger_source)
        return _FakeSpawnerResult(
            success=self.success,
            output=self.output,
            error=self.error,
        )


# ---------------------------------------------------------------------------
# (b) Unit — _record_consolidation_run
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecordConsolidationRun:
    async def test_emits_insert_with_expected_columns_and_params(self):
        pool = _FakePool(claim_rows=[])
        await consolidation_module._record_consolidation_run(
            pool,
            butler="general",
            episodes_processed=4,
            facts_produced=3,
            facts_updated=2,
            rules_created=1,
            confirmations_made=5,
            errors=0,
        )
        inserts = pool.consolidation_run_inserts()
        assert len(inserts) == 1
        query, args = inserts[0]
        for column in (
            "butler",
            "episodes_processed",
            "facts_produced",
            "facts_updated",
            "rules_created",
            "confirmations_made",
            "errors",
        ):
            assert column in query
        # Params are positional in column order.
        assert args == ("general", 4, 3, 2, 1, 5, 0)

    async def test_best_effort_swallows_db_errors(self):
        pool = _FakePool(claim_rows=[], execute_should_raise=True)
        # Must not raise even when the INSERT fails (table not yet migrated, etc.).
        await consolidation_module._record_consolidation_run(
            pool,
            butler="general",
            episodes_processed=1,
            facts_produced=0,
            facts_updated=0,
            rules_created=0,
            confirmations_made=0,
            errors=0,
        )


# ---------------------------------------------------------------------------
# (b) Unit — run_consolidation writes one audit row per successful group
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunConsolidationWritesAuditRow:
    async def test_empty_backlog_does_not_spawn(self):
        pool = _FakePool(claim_rows=[])
        spawner = _FakeSpawner()

        stats = await consolidation_module.run_consolidation(
            pool,
            embedding_engine=object(),
            cc_spawner=spawner,
        )

        assert stats["episodes_processed"] == 0
        assert stats["groups"] == {}
        assert spawner.calls == 0

    async def test_one_audit_row_per_successful_group_with_counts(self, monkeypatch):
        claim_rows = [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "butler": "general",
                "content": "c",
                "importance": 5.0,
                "metadata": {},
                "created_at": None,
                "tenant_id": "t1",
                "consolidation_attempts": 0,
            }
        ]
        pool = _FakePool(claim_rows=claim_rows)

        exec_result = {
            "facts_created": 3,
            "facts_updated": 2,
            "rules_created": 1,
            "confirmations_made": 4,
            "episodes_consolidated": 1,
            "episode_ttl_days": None,
            "errors": [],
        }

        async def _fake_execute_consolidation(**kwargs: Any) -> dict[str, Any]:
            return exec_result

        monkeypatch.setattr(
            consolidation_module, "execute_consolidation", _fake_execute_consolidation
        )
        monkeypatch.setattr(
            consolidation_module,
            "parse_consolidation_output",
            lambda output: type("P", (), {"parse_errors": []})(),
        )
        monkeypatch.setattr(
            consolidation_module, "build_consolidation_prompt", lambda **kwargs: "prompt"
        )

        spawner = _FakeSpawner()
        stats = await consolidation_module.run_consolidation(
            pool,
            embedding_engine=None,
            cc_spawner=spawner,
        )

        inserts = pool.consolidation_run_inserts()
        assert len(inserts) == 1, "expected exactly one audit row for one successful group"
        _query, args = inserts[0]
        # (butler, episodes_processed, facts_produced, facts_updated,
        #  rules_created, confirmations_made, errors)
        assert args == ("general", 1, 3, 2, 1, 4, 0)
        # Aggregate stats still returned as before.
        assert stats["facts_created"] == 3
        assert stats["groups_consolidated"] == 1
        assert spawner.trigger_sources == ["schedule:consolidation"]

    async def test_same_butler_in_two_tenants_gets_disjoint_groups_and_request_ids(
        self, monkeypatch
    ) -> None:
        tenant_a_ids = [uuid.uuid4(), uuid.uuid4()]
        tenant_b_ids = [uuid.uuid4()]
        claim_rows = [
            {
                "id": episode_id,
                "butler": "general",
                "content": f"episode-{episode_id}",
                "importance": 5.0,
                "metadata": {},
                "created_at": None,
                "tenant_id": tenant_id,
                "consolidation_attempts": 0,
            }
            for tenant_id, episode_ids in (
                ("tenant-a", tenant_a_ids),
                ("tenant-b", tenant_b_ids),
            )
            for episode_id in episode_ids
        ]
        pool = _FakePool(claim_rows=claim_rows)
        execute_calls: list[dict[str, Any]] = []

        async def _fake_execute_consolidation(**kwargs: Any) -> dict[str, Any]:
            execute_calls.append(kwargs)
            return {
                "facts_created": 0,
                "facts_updated": 0,
                "rules_created": 0,
                "confirmations_made": 0,
                "episodes_consolidated": len(kwargs["source_episode_ids"]),
                "episode_ttl_days": None,
                "errors": [],
            }

        monkeypatch.setattr(
            consolidation_module, "execute_consolidation", _fake_execute_consolidation
        )
        monkeypatch.setattr(
            consolidation_module,
            "parse_consolidation_output",
            lambda output: type("P", (), {"parse_errors": []})(),
        )
        monkeypatch.setattr(
            consolidation_module, "build_consolidation_prompt", lambda **kwargs: "prompt"
        )

        stats = await consolidation_module.run_consolidation(
            pool,
            embedding_engine=None,
            cc_spawner=_FakeSpawner(),
        )

        assert stats["groups"] == {"tenant-a/general": 2, "tenant-b/general": 1}
        assert stats["groups_consolidated"] == 2
        assert len(execute_calls) == 2

        calls_by_tenant = {call["tenant_id"]: call for call in execute_calls}
        assert set(calls_by_tenant) == {"tenant-a", "tenant-b"}
        assert calls_by_tenant["tenant-a"]["source_episode_ids"] == tenant_a_ids
        assert calls_by_tenant["tenant-b"]["source_episode_ids"] == tenant_b_ids

        request_ids = [call["request_id"] for call in execute_calls]
        assert all(uuid.UUID(request_id).version == 4 for request_id in request_ids)
        assert len(set(request_ids)) == 2

    async def test_each_authority_partition_consolidates_in_its_own_session(
        self, monkeypatch
    ) -> None:
        """REQ-module-memory-005: one tenant/butler group splits by persisted authority."""
        owner_ids = [uuid.uuid4(), uuid.uuid4()]
        system_ids = [uuid.uuid4()]
        other_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        authorities = {
            owner_ids[0]: "owner",
            other_ids[0]: "third_party",
            system_ids[0]: "system",
            owner_ids[1]: "owner_device",
            other_ids[1]: None,
            other_ids[2]: "mixed",
        }
        claim_rows = [
            {
                "id": episode_id,
                "butler": "general",
                "content": f"episode-{episode_id}",
                "importance": 5.0,
                "metadata": {},
                "created_at": None,
                "tenant_id": "t1",
                "consolidation_attempts": 0,
                "content_authority": authority,
            }
            # Interleave so partitioning cannot lean on claim order.
            for episode_id, authority in authorities.items()
        ]
        pool = _FakePool(claim_rows=claim_rows)
        dedup_queries: list[str] = []

        async def _recording_fetch(query: str, *args: Any) -> list[dict]:
            dedup_queries.append(" ".join(query.split()))
            return []

        pool.fetch = _recording_fetch  # type: ignore[method-assign]
        prompts: list[list[uuid.UUID]] = []
        execute_calls: list[dict[str, Any]] = []

        async def _fake_execute_consolidation(**kwargs: Any) -> dict[str, Any]:
            execute_calls.append(kwargs)
            return {
                "facts_created": 0,
                "facts_updated": 0,
                "rules_created": 0,
                "confirmations_made": 0,
                "episodes_consolidated": len(kwargs["source_episode_ids"]),
                "episode_ttl_days": None,
                "errors": [],
            }

        def _prompt(**kwargs: Any) -> str:
            prompts.append([episode["id"] for episode in kwargs["episodes"]])
            return "prompt"

        monkeypatch.setattr(
            consolidation_module, "execute_consolidation", _fake_execute_consolidation
        )
        monkeypatch.setattr(
            consolidation_module,
            "parse_consolidation_output",
            lambda output: type("P", (), {"parse_errors": []})(),
        )
        monkeypatch.setattr(consolidation_module, "build_consolidation_prompt", _prompt)
        spawner = _FakeSpawner()

        stats = await consolidation_module.run_consolidation(
            pool, embedding_engine=None, cc_spawner=spawner
        )

        # A system-stamped episode gets its own session (bu-dhloi1.26): sharing one
        # with third-party content would let it restate that content as system.
        assert spawner.calls == 3
        assert prompts == [owner_ids, system_ids, other_ids]
        assert [call["source_episode_ids"] for call in execute_calls] == [
            owner_ids,
            system_ids,
            other_ids,
        ]
        assert [call["authority_partition"] for call in execute_calls] == [
            "owner",
            "system",
            "non_owner",
        ]
        assert len({call["request_id"] for call in execute_calls}) == 3
        assert stats["groups"] == {"t1/general": 6}
        assert stats["groups_consolidated"] == 3
        assert stats["episodes_consolidated"] == 6
        assert len(pool.consolidation_run_inserts()) == 3

        # Dedup candidates are filtered by partition in SQL, before LIMIT, so a
        # third-party fact or rule never reaches the owner or system prompt.
        assert len(dedup_queries) == 6
        predicates = [
            "content_authority IN ('owner', 'owner_device')",
            "content_authority = 'system'",
            "COALESCE(content_authority, '') NOT IN ('owner', 'owner_device', 'system')",
        ]
        for index, query in enumerate(dedup_queries):
            predicate = predicates[index // 2]
            assert predicate in query
            assert query.index(predicate) < query.index("LIMIT")

    async def test_failed_partition_does_not_block_the_other_partition(self, monkeypatch) -> None:
        owner_id, other_id = uuid.uuid4(), uuid.uuid4()
        claim_rows = [
            {
                "id": episode_id,
                "butler": "general",
                "content": "c",
                "importance": 5.0,
                "metadata": {},
                "created_at": None,
                "tenant_id": "t1",
                "consolidation_attempts": 0,
                "content_authority": authority,
            }
            for episode_id, authority in ((owner_id, "owner"), (other_id, "third_party"))
        ]
        pool = _FakePool(claim_rows=claim_rows)

        async def _fake_execute_consolidation(**kwargs: Any) -> dict[str, Any]:
            if kwargs["authority_partition"] == "owner":
                raise ValueError("out-of-partition evidence")
            return {
                "facts_created": 1,
                "facts_updated": 0,
                "rules_created": 0,
                "confirmations_made": 0,
                "episodes_consolidated": 1,
                "episode_ttl_days": None,
                "errors": [],
            }

        monkeypatch.setattr(
            consolidation_module, "execute_consolidation", _fake_execute_consolidation
        )
        monkeypatch.setattr(
            consolidation_module,
            "parse_consolidation_output",
            lambda output: type("P", (), {"parse_errors": []})(),
        )
        monkeypatch.setattr(
            consolidation_module, "build_consolidation_prompt", lambda **kwargs: "prompt"
        )

        stats = await consolidation_module.run_consolidation(
            pool, embedding_engine=None, cc_spawner=_FakeSpawner()
        )

        assert stats["groups_consolidated"] == 1
        assert stats["facts_created"] == 1
        assert stats["errors"] == ["Failed to consolidate general"]
        failure_updates = [
            args for query, args in pool.executes if "last_consolidation_error" in query
        ]
        assert len(failure_updates) == 1
        assert [owner_id] in failure_updates[0]
        assert len(pool.consolidation_run_inserts()) == 1

    async def test_no_audit_row_when_no_spawner(self, monkeypatch):
        # Without a spawner, no group is consolidated → no audit row written.
        claim_rows = [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "butler": "general",
                "content": "c",
                "importance": 5.0,
                "metadata": {},
                "created_at": None,
                "tenant_id": "t1",
                "consolidation_attempts": 0,
            }
        ]
        pool = _FakePool(claim_rows=claim_rows)

        stats = await consolidation_module.run_consolidation(
            pool,
            embedding_engine=None,
            cc_spawner=None,
        )
        assert pool.consolidation_run_inserts() == []
        assert stats["groups_consolidated"] == 0

    @pytest.mark.parametrize("output", [None, " \n\t"])
    async def test_missing_runtime_output_records_actionable_failure(
        self, output: str | None
    ) -> None:
        claim_rows = [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "butler": "switchboard",
                "content": "c",
                "importance": 5.0,
                "metadata": {},
                "created_at": None,
                "tenant_id": "t1",
                "consolidation_attempts": 0,
            }
        ]
        pool = _FakePool(claim_rows=claim_rows)

        stats = await consolidation_module.run_consolidation(
            pool,
            embedding_engine=None,
            cc_spawner=_FakeSpawner(output=output),
        )

        detail = "Consolidation runtime returned no output."
        assert stats["groups_consolidated"] == 0
        assert stats["errors"] == [f"runtime session failed for switchboard: {detail}"]
        failure_updates = [
            args for query, args in pool.executes if "last_consolidation_error" in query
        ]
        assert failure_updates[0][0] == detail

    async def test_unsuccessful_runtime_without_error_records_fallback_detail(self) -> None:
        claim_rows = [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "butler": "switchboard",
                "content": "c",
                "importance": 5.0,
                "metadata": {},
                "created_at": None,
                "tenant_id": "t1",
                "consolidation_attempts": 0,
            }
        ]
        pool = _FakePool(claim_rows=claim_rows)

        stats = await consolidation_module.run_consolidation(
            pool,
            embedding_engine=None,
            cc_spawner=_FakeSpawner(success=False),
        )

        detail = "Consolidation runtime reported a failure."
        assert stats["groups_consolidated"] == 0
        assert stats["errors"] == [f"runtime session failed for switchboard: {detail}"]
        failure_updates = [
            args for query, args in pool.executes if "last_consolidation_error" in query
        ]
        assert failure_updates[0][0] == detail

    async def test_invalid_evidence_uses_existing_group_failure_path(self) -> None:
        claim_rows = [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "butler": "relationship",
                "content": "c",
                "importance": 5.0,
                "metadata": {},
                "created_at": None,
                "tenant_id": "t1",
                "consolidation_attempts": 0,
            }
        ]
        pool = _FakePool(claim_rows=claim_rows)

        stats = await consolidation_module.run_consolidation(
            pool,
            embedding_engine=None,
            cc_spawner=_FakeSpawner(
                output=(
                    '{"new_facts": [{"subject": "owner", '
                    '"predicate": "preference", "content": "likes quiet dinners"}]}'
                )
            ),
        )

        assert stats["groups_consolidated"] == 0
        assert stats["errors"] == ["Failed to consolidate relationship"]
        assert pool.consolidation_run_inserts() == []
        failure_updates = [
            args for query, args in pool.executes if "last_consolidation_error" in query
        ]
        assert failure_updates[0][0] == "Consolidation execution failed."
