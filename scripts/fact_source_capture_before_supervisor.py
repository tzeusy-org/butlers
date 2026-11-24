"""Confined Linux child subreaper; emits only closed numeric lifecycle records.

Ownership is established before exec. Orphans, including descendants which
change process groups, are adopted by this supervisor rather than lost between
ancestry polls. This changes only this process's unprivileged child-reaping flag.
"""

import ctypes
import errno
import json
import os
import select
import signal
import subprocess
import sys
import time

MAX_PROCESSES = 256
MAX_CONTROL_BYTES = 4096
TERM_GRACE = 0.02
SETTLEMENT_BOUND = 1.5
COUNT_LIMIT = 65535
REFUSAL_KEYS = (
    "scan_refused",
    "task_overflow",
    "children_overflow",
    "owned_overflow",
    "pending_overflow",
    "walk_overflow",
)


def count_refusal(diagnostics, key):
    if diagnostics is not None:
        if diagnostics["counts"][key] < COUNT_LIMIT:
            diagnostics["counts"][key] += 1
        else:
            diagnostics["counts_saturated"] = True


def first_scan_refusal(diagnostics, site, operation, error, before, after, task_scan=None):
    """Retain one closed classification, never an identity or exception value."""
    if diagnostics is None or diagnostics["first_scan_refusal"] is not None:
        return
    kind = (
        {errno.ENOENT: 1, errno.ESRCH: 2, errno.EACCES: 3, errno.EPERM: 4}.get(error.errno, 5)
        if isinstance(error, OSError)
        else 6
    )
    bits = int(before is not None) | (int(after is not None) << 1)
    if before is not None and after is not None and before[0] == after[0]:
        bits |= 4
    if after is not None and after[1] in ("Z", "X"):
        bits |= 8
    if before is not None and before[1] in ("Z", "X"):
        bits |= 16
    diagnostics["first_scan_refusal"] = {"s": site, "k": kind, "o": operation, "b": bits}
    diagnostics["first_task_scan"] = task_scan


def emit(value):
    wire = json.dumps(value, separators=(",", ":")) + "\n"
    if len(wire) > MAX_CONTROL_BYTES:
        raise ValueError("control bound")
    try:
        return os.write(1, wire.encode("ascii")) == len(wire)
    except OSError:
        # Losing the action's control pipe must not strand its owned children.
        return False


def process(pid):
    try:
        with open(f"/proc/{pid}/stat") as stream:
            value = stream.read(4096)
        fields = value[value.rfind(")") + 2 :].split()
        return int(fields[19]), fields[0]
    except (OSError, ValueError, IndexError):
        return None


# Linux __WALL includes clone children as well as conventional SIGCHLD children.
# Its availability is tested with both wait APIs before creating the workload.
ALL_CHILDREN = 0x40000000
WAIT_FLAGS = os.WNOHANG | ALL_CHILDREN
SETTLEMENT_BASIS = "linux-subreaper-all-child-v2"
CANCELLED_EXIT = 3


def direct_children(diagnostics):
    """Bounded positive discovery only; a live list never proves emptiness."""
    pid = os.getpid()
    identity = process(pid)
    operation = 1
    try:
        if not identity:
            raise ValueError("supervisor identity")
        tasks = os.listdir(f"/proc/{pid}/task")
        if tasks != [str(pid)]:
            count_refusal(diagnostics, "task_overflow")
            return set(), True
        operation = 2
        with open(f"/proc/{pid}/task/{pid}/children") as stream:
            operation = 3
            raw = stream.read(65536)
        if len(raw) == 65536:
            count_refusal(diagnostics, "children_overflow")
            return set(), True
        result = set()
        for value in raw.split():
            operation = 4
            if not value.isdecimal() or int(value) <= 1 or int(value) == pid:
                raise ValueError("direct child")
            if len(result) >= MAX_PROCESSES or int(value) in result:
                count_refusal(diagnostics, "children_overflow")
                return result, True
            result.add(int(value))
        return result, False
    except (OSError, ValueError) as error:
        count_refusal(diagnostics, "scan_refused")
        first_scan_refusal(diagnostics, 1, operation, error, identity, process(pid))
        return set(), True


