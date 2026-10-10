#!/usr/bin/env python3
"""Deny network/DB socket operations before a read-only guard starts (Linux).

Uses the runner's libseccomp, not a Python import monkeypatch. Descendants inherit
this restriction. Unsupported hosts refuse before executing the command.
"""

from __future__ import annotations

import ctypes
import errno
import os
import sys


def main() -> int:
    if sys.platform != "linux" or len(sys.argv) < 2:
        return 2
    try:
        lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
        lib.seccomp_init.argtypes = [ctypes.c_uint32]
        lib.seccomp_init.restype = ctypes.c_void_p
        lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
        lib.seccomp_rule_add.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        lib.seccomp_load.argtypes = [ctypes.c_void_p]
        lib.seccomp_release.argtypes = [ctypes.c_void_p]
        context = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
        if not context:
            return 2
        try:
            for name in (b"socket", b"connect", b"sendto", b"sendmsg"):
                number = lib.seccomp_syscall_resolve_name(name)
                if (
                    number < 0
                    or lib.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0) != 0
                ):
                    return 2
            if lib.seccomp_load(context) != 0:
                return 2
        finally:
            lib.seccomp_release(context)
        os.execv(sys.argv[1], sys.argv[1:])
    except (OSError, ValueError):
        print("pre-push: unavailable network confinement", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
