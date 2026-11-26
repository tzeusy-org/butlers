"""core_384: a typed anchor beside the timezone column (bu-5ii4gj.5 P6).

Real migrated databases only. Synthetic names only.
"""

from __future__ import annotations

import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_PREVIOUS = "core_380"
_REVISION = "core_384"


def _run(db_url: str, schema: str, statement: str, params: dict | None = None) -> list[tuple]:
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            result = connection.execute(text(statement), params or {})
            return [tuple(r) for r in result.fetchall()] if result.returns_rows else []
    finally:
        engine.dispose()


def _task(
    db_url, schema, name, *, cron="0 9 * * *", timezone="UTC", task_type="cron", one_shot=False
) -> None:
    _run(
        db_url,
        schema,
        "INSERT INTO scheduled_tasks (name, cron, dispatch_mode, prompt, source, enabled, "
        "timezone, task_type, next_run_at, until_at) VALUES (:n, :c, 'prompt', 'p', 'db', true, "
        ":tz, :tt, now() + interval '1 day', "
        "CASE WHEN :one THEN now() + interval '1 day' + interval '30 seconds' END)",
        {"n": name, "c": cron, "tz": timezone, "tt": task_type, "one": one_shot},
    )


def _anchors(db_url: str, schema: str) -> dict[str, tuple]:
    return {
        r[0]: r[1:]
        for r in _run(db_url, schema, "SELECT name, anchor_kind, anchor_zone FROM scheduled_tasks")
    }


def _seed(db_url: str, schema: str) -> None:
    _task(db_url, schema, "sentinel")
    _task(db_url, schema, "pinned", timezone="Europe/London")
    _task(db_url, schema, "pinned_utc", timezone="Etc/UTC")
    _task(db_url, schema, "oneshot", one_shot=True)  # the remind shape
    _task(db_url, schema, "deadline", cron="0 0 * * *", task_type="deadline")


_DERIVED = {
    "sentinel": ("owner_local", None),
    "pinned": ("zone_pinned", "Europe/London"),
    "pinned_utc": ("zone_pinned", "Etc/UTC"),
    "oneshot": ("absolute", None),
    "deadline": ("absolute", None),
}


def test_existing_rows_are_not_backfilled_in_any_schema_and_stay_derived(postgres_container):
    from butlers.core.scheduler.anchor import effective_anchor

    db_url = create_migration_db(postgres_container, migration_db_name())
    for schema in ("general", "travel"):
        command.upgrade(
            _build_alembic_config(db_url, chains=["core"], target_schema=schema), _PREVIOUS
        )
        _seed(db_url, schema)
    for schema in ("general", "travel"):
        command.upgrade(
            _build_alembic_config(db_url, chains=["core"], target_schema=schema), _REVISION
        )
    for schema in ("general", "travel"):
        assert set(_anchors(db_url, schema).values()) == {(None, None)}, schema
        rows = _run(
            db_url,
            schema,
            "SELECT name, cron, timezone, task_type, next_run_at, until_at FROM scheduled_tasks",
        )
        for name, cron, timezone, task_type, next_run_at, until_at in rows:
            derived = effective_anchor(
                {
                    "cron": cron,
                    "timezone": timezone,
                    "task_type": task_type,
                    "next_run_at": next_run_at,
                    "until_at": until_at,
                }
            )
            assert (derived.kind, derived.zone) == _DERIVED[name], (schema, name)


def test_a_rerun_and_a_downgrade_upgrade_cycle_keep_declared_values(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    command.upgrade(config, _REVISION)
    _task(db_url, "general", "declared", timezone="Europe/London")
    _run(
        db_url,
        "general",
        "UPDATE scheduled_tasks SET anchor_kind = 'zone_pinned', anchor_zone = 'Europe/London'",
    )
    command.upgrade(config, _REVISION)  # idempotent re-run
    command.downgrade(config, _PREVIOUS)
    assert _anchors(db_url, "general")["declared"] == ("zone_pinned", "Europe/London")
    # The CHECKs are gone, so an older writer can edit a declared row's timezone.
    _run(db_url, "general", "UPDATE scheduled_tasks SET timezone = 'Asia/Tokyo'")
    command.upgrade(config, _REVISION)  # the stale declaration is cleared, not refused
    assert _anchors(db_url, "general")["declared"] == (None, None)


def test_the_checks_keep_the_kind_and_the_timezone_from_disagreeing(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    command.upgrade(config, _REVISION)
    _task(db_url, "general", "row", timezone="Europe/London")

    with pytest.raises(Exception, match="scheduled_tasks_anchor_kind_check"):
        _run(db_url, "general", "UPDATE scheduled_tasks SET anchor_kind = 'bogus'")
    with pytest.raises(Exception, match="scheduled_tasks_anchor_pinned_check"):
        _run(
            db_url,
            "general",
            "UPDATE scheduled_tasks SET anchor_kind = 'zone_pinned', anchor_zone = 'Asia/Tokyo'",
        )
    with pytest.raises(Exception, match="scheduled_tasks_anchor_local_check"):
        _run(db_url, "general", "UPDATE scheduled_tasks SET anchor_kind = 'owner_local'")
    # A new row that declares nothing stays NULL: readers derive it from the timezone.
    _task(db_url, "general", "undeclared", timezone="Asia/Tokyo")
    assert _anchors(db_url, "general")["undeclared"] == (None, None)


def test_a_schema_without_the_table_is_skipped(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    command.upgrade(config, _PREVIOUS)
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text('DROP TABLE "general".scheduled_tasks CASCADE'))
    finally:
        engine.dispose()

    command.upgrade(config, _REVISION)  # no table in this schema: nothing to alter, no error
    command.downgrade(config, _PREVIOUS)

    assert _run(db_url, "general", "SELECT to_regclass('scheduled_tasks')") == [(None,)]