def all_child_wait():
    """One validated consuming all-child wait; None is genuine ECHILD only."""
    try:
        result = os.waitpid(-1, WAIT_FLAGS)
    except ChildProcessError:
        return None
    if (
        type(result) is not tuple
        or len(result) != 2
        or any(type(value) is not int for value in result)
        or result[0] < 0
        or not 0 <= result[1] <= 65535
        or (result[0] == 0 and result[1] != 0)
        or (
            result[0] != 0
            and (result[0] <= 1 or not (os.WIFEXITED(result[1]) or os.WIFSIGNALED(result[1])))
        )
    ):
        raise ValueError("all child wait")
    return result


def held_direct_child(pid):
    """Prove waitable ownership without consuming it or relying on a PID stat.

    With default SIGCHLD, no autoreap and this sole waiter, the direct child
    remains kernel-held until our next consuming wait. There is no wait between
    this observation and signalling. None means a living owned child, not ECHILD.
    """
    info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT | ALL_CHILDREN)
    if info is not None and (
        type(getattr(info, "si_pid", None)) is not int
        or info.si_pid != pid
        or type(getattr(info, "si_code", None)) is not int
        or info.si_code not in (os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED)
        or type(getattr(info, "si_status", None)) is not int
        or not 0 <= info.si_status <= 255
    ):
        raise ValueError("direct ownership")


