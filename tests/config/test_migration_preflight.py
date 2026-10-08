"""Protected graph contract with actual Alembic API; no SQL execution."""

from types import SimpleNamespace

import pytest
from alembic.runtime.migration import MigrationContext
from alembic.script.revision import RangeNotAncestorError, ResolutionError

from butlers import migration_preflight as preflight
from butlers.migrations import get_chain_head


def test_multichain_preflight_selects_core_and_preserves_protected_refusal(monkeypatch):
    core = get_chain_head("core")
    foreign = (get_chain_head("memory"), get_chain_head("relationship"))
    heads = (core, *foreign)

    def context(current, target):
        migration = MigrationContext.configure(dialect_name="postgresql")
        migration.get_current_heads = lambda: current
        return SimpleNamespace(get_context=lambda: migration, get_revision_argument=lambda: target)

    original = tuple(heads)
    for current in ((core,), heads, ("core_215", *foreign)):
        assert not preflight._downgrade_crosses_runtime_attention(context(current, "core_215"))
        assert not preflight._downgrade_crosses_runtime_attention(context(current, "core_198"))
        assert preflight._downgrade_crosses_runtime_attention(context(current, "core_197"))
        assert preflight._downgrade_crosses_runtime_attention(context(current, None))
        assert not preflight._downgrade_crosses_runtime_attention(context(current, "-1"))
    assert heads == original  # Selection never stamps/deletes foreign heads.
    assert not preflight._downgrade_crosses_runtime_attention(context(foreign, None))
    assert not preflight._downgrade_crosses_runtime_attention(context((), None))
    assert not preflight._downgrade_crosses_runtime_attention(context(("core_197", *foreign), None))
    assert preflight._downgrade_crosses_runtime_attention(context(("core_199", *foreign), "-2"))
    assert not preflight._downgrade_crosses_runtime_attention(context(("core_199", *foreign), "-1"))
    assert not preflight._downgrade_crosses_runtime_attention(context(heads, foreign))
    for invalid in ((core, "unknown_branch"), (core, core), (core, "core_215")):
        with pytest.raises(RuntimeError, match="unknown|duplicate|ambiguous"):
            preflight._downgrade_crosses_runtime_attention(context(invalid, "core_215"))
    with pytest.raises(RuntimeError, match="exceeds"):
        preflight._downgrade_crosses_runtime_attention(context(heads, "-99999"))
    with pytest.raises(ResolutionError):
        preflight._downgrade_crosses_runtime_attention(context(heads, "unknown_target"))
    with pytest.raises(RangeNotAncestorError):
        preflight._downgrade_crosses_runtime_attention(context(("core_215", *foreign), core))

    bind = object()
    calls = []
    protected = SimpleNamespace(
        protected_rollback_preflight_passes=lambda actual: calls.append(actual) or False
    )
    monkeypatch.setattr(preflight, "_runtime_attention_migration", lambda: protected)
    op = SimpleNamespace(get_bind=lambda: bind)
    preflight.preflight_runtime_attention_downgrade(op, context(heads, "core_215"))
    assert calls == []
    with pytest.raises(RuntimeError, match="protected core_198 rollback preflight failed"):
        preflight.preflight_runtime_attention_downgrade(op, context(heads, "core_197"))
    assert calls == [bind]
    protected.protected_rollback_preflight_passes = lambda actual: actual is bind
    preflight.preflight_runtime_attention_downgrade(op, context(heads, "core_197"))
