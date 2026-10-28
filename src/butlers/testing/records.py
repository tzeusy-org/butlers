"""Small asyncpg-shaped records for DB-free tests, without attribute access."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


class Record:
    """Snapshot columns with key/index lookup and asyncpg's value iteration.

    This models the Python record protocol only, not PostgreSQL types or SQL.
    ``dict(record)`` uses ``keys`` and key lookup despite value iteration.
    """

    __slots__ = ("_columns",)

    def __init__(self, columns: Mapping[str, Any]) -> None:
        self._columns = dict(columns)

    def __getitem__(self, key: str | int | slice) -> Any:
        if isinstance(key, (int, slice)):
            return tuple(self._columns.values())[key]
        return self._columns[key]

    def __iter__(self):
        return iter(self._columns.values())

    def __len__(self) -> int:
        return len(self._columns)

    def __contains__(self, key: str) -> bool:
        return key in self._columns

    def get(self, key: str, default: Any = None) -> Any:
        return self._columns.get(key, default)

    def keys(self):
        return self._columns.keys()

    def values(self):
        return self._columns.values()

    def items(self):
        return self._columns.items()


def row(**columns: Any) -> Record:
    """Return a fresh record from explicitly named columns."""
    return Record(columns)


def records(rows: Iterable[Mapping[str, Any]]) -> list[Record]:
    """Snapshot each mapping as a distinct record, preserving input order."""
    return [Record(columns) for columns in rows]