def main():
    if sys.platform != "linux" or len(sys.argv) < 4:
        return 2
    parent_pid, parent_start = int(sys.argv[1]), int(sys.argv[2])
    parent_identity = process(parent_pid)
    if (
        parent_pid != os.getppid()
        or parent_pid <= 1
        or not parent_identity
        or parent_identity[0] != parent_start
        or parent_start <= 0
        or parent_identity[1] in ("Z", "X")
        or os.listdir(f"/proc/{os.getpid()}/task") != [str(os.getpid())]
    ):
        return 2
    # Explicitly reset inherited ignore/no-zombie disposition. Python installs
    # sigaction without SA_NOCLDWAIT. Never call Popen.poll/wait: we are sole waiter.
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    if all_child_wait() is not None:
        return 2
    try:
        os.waitid(os.P_ALL, 0, os.WEXITED | WAIT_FLAGS | os.WNOWAIT)
    except ChildProcessError:
        pass
    else:
        return 2
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        return 2
    cancelling = []
    terminal = False

    def cancel(signum, frame):
        if not cancelling:
            cancelling.append(signum)
        if terminal:
            # Cleanup is closed. Keep cancellation parent-visible even after
            # the proof object is built/written and after main returns.
            raise SystemExit(CANCELLED_EXIT)

    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    ipc = os.environ.get("NODE_CHANNEL_FD")
    if ipc not in (None, "3"):
        return 2
    control = select.poll()
    control.register(1, select.POLLERR | select.POLLHUP | select.POLLNVAL)
    child = subprocess.Popen(
        sys.argv[3:],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        pass_fds=(3,) if ipc == "3" else (),
    )
    if ipc == "3":
        os.close(3)
    held = {child.pid}
    diagnostics = {
        "counts": dict.fromkeys(REFUSAL_KEYS, 0),
        "counts_saturated": False,
        "first_scan_refusal": None,
        "first_task_scan": None,
    }
    fatal = False
    outcome = None
    stopping_at = None

    def refuse(key="scan_refused"):
        nonlocal fatal
        fatal = True
        count_refusal(diagnostics, key)

    def reap():
        nonlocal outcome
        for _ in range(MAX_PROCESSES):
            try:
                result = all_child_wait()
            except (OSError, ValueError):
                refuse()
                return False
            if result is None:
                return True
            pid, status = result
            if pid == 0:
                return False
            held.discard(pid)
            if pid == child.pid:
                if outcome is not None:
                    refuse()
                    return False
                code = os.waitstatus_to_exitcode(status)
                outcome = {
                    "event": "outcome",
                    "code": code if code >= 0 else None,
                    "signal": signal.Signals(-code).name if code < 0 else None,
                }
                if not emit(outcome):
                    refuse()
                    cancel(signal.SIGTERM, None)
        refuse("walk_overflow")
        return False

    root = process(child.pid)
    if not root or root[0] <= 0:
        refuse()
    if not emit({"event": "ready", "pid": child.pid, "start_ticks": root[0] if root else 0}):
        refuse()
        cancel(signal.SIGTERM, None)
    while True:
        no_children = reap()
        parent_now = process(parent_pid)
        if not parent_now or parent_now[0] != parent_identity[0] or parent_now[1] in ("Z", "X"):
            refuse()
            cancel(signal.SIGTERM, None)
        if control.poll(0):
            refuse()
            cancel(signal.SIGTERM, None)
        if stopping_at is None and (outcome is not None or cancelling or fatal):
            stopping_at = time.monotonic()
        if stopping_at is not None:
            # The terminal proof is independent of a live discovery list. Any
            # surviving descendant still has a direct ancestry path to this
            # pre-exec subreaper; exiting parents adopt before becoming reapable.
            elapsed = time.monotonic() - stopping_at
            if no_children and outcome is not None and not held:
                terminal = True
                complete = not fatal and elapsed < SETTLEMENT_BOUND
                reason = "settled" if complete else "latched_refusal"
                if not emit(
                    {
                        "event": "diagnostic",
                        "reason": reason,
                        **diagnostics,
                        "owned_remaining": len(held),
                        "direct_remaining": 0,
                        "kernel_children_closed": True,
                    }
                ):
                    return 2
                if not emit(
                    {
                        "event": "settled",
                        "complete": complete,
                        "basis": SETTLEMENT_BASIS,
                        "all_child_echild": True,
                        "held_children": len(held),
                        "root_outcome": True,
                        "cancelled": bool(cancelling),
                    }
                ):
                    return 2
                return (CANCELLED_EXIT if cancelling else 0) if complete else 2
            if elapsed >= SETTLEMENT_BOUND:
                emit(
                    {
                        "event": "diagnostic",
                        "reason": "settlement_deadline",
                        **diagnostics,
                        "owned_remaining": len(held),
                        "direct_remaining": len(held),
                        "kernel_children_closed": no_children,
                    }
                )
                emit(
                    {
                        "event": "settled",
                        "complete": False,
                        "basis": SETTLEMENT_BASIS,
                        "all_child_echild": no_children,
                        "held_children": len(held),
                        "root_outcome": outcome is not None,
                        "cancelled": bool(cancelling),
                    }
                )
                return 2
            direct, incomplete = direct_children(diagnostics)
            fatal = fatal or incomplete
            if len(held | direct) > MAX_PROCESSES:
                refuse("owned_overflow")
            # No consuming wait occurs in this selection/signalling wave.
            # waitid(WNOWAIT) rejects nonchildren and holds zombie leaders until
            # the kernel allows their entire thread group to become reapable.
            signum = signal.SIGKILL if elapsed >= TERM_GRACE else signal.SIGTERM
            for pid in sorted(direct | held)[:MAX_PROCESSES]:
                try:
                    held_direct_child(pid)
                    if len(held) < MAX_PROCESSES or pid in held:
                        held.add(pid)
                    os.kill(pid, signum)
                except (OSError, ValueError, OverflowError):
                    refuse()
        time.sleep(0.01)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # Never export arguments, environment, exceptions or runtime output.
        raise SystemExit(2) from None
