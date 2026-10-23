"""Init-only snapshot of purpose-specific projected transport material.

This never generates keys or reads model credentials. The serving process uses
only regular immutable copies, not Kubernetes' replaceable projection symlinks.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from butlers.session_host.control import _unique_object
from butlers.session_host.launch import Refused

_ROOT = Path("/etc/session-host")


def stage_transport(role: str, source: Path, destination: Path) -> None:
    if role not in {"control", "dashboard"}:
        raise Refused("invalid_control_config")
    names = ["ca.crt", "tls.crt", "tls.key"]
    if role == "control":
        names += ["host-ca.crt", "host.crt", "host.key"]
    config_name = role + "-auth.json"
    contents = {}
    # Only this init boundary follows an operator-owned projected source.
    for name in [config_name, *names]:
        descriptor = None
        try:
            descriptor = os.open(source / name, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) & 0o077
                or not 0 < before.st_size <= (8192 if name == config_name else 65536)
            ):
                raise ValueError
            content = os.read(descriptor, 65537)
            after = os.fstat(descriptor)
            if len(content) != before.st_size or (
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError
            contents[name] = content
        except (OSError, ValueError):
            raise Refused("invalid_control_config") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
    try:
        from butlers.session_host.auth import RemoteAuthSandbox
        from butlers.session_host.service_auth import _endpoint

        config = json.loads(contents[config_name], object_pairs_hook=_unique_object)
        ordinary = {
            "ca": str(_ROOT / "ca.crt"),
            "certificate": str(_ROOT / "tls.crt"),
            "private_key": str(_ROOT / "tls.key"),
        }
        if role == "dashboard":
            RemoteAuthSandbox(_endpoint(config, service=True))
            if config["tls"] != ordinary:
                raise ValueError
        else:
            if (
                type(config) is not dict
                or set(config) != {"tls", "host"}
                or config["tls"] != ordinary
            ):
                raise ValueError
            RemoteAuthSandbox(_endpoint(config["host"], service=False))
            if config["host"]["tls"] != {
                "ca": str(_ROOT / "host-ca.crt"),
                "certificate": str(_ROOT / "host.crt"),
                "private_key": str(_ROOT / "host.key"),
            }:
                raise ValueError
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise Refused("invalid_control_config") from None
    directory = None
    created = []
    try:
        directory = os.open(destination, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
        metadata = os.fstat(directory)
        if metadata.st_uid != os.geteuid():
            raise ValueError
        if stat.S_IMODE(metadata.st_mode) == 0o700 and set(os.listdir(directory)) == set(contents):
            # Repeated init may verify the exact original snapshot, never
            # silently retarget a serving volume to changed transport material.
            for name, expected in contents.items():
                descriptor = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
                )
                try:
                    item = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(item.st_mode)
                        or item.st_uid != os.geteuid()
                        or item.st_nlink != 1
                        or item.st_size != len(expected)
                        or stat.S_IMODE(item.st_mode) != (0o600 if name.endswith(".key") else 0o444)
                        or os.read(descriptor, 65537) != expected
                    ):
                        raise ValueError
                finally:
                    os.close(descriptor)
            return
        # Kubernetes emptyDir initially uses 0777. This init is its only
        # writer; the serving mount is installed read-only after completion.
        if stat.S_IMODE(metadata.st_mode) not in {0o755, 0o777} or os.listdir(directory):
            raise ValueError
        os.fchmod(directory, 0o700)
        for name, content in contents.items():
            descriptor = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
            )
            created.append(name)
            try:
                pending = memoryview(content)
                while pending:
                    written = os.write(descriptor, pending)
                    if written <= 0:
                        raise OSError
                    pending = pending[written:]
                os.fchmod(descriptor, 0o600 if name.endswith(".key") else 0o444)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        os.fsync(directory)
    except (OSError, ValueError):
        if directory is not None:
            for name in created:
                os.unlink(name, dir_fd=directory)
            if created:
                os.fchmod(directory, 0o755)
        raise Refused("invalid_control_config") from None
    finally:
        if directory is not None:
            os.close(directory)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("control", "dashboard"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        stage_transport(args.role, args.source, args.destination)
    except Refused:
        parser.exit(1, "session host refused: invalid_control_config\n")


if __name__ == "__main__":
    main()
