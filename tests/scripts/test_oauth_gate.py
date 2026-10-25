"""Actual OAuth gate consumer at a DBAPI boundary; no PostgreSQL or tokens."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def gate(monkeypatch):
    source = Path(__file__).resolve().parents[2] / "scripts" / "oauth_gate.py"
    spec = importlib.util.spec_from_file_location("isolated_oauth_gate", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("POSTGRES_USER", "connector_login")
    monkeypatch.setenv("POSTGRES_PASSWORD", "synthetic-not-a-secret")
    monkeypatch.setenv("POSTGRES_DB", "butlers")
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    connection = Mock()
    connection.cursor.return_value = cursor
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    cursor.fetchone.side_effect = [(True,) * 6, (True,), (1,)]
    connect = Mock(return_value=connection)
    monkeypatch.setitem(sys.modules, "psycopg2", SimpleNamespace(connect=connect))
    return module, cursor, connection, connect


def test_actual_gate_proves_connector_purpose_before_existing_token_query(gate):
    module, cursor, connection, connect = gate
    assert module._check_token() is True
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert statements[0] == 'SET ROLE "connector_writer"'
    assert "session_user" in statements[1] and "pg_auth_members" in statements[1]
    assert "expected_relations" in statements[2]
    assert "public.entity_info" in statements[3] and "COUNT(*)" in statements[3]
    assert connect.call_args.kwargs["connect_timeout"] == 5
    connection.close.assert_called_once()


@pytest.mark.parametrize("failure", ["unproven", "driver", "missing", "wrong_database"])
def test_gate_refuses_before_token_read_and_closes_only_owned_connection(
    gate, monkeypatch, capsys, failure
):
    module, cursor, connection, connect = gate
    if failure == "unproven":
        cursor.fetchone.side_effect = [(True, False, True, True, True, True)]
    elif failure == "driver":
        cursor.execute.side_effect = RuntimeError("synthetic-private-driver-payload")
    elif failure == "missing":
        monkeypatch.delenv("POSTGRES_PASSWORD")
    else:
        monkeypatch.setenv("POSTGRES_DB", "undeclared_deployment")
    assert module._check_token() is False
    assert not any("public.entity_info" in c.args[0] for c in cursor.execute.call_args_list)
    if failure in {"missing", "wrong_database"}:
        connect.assert_not_called()
        connection.close.assert_not_called()
    else:
        connection.close.assert_called_once()
    assert "synthetic-private-driver-payload" not in capsys.readouterr().err


def test_gate_cleanup_failure_is_unavailable_and_content_blind(gate, capsys):
    module, _, connection, _ = gate
    connection.close.side_effect = RuntimeError("synthetic-private-driver-payload")
    assert module._check_token() is False
    connection.close.assert_called_once()
    assert "synthetic-private-driver-payload" not in capsys.readouterr().err


@pytest.mark.parametrize("catalog_ok", [False, None, 1, True])
def test_gate_checks_actual_object_authority_before_reading_tokens(gate, catalog_ok):
    import json

    module, cursor, connection, _ = gate
    cursor.fetchone.side_effect = [(True,) * 6, (catalog_ok,), (1,)]
    assert module._check_token() is (catalog_ok is True)
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert "expected_relations" in statements[2]
    assert "left(n.nspname,3)<>'pg_'" in statements[2]  # Literal prefix, not LIKE wildcard
    metadata = json.loads(cursor.execute.call_args_list[2].args[1]["catalog"])
    assert any(row["name"] == "entity_info" for row in metadata["relations"])
    assert any(row["name"] == "context_dnd_mutate" for row in metadata["functions"])
    assert sum("public.entity_info ei" in statement for statement in statements) == (
        1 if catalog_ok is True else 0
    )
    connection.close.assert_called_once()
