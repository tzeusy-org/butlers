"""``run_migrations`` skips Alembic when the chain is already at head."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

import butlers.migrations as migrations
from alembic import command
from butlers.migrations import get_chain_head, run_migrations

pytestmark = pytest.mark.integration


@pytest.fixture
def upgrades(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record each Alembic upgrade ``run_migrations`` starts, then run it."""
    calls: list[str] = []
    real_upgrade = migrations.command.upgrade

    def _upgrade(config, revision, *args, **kwargs):
        calls.append(revision)
        return real_upgrade(config, revision, *args, **kwargs)

    monkeypatch.setattr(migrations.command, "upgrade", _upgrade)
    return calls


def _applied(db_url: str, schema: str | None) -> set[str]:
    table = f'"{schema}".alembic_version' if schema else "alembic_version"
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return {row[0] for row in conn.execute(text(f"SELECT version_num FROM {table}"))}
    finally:
        engine.dispose()


@pytest.mark.parametrize("domain", ["finance", "health"])
def test_domain_projection_defers_only_initial_historical_core(fresh_db, domain) -> None:
    """Old-core domains stay usable; explicit prerequisite arrival completes their rows."""
    previous = "finance_019" if domain == "finance" else "health_006"
    schemas = {chain: domain for chain in ("core", domain, "memory")}
    url = fresh_db(
        "core",
        domain,
        "memory",
        schemas=schemas,
        revisions={"core": "core_319", domain: previous},
    )
    engine = create_engine(url)
    installer = f"{domain}.mcp_install_{domain}_projection"
    signatures = (
        "mcp_json_tokens(text)",
        "mcp_json_credential(text[])",
        "mcp_json_path_key(text)",
        "mcp_json_leaves(jsonb,text,text[])",
        "mcp_write_json_fields(text,text,text,text[],boolean,jsonb)",
        "mcp_sync_json_fields()",
        "mcp_install_json_projection(text,text,text,text[],boolean)",
    )
    try:
        with engine.begin() as conn:
            fact = conn.execute(
                text(
                    f"INSERT INTO {domain}.facts(subject,predicate,content,metadata) "
                    "VALUES('historical source','medication','accepted',"
                    '\'{"name":"Deferred","notes":"backfill me"}\') RETURNING id'
                )
            ).scalar_one()
            assert conn.execute(
                text("SELECT to_regprocedure('public.mcp_sync_json_fields()') IS NULL")
            ).scalar_one()
        asyncio.run(run_migrations(url, chain=domain, schema=domain))
        assert _applied(url, domain) == {
            "core_319",
            get_chain_head(domain),
            get_chain_head("memory"),
        }, "The domain must not secretly upgrade its historical core"
        with engine.connect() as conn:
            assert (
                conn.execute(text(f"SELECT count(*) FROM {domain}.json_fields_public")).scalar_one()
                == 0
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_trigger WHERE tgrelid=to_regclass(:source) "
                        "AND tgname IN ('trg_mcp_json_fields','trg_mcp_health_fact')"
                    ),
                    {"source": f"{domain}.facts"},
                ).scalar_one()
                == 0
            )
        # Initial compatibility is explicitly deferred, never complete memory success.
        with pytest.raises(DBAPIError, match="projection shared core prerequisite missing"):
            asyncio.run(run_migrations(url, chain="memory", schema=domain))
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"UPDATE {domain}.alembic_version SET version_num='core_unknown' "
                    "WHERE version_num='core_319'"
                )
            )
        try:
            with pytest.raises(DBAPIError, match="projection shared core prerequisite missing"):
                with engine.begin() as conn:
                    conn.execute(text(f"SELECT {installer}(false)"))
        finally:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        f"UPDATE {domain}.alembic_version SET version_num='core_319' "
                        "WHERE version_num='core_unknown'"
                    )
                )
        # This is the caller's explicit core upgrade, outside the domain/finalizer.
        asyncio.run(run_migrations(url, chain="core", schema=domain))
        asyncio.run(run_migrations(url, chain="memory", schema=domain))
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        f"SELECT value_text FROM {domain}.json_fields_public "
                        "WHERE row_id=:id AND path='metadata.notes'"
                    ),
                    {"id": str(fact)},
                ).scalar_one()
                == "backfill me"
            )
            if domain == "health":
                assert (
                    conn.execute(
                        text("SELECT name FROM health.medications_public WHERE id=:id"),
                        {"id": fact},
                    ).scalar_one()
                    == "Deferred"
                )
        for missing in ((signatures[-1],), signatures):
            renamed = []
            try:
                pending = []
                with engine.begin() as conn:
                    for signature in missing:
                        name, arguments = signature.split("(", 1)
                        conn.execute(
                            text(f"ALTER FUNCTION public.{signature} RENAME TO unavailable_{name}")
                        )
                        pending.append((name, arguments))
                renamed = pending
                message = "catalog incomplete" if len(missing) == 1 else "core prerequisite missing"
                for require_facts in ("false", "true"):
                    with pytest.raises(DBAPIError, match=message):
                        with engine.begin() as conn:
                            conn.execute(text(f"SELECT {installer}({require_facts})"))
            finally:
                with engine.begin() as conn:
                    for name, arguments in reversed(renamed):
                        conn.execute(
                            text(
                                f"ALTER FUNCTION public.unavailable_{name}({arguments} RENAME TO {name}"
                            )
                        )
            asyncio.run(run_migrations(url, chain="memory", schema=domain))
    finally:
        engine.dispose()


