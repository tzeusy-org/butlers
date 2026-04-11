"""Contract tests for the butler *runtime identity* contract.

Locks the ownership model documented in ``about/heart-and-soul/vision.md``
Rule 5 and the precedence note in ``about/README.md``:

- Adapter type — a fixed process-wide constant
  ``butlers.core.runtimes.DEFAULT_RUNTIME_TYPE``. No roster-level override.
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

from butlers.config import ButlerType, ConfigError, load_config
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE, get_adapter
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


class TestDefaultRuntimeTypeIsRegistered:
    """The fixed ``DEFAULT_RUNTIME_TYPE`` must resolve to a real adapter."""

    def test_default_runtime_type_is_registered(self):
        registered = set(list_registered_runtime_types())
        assert registered, "runtime registry is empty — cannot validate adapters"
        assert DEFAULT_RUNTIME_TYPE in registered, (
            f"DEFAULT_RUNTIME_TYPE={DEFAULT_RUNTIME_TYPE!r} not registered; "
            f"available adapters: {sorted(registered)}"
        )
        get_adapter(DEFAULT_RUNTIME_TYPE)  # must not raise

    def test_no_roster_butler_pins_runtime_type(self):
        """No roster butler.toml may contain a top-level [runtime] section.

        The loader refuses it — this test projects that refusal into the real
        roster so any commit that re-introduces the section trips the suite.
        """
        for name, path in _iter_roster_configs():
            toml_text = (path / "butler.toml").read_text()
            assert "[runtime]" not in toml_text, (
                f"{name}: top-level [runtime] section is forbidden; "
                "DEFAULT_RUNTIME_TYPE is the single source"
            )

    def test_runtime_seed_never_duplicates_adapter_type(self):
        """A roster butler.toml that tries to set runtime_type under runtime_seed
        is rejected by the loader. This locks that rejection in place."""
        bad_dir = _REPO_ROOT / "tests" / "contracts" / "_synthesised_bad_runtime"
        bad_dir.mkdir(parents=True, exist_ok=True)
        try:
            (bad_dir / "butler.toml").write_text(
                '[butler]\nname = "bogus"\nport = 55550\n'
                '[butler.runtime_seed]\nruntime_type = "codex"\n'
            )
            with pytest.raises(ConfigError, match=r"runtime_seed\.runtime_type"):
                load_config(bad_dir)
        finally:
            (bad_dir / "butler.toml").unlink(missing_ok=True)
            bad_dir.rmdir()

    def test_top_level_runtime_section_rejected(self):
        """The loader must reject any re-introduction of top-level [runtime]."""
        bad_dir = _REPO_ROOT / "tests" / "contracts" / "_synthesised_bad_runtime_top"
        bad_dir.mkdir(parents=True, exist_ok=True)
        try:
            (bad_dir / "butler.toml").write_text(
                '[butler]\nname = "bogus"\nport = 55551\n[runtime]\ntype = "codex"\n'
            )
            with pytest.raises(
                ConfigError, match=r"Top-level \[runtime\] section is no longer supported"
            ):
                load_config(bad_dir)
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
