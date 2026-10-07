"""Fixed dashboard CLI parent; one enrollment frame, no public host endpoint.

This uses existing configured bootstrap authority and ordinary unprivileged
process/socket APIs. It introduces no DB principal, signer, capability change,
privileged container or claim that a same-UID hostile child is isolated.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import socket
import sys
import uuid

from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.core.custody_admission import _sql
from butlers.core.custody_api import (
    dashboard_profile,
    install_api_parent_channel,
    receive_startup_frame,
    send_startup_frame,
)
from butlers.core.custody_bootstrap import _connect
from butlers.core.custody_installed import verify_installed_functions
from butlers.core.custody_source import CustodyError, canonical_uuid, closed_object
from butlers.db import Database


def validate_dashboard_manifest(manifest: dict, config: OwnerAuthConfig) -> None:
    """Only the child anchor's nonce/incarnation vary from the fixed profile."""
    closed_object(
        manifest,
        required={
            "nonce",
            "logical_actor",
            "role",
            "adapter_incarnation",
            "config_digest",
            "source_kinds",
            "operations",
            "audiences",
        },
    )
    profile = dashboard_profile(config)
    expected = {
        "logical_actor": profile.actor,
        "role": profile.role,
        "config_digest": profile.config_digest,
        "source_kinds": list(profile.source_kinds),
        "operations": list(profile.operations),
        "audiences": list(profile.audiences),
    }
    if {key: manifest[key] for key in expected} != expected:
        raise CustodyError("refused")
    canonical_uuid(manifest["nonce"])
    canonical_uuid(manifest["adapter_incarnation"])


async def run_dashboard_parent(host: str, port: int) -> int:
    """Actual independent CLI lifecycle used by Compose and Kubernetes too."""
    # Freeze the approved launch profile/login before creating the child. No
    # request or later mutable child manifest configures this allocation.
    launch_config = OwnerAuthConfig.from_env()
    restricted_login = os.environ.get("DASHBOARD_AUTH_DB_USER")
    parent, child_socket = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    parent.setblocking(False)
    child = host_connection = receipt = None
    loop = asyncio.get_running_loop()
    signals = (signal.SIGTERM, signal.SIGINT)
    try:
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "butlers.core.custody_api_parent",
            "--serve",
            "--host",
            host,
            "--port",
            str(port),
            "--parent-fd",
            str(child_socket.fileno()),
            pass_fds=(child_socket.fileno(),),
        )
        child_socket.close()

        def forward(sig):
            if child.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    child.send_signal(sig)

        for sig in signals:
            loop.add_signal_handler(sig, forward, sig)
        try:
            async with asyncio.timeout(120):
                manifest = await receive_startup_frame(parent)
                validate_dashboard_manifest(manifest, launch_config)
                host_connection = await _connect(Database.from_env("butlers"))
                verify_installed_functions(
                    await _sql(host_connection, "SELECT custody_admission.prove_interface()")
                )
                if not restricted_login:
                    raise CustodyError("unavailable")
                expected_login = await _sql(
                    host_connection,
                    "SELECT pg_catalog.jsonb_build_object('login_oid',oid) "
                    "FROM pg_catalog.pg_roles "
                    "WHERE rolname=$1 AND rolcanlogin",
                    restricted_login,
                )
                receipt = await _sql(
                    host_connection, "SELECT custody_admission.host_enroll($1::jsonb)", manifest
                )
                # The engine returns its actual nonce-bound anchor login; the
                # parent independently matches the fixed launch record before
                # releasing that receipt. Role/actor strings are insufficient.
                if receipt.get("login_oid") != expected_login["login_oid"]:
                    raise CustodyError("refused")
                await send_startup_frame(parent, {"receipt": receipt})
        except (CustodyError, TimeoutError):
            # Closing this one-shot channel leaves the custody door unavailable;
            # existing unrelated dashboard routes retain their normal lifecycle.
            pass
        finally:
            parent.close()
        return await child.wait()
    finally:
        for sig in signals:
            loop.remove_signal_handler(sig)
        parent.close()
        child_socket.close()
        if child is not None and child.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                child.terminate()
            try:
                async with asyncio.timeout(10):
                    await child.wait()
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    child.kill()
                await child.wait()
        if host_connection is not None:
            try:
                if receipt is not None:
                    with contextlib.suppress(CustodyError, TimeoutError), asyncio.timeout(5):
                        await _sql(
                            host_connection,
                            "SELECT custody_admission.host_revoke($1,$2)",
                            uuid.UUID(receipt["process_id"]),
                            receipt["control_epoch"],
                        )
            finally:
                await host_connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--parent-fd", type=int, required=True)
    args = parser.parse_args()
    install_api_parent_channel(socket.socket(fileno=args.parent_fd))
    from butlers.core.logging import configure_logging

    configure_logging()
    import uvicorn

    uvicorn.run(
        "butlers.api.app:create_app",
        host=args.host,
        port=args.port,
        factory=True,
        proxy_headers=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
