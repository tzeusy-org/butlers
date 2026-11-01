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
            # Per-database role settings (core_001's butler search_path) are not
            # copied by CREATE DATABASE either.
            settings = [
                migration._database_role_settings(conn, urlparse(url).path.lstrip("/"))
                for url in (first, second, fresh)
            ]
            assert settings[0] and settings[0] == settings[1] == settings[2]
            template = conn.execute(
                text(
                    "SELECT datallowconn FROM pg_database "
                    "WHERE datistemplate AND datname LIKE 'tmpl\\_%'"
                )
            ).all()
            assert template and not any(allowed for (allowed,) in template)
    finally:
        admin.dispose()
