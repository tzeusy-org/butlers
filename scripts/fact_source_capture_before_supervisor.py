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


def task_identity(pid, task):
    """Observe only one already-selected owned task; never export its identity."""
    try:
        with open(f"/proc/{pid}/task/{task}/stat") as stream:
            value = stream.read(4096)
        fields = value[value.rfind(")") + 2 :].split()
        return (int(fields[19]), fields[0]), "known"
    except OSError as error:
        return None, {
            errno.ENOENT: "absent",
            errno.ESRCH: "absent",
            errno.EACCES: "denied",
            errno.EPERM: "denied",
        }.get(error.errno, "other")
    except (ValueError, IndexError):
        return None, "invalid"


def task_scan_observation(before, after, phase):
    """Closed diagnostic only; no permission to accept an incomplete scan."""
    first, first_category = before
    last, last_category = after
    return {
        "phase": phase,
        "before_probe": first_category,
        "after_probe": last_category,
        "before_known": first is not None,
        "after_known": last is not None,
        "same_task_start": first[0] == last[0] if first and last else None,
        "before_dead": first[1] in ("Z", "X") if first else None,
        "after_dead": last[1] in ("Z", "X") if last else None,
        "classification": (
            "task-entry-absent"
            if last_category == "absent"
            else "task-identity-changed"
            if first and last and first[0] != last[0]
            else "task-known"
            if last
            else "task-probe-unknown"
        ),
    }


