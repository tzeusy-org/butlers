"""Shared test fixtures for the butlers test suite.

The canonical definitions live in the root ``conftest.py`` so they are visible
to both ``tests/`` and ``roster/*/tests/``.  This file re-exports them so that
existing imports like ``from tests.conftest import SpawnerResult`` keep working.
"""

from __future__ import annotations

from butlers.testing.shared_fixtures import MockSpawner, SpawnerResult, mock_spawner  # noqa: F401

__all__ = ["MockSpawner", "SpawnerResult", "mock_spawner"]
