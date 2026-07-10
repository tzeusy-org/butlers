"""Tests for butlers.migrations.get_chain_head / get_chain_revision_ids.

These are pure filesystem scans (no database, no docker) — used by the
migration-drift sentinel (bu-9r3hd.1) to resolve "what does the codebase say
the head of this migration chain should be." Exercised against the real
"core" chain (deterministic, no fixture needed) plus a synthetic temp chain
for the error paths (unknown chain, multiple unmerged heads).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from butlers.migrations import get_all_chains, get_chain_head, get_chain_revision_ids

pytestmark = pytest.mark.unit


def test_get_chain_head_returns_the_real_core_head():
    head = get_chain_head("core")
    assert head.startswith("core_")


def test_get_chain_revision_ids_includes_the_head_and_is_frozen():
    head = get_chain_head("core")
    revisions = get_chain_revision_ids("core")
    assert head in revisions
    assert isinstance(revisions, frozenset)
    # A codebase this mature has well more than a handful of core revisions.
    assert len(revisions) > 50


def test_get_chain_head_raises_for_unknown_chain():
    with pytest.raises(ValueError, match="Unknown migration chain"):
        get_chain_head("definitely_not_a_real_chain")


def test_every_recognized_chain_resolves_exactly_one_head():
    """Every chain get_all_chains() discovers must have exactly one head.

    Two unmerged heads left in a chain is itself a codebase defect the drift
    sentinel should surface loudly (get_chain_head raises) rather than
    silently pick one — this test catches that regression across the whole
    repo, not just "core".
    """
    for chain in get_all_chains():
        head = get_chain_head(chain)
        assert head in get_chain_revision_ids(chain)


def _write_revision(directory: Path, *, revision: str, down_revision: str | None) -> None:
    directory.joinpath(f"{revision}.py").write_text(
        textwrap.dedent(
            f"""
            revision = {revision!r}
            down_revision = {down_revision!r}
            branch_labels = None
            depends_on = None

            def upgrade():
                pass

            def downgrade():
                pass
            """
        )
    )


def test_get_chain_head_raises_on_multiple_unmerged_heads(tmp_path, monkeypatch):
    chain_dir = tmp_path / "fake_chain"
    chain_dir.mkdir()
    _write_revision(chain_dir, revision="fake_001", down_revision=None)
    _write_revision(chain_dir, revision="fake_002a", down_revision="fake_001")
    _write_revision(
        chain_dir, revision="fake_002b", down_revision="fake_001"
    )  # second, unmerged head

    monkeypatch.setattr(
        "butlers.migrations._resolve_chain_dir",
        lambda chain: chain_dir if chain == "fake_chain" else None,
    )

    with pytest.raises(RuntimeError, match="has 2 head"):
        get_chain_head("fake_chain")
