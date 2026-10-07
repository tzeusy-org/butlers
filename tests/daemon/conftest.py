"""Restore fact registry; custody infrastructure fixture is root-registered."""

from __future__ import annotations

import pytest

from butlers.core import fact_authority


@pytest.fixture(autouse=True)
def _restore_fact_source_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep direct and indirect wired registries within their owning test.

    Registration still uses production code. Capture the actual prior object;
    teardown restores it without clearing its live reports or replacing it with
    None. This also covers ButlerDaemon.start callers outside the wiring file.
    """
    monkeypatch.setattr(fact_authority, "_source_registry", fact_authority.source_registry())
