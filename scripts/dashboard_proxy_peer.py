#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Measure the host-published API's exact TCP peer without sending HTTP data."""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path

KEY = "DASHBOARD_AUTH_TRUSTED_PROXY_PEERS"

CONCURRENT_ATTRIBUTION = "concurrent-attribution"
PROBE_UNAVAILABLE = "probe-unavailable"
UNSTABLE_PEER = "unstable-peer"
NON_GATEWAY_PEER = "non-gateway-peer"
UNSAFE_DOTENV = "unsafe-dotenv"
CONTAINER_INSPECTION = "container-inspection"

EXIT_BY_CATEGORY = {
    CONCURRENT_ATTRIBUTION: 75,
    PROBE_UNAVAILABLE: 69,
    UNSTABLE_PEER: 65,
    NON_GATEWAY_PEER: 66,
    UNSAFE_DOTENV: 67,
    CONTAINER_INSPECTION: 68,
}


class PeerAttributionError(RuntimeError):
    """A content-blind, allowlisted proxy-attribution failure."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


def established_rows(raw: str, port: int = 41200) -> set[tuple[str, str, str]]:
    rows = set()
    for line in raw.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 10 and fields[3] == "01":
            if int(fields[1].split(":")[1], 16) == port:
                rows.add((fields[1], fields[2], fields[9]))
    return rows


def select_peer(before, during, after) -> str:
    candidates = during - before
    if len(candidates) != 1 or candidates & after:
        raise PeerAttributionError(CONCURRENT_ATTRIBUTION)
    _, remote, _ = candidates.pop()
    address = remote.split(":")[0]
    if len(address) != 8:
        raise PeerAttributionError(PROBE_UNAVAILABLE)
    return str(ipaddress.IPv4Address(bytes.fromhex(address)[::-1]))


def snapshot(container: str):
    try:
        result = subprocess.run(
            ["docker", "exec", container, "cat", "/proc/net/tcp"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return established_rows(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise PeerAttributionError(CONTAINER_INSPECTION) from error


def measure_peer(container: str, port: int) -> str:
    before = snapshot(container)
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        time.sleep(0.2)
        during = snapshot(container)
        if len(during - before) != 1:
            raise PeerAttributionError(CONCURRENT_ATTRIBUTION)
        # If the server closed our connection before sampling, an unrelated
        # short-lived connection must not be mistaken for our probe.
        connection.setblocking(False)
        try:
            connection.recv(1, socket.MSG_PEEK)
        except BlockingIOError:
            pass
        else:
            raise PeerAttributionError(PROBE_UNAVAILABLE)
    # A closed client must disappear from ESTABLISHED before attribution.
    for _ in range(20):
        after = snapshot(container)
        if not (during - before) & after:
            return select_peer(before, during, after)
        time.sleep(0.1)
    raise PeerAttributionError(PROBE_UNAVAILABLE)


def require_gateway(peer: str, gateways: str) -> None:
    allowed = {str(ipaddress.ip_address(line)) for line in gateways.splitlines() if line}
    if peer not in allowed:
        raise PeerAttributionError(NON_GATEWAY_PEER)


def attest_gateway(container: str, peer: str) -> None:
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{range .NetworkSettings.Networks}}{{.Gateway}}{{"\\n"}}{{end}}',
                container,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PeerAttributionError(CONTAINER_INSPECTION) from error
    require_gateway(peer, result.stdout)


def update_env(path: Path, peer: str) -> None:
    if path.is_symlink():
        raise PeerAttributionError(UNSAFE_DOTENV)
    try:
        original = path.read_text()
    except OSError as error:
        raise PeerAttributionError(UNSAFE_DOTENV) from error
    pattern = re.compile(r"^[ \t]*(?:export[ \t]+)?" + KEY + r"=.*$", re.MULTILINE)
    if len(pattern.findall(original)) > 1:
        raise PeerAttributionError(UNSAFE_DOTENV)
    assignment = f"{KEY}={ipaddress.ip_address(peer)}"
    updated, count = pattern.subn(assignment, original)
    if not count:
        updated = original.rstrip("\n") + "\n" + assignment + "\n"
    if updated == original:
        return
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".proxy-peer-", dir=path.parent)
    except OSError as error:
        raise PeerAttributionError(UNSAFE_DOTENV) from error
    try:
        try:
            os.fchmod(descriptor, path.stat().st_mode & 0o777)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as error:
            raise PeerAttributionError(UNSAFE_DOTENV) from error
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args()
    try:
        first = measure_peer(args.container, args.port)
        if measure_peer(args.container, args.port) != first:
            raise PeerAttributionError(UNSTABLE_PEER)
        attest_gateway(args.container, first)
        update_env(args.env_file, first)
    except PeerAttributionError as error:
        parser.exit(
            EXIT_BY_CATEGORY[error.category],
            f"ERROR: dashboard proxy attribution failed (category={error.category}); "
            "auth remains closed.\n",
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # Never expose exception detail, subprocess output, or dotenv contents.
        parser.exit(
            EXIT_BY_CATEGORY[PROBE_UNAVAILABLE],
            f"ERROR: dashboard proxy attribution failed (category={PROBE_UNAVAILABLE}); "
            "auth remains closed.\n",
        )
    print(first)


if __name__ == "__main__":
    main()
