"""Unit tests for DB env parsing in API dependencies."""

from __future__ import annotations

import pytest

from butlers.api.deps import _db_params_from_env

pytestmark = pytest.mark.unit


def test_db_params_from_env_parses_database_url_sslmode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DATABASE_URL sslmode query parameter is preserved."""
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.internal:5432/postgres?sslmode=disable")

    params = _db_params_from_env()

    assert params["host"] == "db.internal"
    assert params["port"] == 5432
    assert params["user"] == "u"
    assert params["password"] == "p"
    assert params["ssl"] == "disable"


def test_db_params_from_env_uses_postgres_sslmode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback env vars include POSTGRES_SSLMODE."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "dbhost")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_USER", "user1")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pass1")
    monkeypatch.setenv("POSTGRES_SSLMODE", "verify-full")

    params = _db_params_from_env()

    assert params["host"] == "dbhost"
    assert params["port"] == 6543
    assert params["user"] == "user1"
    assert params["password"] == "pass1"
    assert params["ssl"] == "verify-full"
