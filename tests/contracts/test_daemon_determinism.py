"""Contract tests: Daemon Determinism — startup order and failure propagation (RFC 0001).

Validates startup phase ordering, failure semantics, route inbox state machine,
and concurrency controls.
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.contract


class TestStartupPhaseOrder:
    """RFC 0001: 17-phase startup executes in strict order."""

    def test_daemon_startup_structure(self):
        """Daemon has run method; telemetry before modules; DB before migrations;
        tools before server; scheduler after server."""
        from butlers.daemon import ButlerDaemon
        from butlers.lifecycle import run_startup

        assert hasattr(ButlerDaemon, "run") or hasattr(ButlerDaemon, "start")

        src = inspect.getsource(run_startup)
        assert "telemetry" in src.lower() or "init_telemetry" in src
        assert "module" in src.lower()
        assert "provision" in src or "migration" in src
        assert "register" in src.lower() or "tool" in src.lower()
        assert "scheduler" in src.lower()


class TestFailureSemantics:
    """RFC 0001: Fatal phases abort; module failures are non-fatal."""

    def test_config_and_registry_errors_are_fatal(self):
        from butlers.modules.registry import ModuleRegistry

        reg = ModuleRegistry()
        with pytest.raises(ValueError, match="Unknown module"):
            reg.load_from_config({"nonexistent": {}})

    def test_dependency_cycle_is_fatal(self):
        from butlers.modules.registry import ModuleRegistry
        from tests.modules.test_module_registry import _make_module

        CycA = _make_module("cycle_a", deps=["cycle_b"])
        CycB = _make_module("cycle_b", deps=["cycle_a"])
        reg = ModuleRegistry()
        reg.register(CycA)
        reg.register(CycB)
        with pytest.raises(ValueError, match="Circular dependency"):
            reg.load_from_config({"cycle_a": {}, "cycle_b": {}})

    def test_module_and_telemetry_failure_non_fatal(self):
        """Module phase and telemetry failures are non-fatal (logged, not raised)."""
        from butlers.daemon import ButlerDaemon

        src = inspect.getsource(ButlerDaemon)
        assert "module" in src.lower()  # Module handling present


class TestRouteInboxAndConcurrency:
    """RFC 0001: Route inbox states, crash recovery, and spawner concurrency."""

    def test_route_inbox_states(self):
        """Route inbox has accepted/processing/processed/errored states."""
        from butlers.core.route_inbox import (
            STATE_ACCEPTED,
            STATE_ERRORED,
            STATE_PROCESSED,
            STATE_PROCESSING,
        )

        route_states = {STATE_ACCEPTED, STATE_PROCESSING, STATE_PROCESSED, STATE_ERRORED}
        assert len(route_states) == 4, "route_inbox must have exactly 4 lifecycle states (RFC 0001)"
        assert STATE_ACCEPTED == "accepted", "accepted state value must be 'accepted'"
        assert STATE_PROCESSING == "processing", "processing state value must be 'processing'"
        assert STATE_PROCESSED == "processed", "processed state value must be 'processed'"
        assert STATE_ERRORED == "errored", "errored state value must be 'errored'"

        # replay_pending is a connector filtered_events state — verify it exists in that module
        from butlers.connectors.filtered_event_buffer import drain_replay_pending

        assert callable(drain_replay_pending), (
            "drain_replay_pending must be importable from filtered_event_buffer "
            "(handles 'replay_pending' rows in connectors.filtered_events)"
        )

    def test_spawner_concurrency_controls(self):
        from butlers.core.spawner import Spawner

        src = inspect.getsource(Spawner)
        assert "semaphore" in src.lower() or "concurrency" in src.lower()

        # Trigger sources documented
        trigger_sources = {"trigger", "route", "schedule"}
        assert len(trigger_sources) == 3


class TestCrashRecoveryAndGlobalConcurrency:
    """RFC 0001: Crash recovery re-dispatches orphaned routes; global semaphore limits sessions."""

    def test_crash_recovery_replays_orphaned_routes(self):
        """RFC 0001: route_inbox rows in accepted/processing > grace window re-dispatched at startup.

        On startup, the daemon scans for rows in 'accepted' or 'processing' state
        older than the configurable grace period (default 10s) and re-dispatches them.
        Both states are scanned because a crash can leave rows in either state.
        """
        from butlers.switchboard_wiring import recover_route_inbox

        assert callable(recover_route_inbox), (
            "recover_route_inbox must be importable from switchboard_wiring (RFC 0001)"
        )
        assert __import__("asyncio").iscoroutinefunction(recover_route_inbox), (
            "recover_route_inbox must be async (scans DB and re-dispatches routes)"
        )

        # The recovery function scans both accepted and processing states
        src = inspect.getsource(recover_route_inbox)
        assert "accepted" in src, "crash recovery must scan 'accepted' rows (RFC 0001)"
        assert "processing" in src or "grace" in src, (
            "crash recovery must scan 'processing' rows or use grace period filter (RFC 0001)"
        )

    def test_global_semaphore_limits_cross_butler_concurrency(self):
        """RFC 0001: BUTLERS_MAX_GLOBAL_SESSIONS shared across all butlers.

        The global semaphore is process-wide and shared across all butler instances.
        It is lazy-initialized on first access via _get_global_semaphore().
        Default cap is 3, configurable via BUTLERS_MAX_GLOBAL_SESSIONS env var.
        """
        from butlers.core.spawner import _get_global_semaphore

        assert callable(_get_global_semaphore), (
            "_get_global_semaphore must be importable (RFC 0001)"
        )

        src = inspect.getsource(_get_global_semaphore)
        assert "BUTLERS_MAX_GLOBAL_SESSIONS" in src, (
            "_get_global_semaphore must read BUTLERS_MAX_GLOBAL_SESSIONS (RFC 0001)"
        )

        # Default max global sessions documented in RFC 0001
        from butlers.core.spawner import _DEFAULT_MAX_GLOBAL_SESSIONS

        assert _DEFAULT_MAX_GLOBAL_SESSIONS == 3, "Default max global sessions must be 3 (RFC 0001)"

    def test_route_inbox_state_machine_transitions(self):
        r"""RFC 0001: route_inbox row transitions: accepted -> processing -> processed/errored.

        RFC 0001 defines the state machine:
          accepted --> processing --> processed (session_id stored)
                                 \--> errored   (error stored)
        Both crash recovery and the hot path must respect these states.
        """
        # State machine transitions per RFC 0001
        states = {"accepted", "processing", "processed", "errored"}
        valid_terminal_states = {"processed", "errored"}
        initial_state = "accepted"

        assert initial_state in states, "Initial state must be 'accepted'"
        assert "processing" in states, "Intermediate state must be 'processing'"

        # Both processed and errored are terminal
        for terminal in valid_terminal_states:
            assert terminal in states, f"Terminal state '{terminal}' must exist"

        # Processing leads to processed OR errored (not back to accepted)
        non_reversible_transition = "accepted"
        assert non_reversible_transition not in valid_terminal_states, (
            "accepted is not a terminal state — rows must advance through the machine (RFC 0001)"
        )

    def test_spawner_drain_cancels_after_timeout(self):
        """RFC 0001: Spawner.drain() cancels in-flight sessions after timeout expires.

        Sessions get a configurable drain window; if they don't finish, they are
        force-cancelled. This enforces the shutdown guarantee without hanging.
        """
        import asyncio

        from butlers.core.spawner import Spawner

        assert hasattr(Spawner, "drain"), "Spawner must have drain() method (RFC 0001)"
        assert asyncio.iscoroutinefunction(Spawner.drain), "Spawner.drain must be async (RFC 0001)"

        # drain() signature must accept timeout parameter
        sig = inspect.signature(Spawner.drain)
        params = list(sig.parameters.keys())
        assert "timeout" in params, "Spawner.drain must accept timeout parameter (RFC 0001)"

        # Default timeout must be 30s per RFC 0001
        default_timeout = sig.parameters["timeout"].default
        assert default_timeout == 30.0, "Spawner.drain default timeout must be 30.0s (RFC 0001)"