def _one_behind_head(chain: str) -> str:
    head = migrations._chain_script_directory(chain).get_revision(get_chain_head(chain))
    assert isinstance(head.down_revision, str)
    return head.down_revision


@pytest.mark.parametrize("schema", ["general", None])
def test_at_head_no_upgrade_runs(fresh_db, upgrades, schema, monkeypatch) -> None:
    url = fresh_db("core", schemas={"core": schema} if schema else None)
    admitted: list[str] = []
    real_admission = migrations.check_bootstrap_database

    def _admission(db_url: str) -> None:
        admitted.append(db_url)
        real_admission(db_url)

    monkeypatch.setattr(migrations, "check_bootstrap_database", _admission)
    upgrades.clear()  # template builds in this process also upgrade
    asyncio.run(run_migrations(url, chain="core", schema=schema))
    assert upgrades == []
    # Bootstrap admission (REQ-database-security-011) still runs at head.
    assert admitted == [url]
    assert get_chain_head("core") in _applied(url, schema)


@pytest.mark.parametrize("schema", ["general", None])
def test_one_revision_behind_still_upgrades(fresh_db, upgrades, schema) -> None:
    behind = _one_behind_head("core")
    url = fresh_db("core", schemas={"core": schema} if schema else None, revisions={"core": behind})
    assert behind in _applied(url, schema)
    upgrades.clear()  # template builds in this process also upgrade
    asyncio.run(run_migrations(url, chain="core", schema=schema))
    assert upgrades == ["core@head"]
    assert get_chain_head("core") in _applied(url, schema)


def test_unmigrated_database_still_upgrades(fresh_db, upgrades) -> None:
    # The core template exists; the switchboard chain has never run on this clone.
    url = fresh_db("core", schemas={"core": "switchboard"})
    upgrades.clear()  # template builds in this process also upgrade
    asyncio.run(run_migrations(url, chain="switchboard", schema="switchboard"))
    assert upgrades == ["switchboard@head"]
    assert get_chain_head("switchboard") in _applied(url, "switchboard")


