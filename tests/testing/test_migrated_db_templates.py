"""Migrated test databases are clones of one per-process template (bu-ly3lv5.8)."""

from __future__ import annotations

from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text

from butlers.testing import migration


class _Server:
    def __init__(self, url: str = "postgresql://admin:pw@localhost:5432/test") -> None:
        self.url = url

    def get_connection_url(self) -> str:
        return self.url


def _key(server: _Server | None = None, **request: object) -> str:
    request = {"chains": ["core"], "schemas": {}, "revisions": {}, **request}
    return migration.template_key(server or _Server(), **request)  # type: ignore[arg-type]


@pytest.mark.unit
def test_template_key_names_one_migrated_state_on_one_server() -> None:
    assert _key(schemas={"a": "x", "b": "y"}) == _key(schemas={"b": "y", "a": "x"})
    assert _key(chains=["core", "memory"]) != _key(chains=["memory", "core"])
    assert _key(revisions={"core": "core_170"}) != _key()
    assert _key(schemas={"memory": "memory"}) != _key()
    assert _key(_Server("postgresql://admin:pw@localhost:5433/test")) != _key()


@pytest.mark.unit
@pytest.mark.parametrize(("setting", "templated"), [(None, True), ("1", True), ("0", False)])
def test_templates_are_the_default_and_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch, setting: str | None, templated: bool
) -> None:
    calls: list[str] = []
    template = migration._Template(name="tmpl", db_url="postgresql://u:p@h:1/tmpl")
    monkeypatch.setattr(
        migration, "_migrated_template", lambda *a, **k: calls.append("template") or template
    )
    monkeypatch.setattr(
        migration, "_clone_template", lambda c, t, name: calls.append(f"clone:{name}") or "clone"
    )
    monkeypatch.setattr(
        migration, "create_migration_db", lambda c, name: calls.append(f"fresh:{name}") or "fresh"
    )
    monkeypatch.setattr(migration, "_migrate", lambda url, **k: calls.append(f"migrate:{url}"))
    if setting is None:
        monkeypatch.delenv(migration._TEMPLATES_ENV, raising=False)
    else:
        monkeypatch.setenv(migration._TEMPLATES_ENV, setting)

    result = migration.create_migrated_test_db(_Server(), "db1", chains=["core"])

    if templated:
        assert (result, calls) == ("clone", ["template", "clone:db1"])
    else:
        assert (result, calls) == ("fresh", ["fresh:db1", "migrate:fresh"])


def _scalar(url: str, sql: str) -> object:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar()
    finally:
        engine.dispose()


