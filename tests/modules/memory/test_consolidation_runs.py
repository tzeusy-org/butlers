"""Tests for the consolidation_runs audit table + write-on-completion (bu-awo8k.1).

Covers:
  (a) Unit — core_119 migration structure, revision wiring, the additive-only
      (no-drop) guarantee, and source-text guards.
  (b) Write-on-completion logic in run_consolidation, on the real core + memory
      chains (claims, failure transitions and dedup reads are real SQL):
        - _record_consolidation_run writes the audit row and is best-effort.
        - run_consolidation writes one audit row per successfully consolidated
          (tenant, butler) authority partition, with its counts.

The table's privileges on the real core chain (append-only for butler roles,
core_308) are pinned in tests/migrations/test_consolidation_runs_grants.py.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.modules.memory import consolidation as consolidation_module
from butlers.testing.fake_pool import fake_pool
from butlers.testing.migration import load_migration

_db_backed = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# Migration file path
# ---------------------------------------------------------------------------

_CORE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "core"
    / "core_119_consolidation_runs.py"
)


# ---------------------------------------------------------------------------
# (a) Unit — migration structure and source-text guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCore119Structure:
    def test_migration_wiring(self):
        """core_119 exists, declares its revision/down_revision chain, and is callable."""
        assert _CORE_MIGRATION_PATH.exists(), f"Migration file not found: {_CORE_MIGRATION_PATH}"
        mod = load_migration(_CORE_MIGRATION_PATH)
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
# Real-DB seeding and runtime doubles
# ---------------------------------------------------------------------------


@pytest.fixture
async def memory_pool(db):
    """``episodes``, ``facts``, ``rules`` and ``public.consolidation_runs``, flat in public."""
    return await db("core", "memory")


_BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


async def _seed_episodes(
    pool,
    *episodes: tuple[str, str, str | None],
) -> list[uuid.UUID]:
    """Insert pending ``(tenant_id, butler, content_authority)`` episodes in claim order.

    ``created_at`` increases with position so the real claim order
    (tenant, butler, created_at, id) is the argument order within a group.
    """
    ids = [uuid.uuid4() for _ in episodes]
    await pool.executemany(
        "INSERT INTO episodes (id, tenant_id, butler, content, content_authority, created_at) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        [
            (episode_id, tenant, butler, f"episode {i}", authority, _BASE_TIME + timedelta(i))
            for i, (episode_id, (tenant, butler, authority)) in enumerate(
                zip(ids, episodes, strict=True)
            )
        ],
    )
    return ids


async def _audit_rows(pool) -> list[tuple]:
    rows = await pool.fetch(
        "SELECT butler, episodes_processed, facts_produced, facts_updated, rules_created, "
        "confirmations_made, errors FROM public.consolidation_runs ORDER BY id"
    )
    return [tuple(r) for r in rows]


async def _episode_state(pool, episode_id: uuid.UUID) -> tuple:
    return tuple(
        await pool.fetchrow(
            "SELECT consolidation_status, last_consolidation_error FROM episodes WHERE id = $1",
            episode_id,
        )
    )


def _exec_result(episodes: int, **counts: int) -> dict[str, Any]:
    return {
        "facts_created": counts.get("facts_created", 0),
        "facts_updated": counts.get("facts_updated", 0),
        "rules_created": counts.get("rules_created", 0),
        "confirmations_made": counts.get("confirmations_made", 0),
        "episodes_consolidated": episodes,
        "episode_ttl_days": None,
        "errors": [],
    }


def _stub_runtime_pipeline(monkeypatch, execute_consolidation) -> None:
    """Replace parse + execute so the test controls each partition's outcome."""
    monkeypatch.setattr(consolidation_module, "execute_consolidation", execute_consolidation)
    monkeypatch.setattr(
        consolidation_module,
        "parse_consolidation_output",
        lambda output: type("P", (), {"parse_errors": []})(),
    )


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
# (b) _record_consolidation_run
# ---------------------------------------------------------------------------


class TestRecordConsolidationRun:
    pytestmark = _db_backed

    async def test_writes_one_audit_row_with_the_given_counts(self, memory_pool):
        await consolidation_module._record_consolidation_run(
            memory_pool,
            butler="general",
            episodes_processed=4,
            facts_produced=3,
            facts_updated=2,
            rules_created=1,
            confirmations_made=5,
            errors=0,
        )
        assert await _audit_rows(memory_pool) == [("general", 4, 3, 2, 1, 5, 0)]


@pytest.mark.unit
class TestRecordConsolidationRunBestEffort:
    async def test_best_effort_swallows_db_errors(self):
        pool = fake_pool(execute=AsyncMock(side_effect=RuntimeError("simulated audit failure")))
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
        pool.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# (b) run_consolidation writes one audit row per successful partition
# ---------------------------------------------------------------------------


