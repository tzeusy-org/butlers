"""Contract tests for the butler *runtime identity* contract.

Locks the ownership model documented in ``about/heart-and-soul/vision.md``
Rule 5 and the precedence note in ``about/README.md``:

- Adapter type (``[runtime].type``) — git, top-level.
- Operational tuning seed — git, ``[butler.runtime_seed]``.
- Model / session_timeout / CLI args — ``public.model_catalog`` (DB), resolved
  per spawn. Never pinned in roster.
- DB runtime row — source of truth for ``max_concurrent`` / ``max_queued`` /
  ``core_groups`` at runtime, seeded from the TOML on first boot.

Each test below projects one of those claims into the code or config and
fails loudly when something drifts. If you change the ownership model, update
this file *and* the precedence note — the two are siblings.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.config import ButlerType, load_config
from butlers.core.runtimes import get_adapter
from butlers.core.runtimes.base import list_registered_runtime_types

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROSTER_DIR = _REPO_ROOT / "roster"


def _iter_roster_configs() -> list[tuple[str, Path]]:
    entries: list[tuple[str, Path]] = []
    for entry in sorted(_ROSTER_DIR.iterdir()):
        if entry.is_dir() and (entry / "butler.toml").exists():
            entries.append((entry.name, entry))
    return entries


class TestRuntimeTypeIsSingleSource:
    """``[runtime].type`` is the only place in git that names the adapter."""

    def test_every_roster_resolves_to_registered_adapter(self):
        """Every roster's declared runtime adapter must exist in the registry.

        If a butler config is shipped with a typo or with an adapter that has
        not been implemented yet, daemon startup will fail at Spawner
        construction. This test surfaces that class of drift at load time.
        """
        registered = set(list_registered_runtime_types())
        assert registered, "runtime registry is empty — cannot validate adapters"

        failed: list[tuple[str, str]] = []
        for name, path in _iter_roster_configs():
            cfg = load_config(path)
            if cfg.runtime.type not in registered:
                failed.append((name, cfg.runtime.type))
            # get_adapter is what load_config already calls; keep the
            # assertion symmetric so a future divergence between list +
            # registry is visible.
            get_adapter(cfg.runtime.type)
        assert not failed, f"butlers naming unregistered runtime adapters: {failed}"

    def test_runtime_seed_never_duplicates_adapter_type(self):
        """A roster butler.toml that tries to set runtime_type under runtime_seed
        is rejected by the loader. This locks that rejection in place."""
        from butlers.config import ConfigError
        from butlers.config import load_config as load_cfg

        bad_dir = _REPO_ROOT / "tests" / "contracts" / "_synthesised_bad_runtime"
        bad_dir.mkdir(parents=True, exist_ok=True)
        try:
            (bad_dir / "butler.toml").write_text(
                '[butler]\nname = "bogus"\nport = 55550\n'
                '[butler.runtime_seed]\nruntime_type = "codex"\n'
            )
            with pytest.raises(ConfigError, match=r"runtime_seed\.runtime_type"):
                load_cfg(bad_dir)
        finally:
            (bad_dir / "butler.toml").unlink(missing_ok=True)
            bad_dir.rmdir()


class TestOperationalFieldsLiveInSeed:
    """``max_concurrent_sessions`` and friends come from ``runtime_seed``.

    The Spawner's DB accessor path overrides these at runtime; this test only
    enforces that the *git seed* exposes sane defaults so that the first-boot
    DB row is non-degenerate.
    """

    def test_operational_fields_nonzero(self):
        for name, path in _iter_roster_configs():
            cfg = load_config(path)
            assert cfg.runtime_seed.max_concurrent_sessions > 0, (
                f"{name}: runtime_seed.max_concurrent_sessions must be > 0"
            )
            assert cfg.runtime_seed.max_queued_sessions > 0, (
                f"{name}: runtime_seed.max_queued_sessions must be > 0"
            )
            # RuntimeConfig snapshot exposes the same values.
            assert cfg.runtime.max_concurrent_sessions == cfg.runtime_seed.max_concurrent_sessions
            assert cfg.runtime.max_queued_sessions == cfg.runtime_seed.max_queued_sessions


class TestStaffersAndButlersAreTyped:
    """Every roster agent declares a valid ``butler.type`` (butler | staffer).

    Rule 6 from vision.md says every agent has a governing document that
    controls its scope; the type field selects which document class applies.
    """

    def test_types_are_well_formed(self):
        for name, path in _iter_roster_configs():
            cfg = load_config(path)
            assert isinstance(cfg.type, ButlerType), (
                f"{name}: type must be ButlerType enum, got {type(cfg.type).__name__}"
            )