@pytest.mark.integration
def test_clones_are_independent_migrated_databases_with_the_template_privileges(
    postgres_container, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(migration._TEMPLATES_ENV, raising=False)
    migrations: list[str] = []
    real_migrate = migration._migrate
    monkeypatch.setattr(
        migration, "_migrate", lambda url, **k: migrations.append(url) or real_migrate(url, **k)
    )
    first = migration.create_migrated_test_db(
        postgres_container, migration.migration_db_name(), chains=["core"]
    )
    second = migration.create_migrated_test_db(
        postgres_container, migration.migration_db_name(), chains=["core"]
    )
    fresh_name = migration.migration_db_name()
    monkeypatch.setenv(migration._TEMPLATES_ENV, "0")
    fresh = migration.create_migrated_test_db(postgres_container, fresh_name, chains=["core"])

    # At most one template build per key and process, then the scratch control.
    assert len(migrations) <= 2 and migrations[-1] == fresh

    version = "SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version"
    assert _scalar(first, version) == _scalar(second, version) == _scalar(fresh, version)

    engine = create_engine(first, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE clone_isolation_probe (id int)"))
    finally:
        engine.dispose()
    probe = "SELECT to_regclass('public.clone_isolation_probe') IS NOT NULL"
    assert _scalar(first, probe) is True
    assert _scalar(second, probe) is False

    admin = create_engine(postgres_container.get_connection_url(), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            # Database privileges that init-db.sql grants are not copied by
            # CREATE DATABASE; the clone must carry them like a scratch database.
            def shape(url: str) -> set[tuple[str, str, bool]]:
                name = urlparse(url).path.lstrip("/")
                login = urlparse(url).username
                acl = migration._database_acl(conn, name)
                return {("<login>" if g == login else g, p, o) for g, p, o in acl}

            assert shape(first) == shape(fresh)
            settings = [
                migration._database_role_settings(conn, urlparse(url).path.lstrip("/"))
                for url in (first, second, fresh)
            ]
            assert settings[0] == settings[1] == settings[2]
            template = conn.execute(
                text(
                    "SELECT datallowconn FROM pg_database "
                    "WHERE datistemplate AND datname LIKE 'tmpl\\_%'"
                )
            ).all()
            assert template and not any(allowed for (allowed,) in template)
    finally:
        admin.dispose()


@pytest.mark.integration
def test_per_database_role_settings_are_replayed_onto_a_clone(postgres_container) -> None:
    """core_001's ``ALTER ROLE ... IN DATABASE ... SET search_path`` survives cloning."""
    source = migration.migration_db_name()
    clone = migration.migration_db_name()
    login = f"settings_{source}"
    admin = create_engine(postgres_container.get_connection_url(), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE ROLE "{login}" LOGIN'))
            conn.execute(text(f'CREATE DATABASE "{source}"'))
            conn.execute(
                text(
                    f'ALTER ROLE "{login}" IN DATABASE "{source}" SET search_path = memory, public'
                )
            )
            conn.execute(text(f"ALTER DATABASE \"{source}\" SET application_name = 'it''s'"))
            conn.execute(text(f'CREATE DATABASE "{clone}" TEMPLATE "{source}"'))
            assert migration._database_role_settings(conn, clone) == set()

            migration._replay_role_settings(conn, template=source, clone=clone)

            assert migration._database_role_settings(conn, clone) == {
                (login, "search_path=memory, public"),
                ("", "application_name=it's"),
            }
            conn.execute(text(f'DROP DATABASE "{clone}"'))
            conn.execute(text(f'DROP DATABASE "{source}"'))
            conn.execute(text(f'DROP ROLE "{login}"'))
    finally:
        admin.dispose()


@pytest.mark.unit
@pytest.mark.parametrize("stage", ["database_create", "migration", "finalize", "success"])
@pytest.mark.parametrize("disk_full", [False, True])
def test_template_storage_observes_complete_lifecycle(monkeypatch, stage, disk_full):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers.testing import storage

    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://synthetic@localhost/synthetic",
        get_wrapped_container=lambda: SimpleNamespace(id="a" * 64),
    )
    monkeypatch.setattr(storage, "identity", lambda: {"worker": "gw0"})
    observer = storage.StorageObserver(container)
    monkeypatch.setattr(storage, "_ACTIVE", observer)
    monkeypatch.setattr(migration, "_TEMPLATES", {})
    monkeypatch.setattr(migration, "_TEMPLATE_LOCKS", {})
    calls = []
    failure = RuntimeError("fixed synthetic template failure")
    failure.orig = SimpleNamespace(pgcode="53100" if disk_full else "XX000")

    def operation(name):
        calls.append(name)
        if stage == name:
            raise failure

    def create(*args):
        operation("database_create")
        return "postgresql://synthetic@localhost/template"

    class Engine:
        @contextmanager
        def connect(self):
            yield self

        def execute(self, *args):
            operation("finalize")

        def dispose(self):
            calls.append("dispose")

    monkeypatch.setattr(migration, "create_migration_db", create)
    monkeypatch.setattr(migration, "_migrate", lambda *a, **k: operation("migration"))
    monkeypatch.setattr(migration, "create_engine", lambda *a, **k: Engine())
    request = dict(chains=["core"], schemas={}, revisions={})
    if stage == "success":
        first = migration._migrated_template(container, **request)
        assert migration._migrated_template(container, **request) is first
        assert calls == ["database_create", "migration", "finalize", "finalize", "dispose"]
        assert observer.events == dict(
            template_created=1, clone_created=0, create_failed=0, storage_error=0
        )
        assert not observer.wake_event.is_set()
    else:
        with pytest.raises(RuntimeError) as raised:
            migration._migrated_template(container, **request)
        assert raised.value is failure
        assert observer.events == dict(
            template_created=0, clone_created=0, create_failed=1, storage_error=int(disk_full)
        )
        assert observer.wake_event.is_set()
        assert migration._TEMPLATES == {}
        assert calls.count("dispose") == int(stage == "finalize")
    assert all(not lock.locked() for lock in migration._TEMPLATE_LOCKS.values())
    assert observer.thread.ident is None
