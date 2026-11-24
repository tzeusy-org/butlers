"""Ownership transfer refusals and statements (bu-dss7he.6 S3), no database needed."""

from __future__ import annotations

from dataclasses import replace

import pytest

from butlers.db_authority.ownership import (
    LoginFacts,
    Membership,
    membership_statement,
    transfer_refusals,
)

pytestmark = pytest.mark.unit

_SOURCE = LoginFacts("butlers", exists=True, can_login=True)
_TARGET = LoginFacts("butlers_migrator", exists=True, can_login=True)


def _refusals(**overrides: object) -> list[str]:
    args: dict[str, object] = {
        "executor_is_superuser": True,
        "source": _SOURCE,
        "target": _TARGET,
        "foreign_shared_objects": (),
    }
    args.update(overrides)
    return transfer_refusals(**args)  # type: ignore[arg-type]


def test_a_clean_pair_is_accepted() -> None:
    assert _refusals() == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"executor_is_superuser": False}, "superuser"),
        ({"source": replace(_SOURCE, exists=False)}, "does not exist"),
        ({"target": replace(_TARGET, exists=False)}, "does not exist"),
        ({"target": replace(_TARGET, name="butlers")}, "same login"),
        ({"target": replace(_TARGET, can_login=False)}, "LOGIN"),
        ({"target": replace(_TARGET, superuser=True)}, "SUPERUSER"),
        ({"target": replace(_TARGET, createrole=True)}, "CREATEROLE"),
        ({"target": replace(_TARGET, createdb=True)}, "CREATEDB"),
        ({"target": replace(_TARGET, replication=True)}, "REPLICATION"),
        ({"target": replace(_TARGET, bypassrls=True)}, "BYPASSRLS"),
        ({"source": replace(_SOURCE, superuser=True)}, "superuser source"),
        ({"target": replace(_TARGET, name="butler_general_rw")}, "runtime or purpose"),
        ({"target": replace(_TARGET, name="connector_writer")}, "runtime or purpose"),
        ({"target": replace(_TARGET, name="dnd_generation_owner")}, "runtime or purpose"),
        ({"foreign_shared_objects": ("database other_db",)}, "outside this database"),
    ],
)
def test_each_unsafe_pairing_is_refused(overrides: dict[str, object], fragment: str) -> None:
    problems = _refusals(**overrides)
    assert problems and any(fragment in p for p in problems), problems


def test_membership_statement_copies_every_option_and_quotes_names() -> None:
    statement = membership_statement(
        Membership("butler_general_rw", inherit=True, set=True, admin=False),
        'odd"login',
        quote=lambda name: '"' + name.replace('"', '""') + '"',
    )
    assert statement == (
        'GRANT "butler_general_rw" TO "odd""login" WITH INHERIT TRUE, SET TRUE, ADMIN FALSE'
    )