class TestRunConsolidationWritesAuditRow:
    pytestmark = _db_backed

    async def test_empty_backlog_does_not_spawn(self, memory_pool):
        spawner = _FakeSpawner()

        stats = await consolidation_module.run_consolidation(
            memory_pool,
            embedding_engine=object(),
            cc_spawner=spawner,
        )

        assert stats["episodes_processed"] == 0
        assert stats["groups"] == {}
        assert spawner.calls == 0
        assert await _audit_rows(memory_pool) == []

    async def test_one_audit_row_per_successful_group_with_counts(self, memory_pool, monkeypatch):
        await _seed_episodes(memory_pool, ("t1", "general", None))

        async def _fake_execute_consolidation(**kwargs: Any) -> dict[str, Any]:
            return _exec_result(
                1, facts_created=3, facts_updated=2, rules_created=1, confirmations_made=4
            )

        _stub_runtime_pipeline(monkeypatch, _fake_execute_consolidation)
        spawner = _FakeSpawner()
        stats = await consolidation_module.run_consolidation(
            memory_pool,
            embedding_engine=None,
            cc_spawner=spawner,
        )

        # (butler, episodes_processed, facts_produced, facts_updated,
        #  rules_created, confirmations_made, errors)
        assert await _audit_rows(memory_pool) == [("general", 1, 3, 2, 1, 4, 0)]
        # Aggregate stats still returned as before.
        assert stats["facts_created"] == 3
        assert stats["groups_consolidated"] == 1
        assert spawner.trigger_sources == ["schedule:consolidation"]

    async def test_same_butler_in_two_tenants_gets_disjoint_groups_and_request_ids(
        self, memory_pool, monkeypatch
    ) -> None:
        ids = await _seed_episodes(
            memory_pool,
            ("tenant-a", "general", None),
            ("tenant-b", "general", None),
            ("tenant-a", "general", None),
        )
        tenant_a_ids, tenant_b_ids = [ids[0], ids[2]], [ids[1]]
        execute_calls: list[dict[str, Any]] = []

        async def _fake_execute_consolidation(**kwargs: Any) -> dict[str, Any]:
            execute_calls.append(kwargs)
            return _exec_result(len(kwargs["source_episode_ids"]))

        _stub_runtime_pipeline(monkeypatch, _fake_execute_consolidation)
        stats = await consolidation_module.run_consolidation(
            memory_pool,
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
        self, memory_pool, monkeypatch
    ) -> None:
        """REQ-module-memory-005: one tenant/butler group splits by persisted authority."""
        # Interleave so partitioning cannot lean on claim order.
        authorities = ["owner", "third_party", "system", "owner_device", None, "mixed"]
        ids = await _seed_episodes(
            memory_pool, *[("t1", "general", authority) for authority in authorities]
        )
        owner_ids = [ids[0], ids[3]]
        system_ids = [ids[2]]
        other_ids = [ids[1], ids[4], ids[5]]

        # Dedup candidates: one owner-class and one system fact/rule, buried under
        # a full LIMIT's worth of newer third-party ones (facts LIMIT 100, rules
        # LIMIT 50). Filtering by partition before LIMIT is the only way the
        # owner and system sessions still see theirs.
        await memory_pool.executemany(
            "INSERT INTO facts (subject, predicate, content, source_butler, tenant_id, "
            "content_authority, created_at) VALUES ($1, 'note', $1, 'general', 't1', $2, $3)",
            [("owner-fact", "owner", _BASE_TIME), ("system-fact", "system", _BASE_TIME)]
            + [
                (f"third-party-fact-{i}", "third_party", _BASE_TIME + timedelta(days=1, seconds=i))
                for i in range(100)
            ],
        )
        await memory_pool.executemany(
            "INSERT INTO rules (content, source_butler, tenant_id, content_authority, created_at) "
            "VALUES ($1, 'general', 't1', $2, $3)",
            [("owner-rule", "owner_device", _BASE_TIME), ("system-rule", "system", _BASE_TIME)]
            + [
                (f"third-party-rule-{i}", "third_party", _BASE_TIME + timedelta(days=1, seconds=i))
                for i in range(50)
            ],
        )

        prompts: list[dict[str, Any]] = []
        execute_calls: list[dict[str, Any]] = []

        async def _fake_execute_consolidation(**kwargs: Any) -> dict[str, Any]:
            execute_calls.append(kwargs)
            return _exec_result(len(kwargs["source_episode_ids"]))

        def _prompt(**kwargs: Any) -> str:
            prompts.append(kwargs)
            return "prompt"

        _stub_runtime_pipeline(monkeypatch, _fake_execute_consolidation)
        monkeypatch.setattr(consolidation_module, "build_consolidation_prompt", _prompt)
        spawner = _FakeSpawner()

        stats = await consolidation_module.run_consolidation(
            memory_pool, embedding_engine=None, cc_spawner=spawner
        )

        # A system-stamped episode gets its own session (bu-dhloi1.26): sharing one
        # with third-party content would let it restate that content as system.
        assert spawner.calls == 3
        assert [[e["id"] for e in p["episodes"]] for p in prompts] == [
            owner_ids,
            system_ids,
            other_ids,
        ]
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
        assert [r[1] for r in await _audit_rows(memory_pool)] == [2, 1, 3]

        # Dedup candidates are filtered by partition in SQL, before LIMIT, so a
        # third-party fact or rule never reaches the owner or system prompt.
        owner, system, non_owner = prompts
        assert [f["subject"] for f in owner["existing_facts"]] == ["owner-fact"]
        assert [r["content"] for r in owner["existing_rules"]] == ["owner-rule"]
        assert [f["subject"] for f in system["existing_facts"]] == ["system-fact"]
        assert [r["content"] for r in system["existing_rules"]] == ["system-rule"]
        assert len(non_owner["existing_facts"]) == 100
        assert all(f["subject"].startswith("third-party") for f in non_owner["existing_facts"])
        assert len(non_owner["existing_rules"]) == 50
        assert all(r["content"].startswith("third-party") for r in non_owner["existing_rules"])

    async def test_failed_partition_does_not_block_the_other_partition(
        self, memory_pool, monkeypatch
    ) -> None:
        owner_id, other_id = await _seed_episodes(
            memory_pool, ("t1", "general", "owner"), ("t1", "general", "third_party")
        )

        async def _fake_execute_consolidation(**kwargs: Any) -> dict[str, Any]:
            if kwargs["authority_partition"] == "owner":
                raise ValueError("out-of-partition evidence")
            return _exec_result(1, facts_created=1)

        _stub_runtime_pipeline(monkeypatch, _fake_execute_consolidation)
        monkeypatch.setattr(
            consolidation_module, "build_consolidation_prompt", lambda **kwargs: "prompt"
        )

        stats = await consolidation_module.run_consolidation(
            memory_pool, embedding_engine=None, cc_spawner=_FakeSpawner()
        )

        assert stats["groups_consolidated"] == 1
        assert stats["facts_created"] == 1
        assert stats["errors"] == ["Failed to consolidate general"]
        assert await _episode_state(memory_pool, owner_id) == (
            "failed",
            "Consolidation execution failed.",
        )
        # The stub leaves the sibling's lease for its own executor; it is not failed.
        assert (await _episode_state(memory_pool, other_id))[0] == "pending"
        assert [r[1] for r in await _audit_rows(memory_pool)] == [1]

    async def test_no_audit_row_when_no_spawner(self, memory_pool):
        # Without a spawner, no group is consolidated → no audit row written.
        await _seed_episodes(memory_pool, ("t1", "general", None))

        stats = await consolidation_module.run_consolidation(
            memory_pool,
            embedding_engine=None,
            cc_spawner=None,
        )
        assert await _audit_rows(memory_pool) == []
        assert stats["episodes_processed"] == 1
        assert stats["groups_consolidated"] == 0

    @pytest.mark.parametrize("output", [None, " \n\t"])
    async def test_missing_runtime_output_records_actionable_failure(
        self, memory_pool, output: str | None
    ) -> None:
        (episode_id,) = await _seed_episodes(memory_pool, ("t1", "switchboard", None))

        stats = await consolidation_module.run_consolidation(
            memory_pool,
            embedding_engine=None,
            cc_spawner=_FakeSpawner(output=output),
        )

        detail = "Consolidation runtime returned no output."
        assert stats["groups_consolidated"] == 0
        assert stats["errors"] == [f"runtime session failed for switchboard: {detail}"]
        assert await _episode_state(memory_pool, episode_id) == ("failed", detail)

    async def test_unsuccessful_runtime_without_error_records_fallback_detail(
        self, memory_pool
    ) -> None:
        (episode_id,) = await _seed_episodes(memory_pool, ("t1", "switchboard", None))

        stats = await consolidation_module.run_consolidation(
            memory_pool,
            embedding_engine=None,
            cc_spawner=_FakeSpawner(success=False),
        )

        detail = "Consolidation runtime reported a failure."
        assert stats["groups_consolidated"] == 0
        assert stats["errors"] == [f"runtime session failed for switchboard: {detail}"]
        assert await _episode_state(memory_pool, episode_id) == ("failed", detail)

    async def test_invalid_evidence_uses_existing_group_failure_path(self, memory_pool) -> None:
        (episode_id,) = await _seed_episodes(memory_pool, ("t1", "relationship", None))

        stats = await consolidation_module.run_consolidation(
            memory_pool,
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
        assert await _audit_rows(memory_pool) == []
        assert await _episode_state(memory_pool, episode_id) == (
            "failed",
            "Consolidation execution failed.",
        )