def children(pid, diagnostics=None, site=0):
    identity = process(pid)
    truncated = False
    operation = 1  # task list
    selected_task = None
    task_before = (None, "unknown")
    task_phase = "task-list"
    try:
        # The supervisor is single-threaded. Other owned processes may not be.
        result = set()
        tasks = os.listdir(f"/proc/{pid}/task")
        truncated = len(tasks) > MAX_PROCESSES
        if truncated:
            count_refusal(diagnostics, "task_overflow")
        for task in tasks[:MAX_PROCESSES]:
            if task.isdecimal():
                selected_task = task
                task_before = task_identity(pid, task)
                task_phase = "child-open"
                operation = 2  # child-file open/context
                stream = open(f"/proc/{pid}/task/{task}/children")
                task_phase = "child-context-enter"
                with stream:
                    task_phase = "child-read"
                    operation = 3  # child-file read
                    raw = stream.read(65536)
                    truncated = truncated or len(raw) == 65536
                    if len(raw) == 65536:
                        count_refusal(diagnostics, "children_overflow")
                    task_phase = "child-parse"
                    for n in raw.split():
                        if len(result) >= MAX_PROCESSES:
                            count_refusal(diagnostics, "children_overflow")
                            return result, True
                        operation = 4  # child PID conversion
                        result.add(int(n))
                    task_phase = "child-context-exit"
                    operation = 2  # child-file context exit
        return result, truncated
    except (OSError, ValueError) as error:
        current = process(pid)
        # A task can disappear after listing while its same-identity owner
        # becomes a zombie. Only ENOENT plus a known departed identity closes
        # this scan; final settlement still requires kernel ECHILD below.
        if (
            isinstance(error, OSError)
            and error.errno == errno.ENOENT
            and identity
            and current
            and current[0] == identity[0]
            and current[1] in ("Z", "X")
        ):
            # Departure cannot erase overflow already observed in this scan.
            return set(), truncated
        # Permission, malformed data, unknown/reused identity and live actors
        # remain unobserved/truncated; never turn them into a positive scan.
        count_refusal(diagnostics, "scan_refused")
        task_scan = (
            task_scan_observation(task_before, task_identity(pid, selected_task), task_phase)
            if selected_task is not None
            else None
        )
        first_scan_refusal(diagnostics, site, operation, error, identity, current, task_scan)
        return set(), True


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
    ):
        return 2
    libc = ctypes.CDLL(None, use_errno=True)
    # PR_SET_CHILD_SUBREAPER. No capabilities or host-wide state changes.
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        return 2
    cancelling = []

    def cancel(signum, frame):
        if not cancelling:
            cancelling.append(signum)

    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    # A Node IPC socket, when present, goes directly to the SDK child. Neither
    # this supervisor nor stdout/stderr consumes or forwards provider output.
    ipc = os.environ.get("NODE_CHANNEL_FD")
    if ipc not in (None, "3"):
        return 2
    # Reader loss must be observed even when a resistant child emits nothing.
    # Poll only descriptor health; never read or export provider/control data.
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
    owned = {}
    diagnostics = {
        "counts": dict.fromkeys(REFUSAL_KEYS, 0),
        "counts_saturated": False,
        "first_scan_refusal": None,
        "first_task_scan": None,
    }

    def diagnostic(reason, direct, no_children):
        # Closed categories and saturated numeric counts only. This explains
        # an existing refusal; it never supplies missing ownership evidence.
        emit(
            {
                "event": "diagnostic",
                "reason": reason,
                **diagnostics,
                "owned_remaining": min(MAX_PROCESSES, len(owned)),
                "direct_remaining": min(MAX_PROCESSES, len(direct)),
                "kernel_children_closed": no_children,
            }
        )

    overflow = False
    outcome = None
    stopping_at = None
    root = process(child.pid)
    if root:
        owned[child.pid] = root[0]
    if not emit({"event": "ready", "pid": child.pid, "start_ticks": root[0] if root else 0}):
        cancel(signal.SIGTERM, None)
    while True:
        # Reap only children of this exact supervisor. Reparenting happens in
        # the kernel before an intermediate parent's departure can lose them.
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                pid = 0
            if not pid:
                break
            owned.pop(pid, None)
            if pid == child.pid and outcome is None:
                code = os.waitstatus_to_exitcode(status)
                outcome = {
                    "event": "outcome",
                    "code": code if code >= 0 else None,
                    "signal": signal.Signals(-code).name if code < 0 else None,
                }
                if not emit(outcome):
                    cancel(signal.SIGTERM, None)
        for pid, start in list(owned.items()):
            current = process(pid)
            if not current or current[0] != start or current[1] in ("Z", "X"):
                owned.pop(pid, None)
        direct, truncated = children(os.getpid(), diagnostics, site=1)
        overflow = overflow or truncated
        todo = list(direct)
        seen = set()
        while todo:
            pid = todo.pop()
            if pid in seen:
                continue
            seen.add(pid)
            current = process(pid)
            if current and current[1] not in ("Z", "X"):
                if len(owned) < MAX_PROCESSES or pid in owned:
                    owned[pid] = current[0]
                else:
                    overflow = True
                    count_refusal(diagnostics, "owned_overflow")
                descendants, truncated = children(pid, diagnostics, site=2)
                overflow = overflow or truncated
                room = max(0, MAX_PROCESSES - len(todo))
                remaining = descendants - seen
                overflow = overflow or len(remaining) > room
                if len(remaining) > room:
                    count_refusal(diagnostics, "pending_overflow")
                todo.extend(list(remaining)[:room])
            if len(seen) >= MAX_PROCESSES:
                overflow = overflow or bool(todo)
                if todo:
                    count_refusal(diagnostics, "walk_overflow")
                break
        for pid, start in list(owned.items()):
            current = process(pid)
            if not current or current[0] != start or current[1] in ("Z", "X"):
                owned.pop(pid, None)
        parent_now = process(parent_pid)
        if (
            not parent_identity
            or not parent_now
            or parent_now[0] != parent_identity[0]
            or parent_now[1] in ("Z", "X")
        ):
            cancel(signal.SIGTERM, None)
        if control.poll(0):
            cancel(signal.SIGTERM, None)
        if cancelling and stopping_at is None:
            stopping_at = time.monotonic()
        if stopping_at is not None:
            elapsed = time.monotonic() - stopping_at
            signum = (
                signal.SIGKILL
                if elapsed >= TERM_GRACE
                else (cancelling[0] if cancelling else signal.SIGTERM)
            )
            for pid, start in list(owned.items()):
                current = process(pid)
                if current and current[0] == start:
                    try:
                        os.kill(pid, signum)
                    except ProcessLookupError:
                        pass
            # ECHILD plus an empty adopted-child relation closes the ownership
            # boundary. An empty polling result alone never claims completion.
            no_children = False
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
                if pid:
                    owned.pop(pid, None)
                    if pid == child.pid and outcome is None:
                        code = os.waitstatus_to_exitcode(status)
                        outcome = {
                            "event": "outcome",
                            "code": code if code >= 0 else None,
                            "signal": signal.Signals(-code).name if code < 0 else None,
                        }
                        if not emit(outcome):
                            cancel(signal.SIGTERM, None)
            except ChildProcessError:
                no_children = True
            direct, truncated = children(os.getpid(), diagnostics, site=3)
            overflow = overflow or truncated
            if outcome is not None and no_children and not direct and not owned:
                diagnostic("latched_refusal" if overflow else "settled", direct, no_children)
                emit({"event": "settled", "complete": not overflow})
                return 0 if not overflow else 2
            if elapsed >= SETTLEMENT_BOUND:
                diagnostic("settlement_deadline", direct, no_children)
                emit({"event": "settled", "complete": False})
                return 2
        time.sleep(0.01)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # Never export arguments, environment, exceptions or runtime output.
        raise SystemExit(2) from None
