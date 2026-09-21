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
        raise RuntimeError("TCP peer measurement is ambiguous; configuration unchanged")
    _, remote, _ = candidates.pop()
    address = remote.split(":")[0]
    if len(address) != 8:
        raise RuntimeError("Expected an IPv4 published-port peer")
    return str(ipaddress.IPv4Address(bytes.fromhex(address)[::-1]))


def snapshot(container: str):
    result = subprocess.run(
        ["docker", "exec", container, "cat", "/proc/net/tcp"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return established_rows(result.stdout)


def measure_peer(container: str, port: int) -> str:
    before = snapshot(container)
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        time.sleep(0.2)
        during = snapshot(container)
        if len(during - before) != 1:
            raise RuntimeError("Concurrent TCP connections make peer attribution ambiguous")
        # If the server closed our connection before sampling, an unrelated
        # short-lived connection must not be mistaken for our probe.
        connection.setblocking(False)
        try:
            connection.recv(1, socket.MSG_PEEK)
        except BlockingIOError:
            pass
        else:
            raise RuntimeError("Synthetic TCP probe closed or received unexpected data")
    # A closed client must disappear from ESTABLISHED before attribution.
    for _ in range(20):
        after = snapshot(container)
        if not (during - before) & after:
            return select_peer(before, during, after)
        time.sleep(0.1)
    raise RuntimeError("Synthetic TCP connection did not close; configuration unchanged")


def require_gateway(peer: str, gateways: str) -> None:
    allowed = {str(ipaddress.ip_address(line)) for line in gateways.splitlines() if line}
    if peer not in allowed:
        raise RuntimeError("Observed TCP peer is not a container network gateway")


def attest_gateway(container: str, peer: str) -> None:
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
    require_gateway(peer, result.stdout)


def update_env(path: Path, peer: str) -> None:
    if path.is_symlink():
        raise RuntimeError("Refusing to replace a symlinked environment file")
    original = path.read_text()
    pattern = re.compile(r"^[ \t]*(?:export[ \t]+)?" + KEY + r"=.*$", re.MULTILINE)
    if len(pattern.findall(original)) > 1:
        raise RuntimeError("Duplicate proxy peer settings; configuration unchanged")
    assignment = f"{KEY}={ipaddress.ip_address(peer)}"
    updated, count = pattern.subn(assignment, original)
    if not count:
        updated = original.rstrip("\n") + "\n" + assignment + "\n"
    if updated == original:
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".proxy-peer-", dir=path.parent)
    try:
        os.fchmod(descriptor, path.stat().st_mode & 0o777)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
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
            raise RuntimeError("TCP peer changed between probes; configuration unchanged")
        attest_gateway(args.container, first)
        update_env(args.env_file, first)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # Never expose subprocess output or dotenv contents on failure.
        parser.exit(
            1, "ERROR: Cannot establish one exact dashboard proxy peer; auth remains closed.\n"
        )
    print(first)


if __name__ == "__main__":
    main()