def test_at_head_check_sees_a_new_revision_file(fresh_db, upgrades, monkeypatch) -> None:
    """A cached head must not outlive the chain's files changing on disk."""
    url = fresh_db("core", schemas={"core": "general"})
    upgrades.clear()  # template builds in this process also upgrade
    asyncio.run(run_migrations(url, chain="core", schema="general"))
    assert upgrades == []
    monkeypatch.setattr(migrations, "_chain_files_signature", lambda chain_dir: ("changed",))
    real_heads = migrations._chain_heads_uncached

    def _moved_head(chain: str):
        _heads, revisions = real_heads(chain)
        return ("core_new_head",), revisions | {"core_new_head"}

    monkeypatch.setattr(migrations, "_chain_heads_uncached", _moved_head)
    # The database no longer matches the (new) head, so the real upgrade runs; on the
    # real files it is a no-op.
    asyncio.run(run_migrations(url, chain="core", schema="general"))
    assert upgrades == ["core@head"]


@pytest.mark.parametrize("domain", ["finance", "health"])
@pytest.mark.parametrize("order", ["cold", "populated"])
def test_domain_projection_finishes_deferred_and_already_head_memory(
    fresh_db,
    upgrades,
    domain,
    order,
) -> None:
    """Real facts are present and accepted before an at-head finalizer must project them."""
    schemas = {chain: domain for chain in ("core", domain, "memory")}
    previous = "finance_019" if domain == "finance" else "health_006"
    if order == "cold":
        url = fresh_db("core", domain, schemas=schemas)
        # Real memory migration, deliberately before the runner's post-memory
        # callback, positions its already-at-head branch rather than faking it.
        command.upgrade(migrations._build_alembic_config(url, target_schema=domain), "memory@head")
    else:
        url = fresh_db("core", domain, "memory", schemas=schemas, revisions={domain: previous})
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            fact = conn.execute(
                text(f"""
                INSERT INTO {domain}.facts(subject,predicate,content,metadata,sensitivity)
                VALUES('other person','medication','accepted source',
                    '{{"name":"Initial","notes":"must survive"}}','confidential') RETURNING id
            """)
            ).scalar_one()
            if order == "cold":
                assert (
                    conn.execute(
                        text(f"SELECT count(*) FROM {domain}.json_fields_public WHERE row_id=:id"),
                        {"id": str(fact)},
                    ).scalar_one()
                    == 0
                )
        if order == "populated":
            asyncio.run(run_migrations(url, chain=domain, schema=domain))
        upgrades.clear()
        asyncio.run(run_migrations(url, chain="memory", schema=domain))
        assert upgrades == []
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        f"SELECT value_text FROM {domain}.json_fields_public "
                        "WHERE row_id=:id AND path='metadata.notes'"
                    ),
                    {"id": str(fact)},
                ).scalar_one_or_none()
                == "must survive"
            )
            if domain == "health":
                assert (
                    conn.execute(
                        text("SELECT name FROM health.medications_public WHERE id=:id"),
                        {"id": fact},
                    ).scalar_one()
                    == "Initial"
                )
        # A correct boundary does not reacquire a source-table lock or rebackfill
        # on every boot. A genuinely held exclusive lock positions that claim.
        with engine.begin() as blocker:
            blocker.execute(text(f"LOCK TABLE {domain}.facts IN ACCESS EXCLUSIVE MODE"))
            asyncio.run(run_migrations(url, chain="memory", schema=domain))
        assert upgrades == []
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {domain}.facts DISABLE TRIGGER trg_mcp_json_fields"))
        with pytest.raises(DBAPIError):
            asyncio.run(run_migrations(url, chain="memory", schema=domain))
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {domain}.facts ENABLE TRIGGER trg_mcp_json_fields"))
        asyncio.run(run_migrations(url, chain="memory", schema=domain))
        for missing in ("facts", "calendar_sources") if domain == "health" else ("facts",):
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {domain}.{missing} RENAME TO unavailable_source"))
            try:
                with pytest.raises(DBAPIError):
                    asyncio.run(run_migrations(url, chain="memory", schema=domain))
            finally:
                with engine.begin() as conn:
                    conn.execute(
                        text(f"ALTER TABLE {domain}.unavailable_source RENAME TO {missing}")
                    )
            asyncio.run(run_migrations(url, chain="memory", schema=domain))
    finally:
        engine.dispose()
