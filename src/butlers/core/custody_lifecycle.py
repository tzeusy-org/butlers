"""Core-owned custody startup/shutdown for run and up daemon entrypoints.

Uses existing loaded Git configuration and existing database authority. No
request can supply these scopes, enroll a process, or retain an anchor. The
separate restricted API and connector startup paths need their own lifecycle
integration; this daemon helper does not claim to implement those paths.
"""

from __future__ import annotations

from typing import Any

from butlers.core.custody_admission import CustodyProfile
from butlers.core.custody_bootstrap import CustodyRuntime
from butlers.core.custody_source import CustodyError, digest


def daemon_profile(daemon: Any) -> CustodyProfile:
    """Derive a bounded profile without hashing/logging credential configuration."""
    actor = daemon.config.name
    source_kinds = {"deferred_notice", "domain_evidence", "scheduled_task"}
    operations = {"eligibility", "provider_start", "write"}
    if actor == "switchboard":
        source_kinds |= {"accepted_ingress", "owner_command", "provider_inventory"}
        operations |= {"hold", "release", "replaced", "yes", "no", "question", "revoke_sessions"}
    # Inter-butler transport still passes through Switchboard. This is a fixed
    # custody protocol audience, not a new declarative cross-butler permission.
    # Other domains cannot gain control operations merely by possessing a name.
    audiences = tuple(sorted({actor, "switchboard"}))
    fields = {
        "version": "custody-source.v1",
        "actor": actor,
        "role": daemon.db.role,
        "schema": daemon.config.db_schema,
        "source_kinds": sorted(source_kinds),
        "operations": sorted(operations),
        "audiences": list(audiences),
    }
    return CustodyProfile(
        actor,
        daemon.db.role,
        tuple(sorted(source_kinds)),
        tuple(sorted(operations)),
        audiences,
        digest(fields),
    )


async def start_daemon_custody(daemon: Any) -> None:
    """Start after actual schema migration, before pipeline/runtime/server work."""
    if getattr(daemon, "_custody_runtime", None) is not None:
        raise CustodyError("conflict")
    runtime = CustodyRuntime(daemon.db, daemon_profile(daemon))
    # Retain before awaiting startup: ordinary startup failure cleanup owns it.
    daemon._custody_runtime = runtime
    try:
        await runtime.start()
    except BaseException:
        daemon._custody_runtime = None
        await runtime.stop()
        raise


async def stop_daemon_custody(daemon: Any) -> None:
    """Revoke/close the real anchor before its owning pool is torn down."""
    runtime = getattr(daemon, "_custody_runtime", None)
    daemon._custody_runtime = None
    daemon._custody_mcp_service = None
    if runtime is not None:
        await runtime.stop()
