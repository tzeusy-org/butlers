"""Keep the nightly runner alive long enough to leave evidence of what filled ``/``.

Run 43545970763 (and 43532052607 before it) died with ``No space left on device`` while the
image-context producer ran, and a dead runner loses its logs. This watchdog runs beside the
producer: it samples free space on ``/`` (and available memory) and, below a floor, freezes the
producer, prunes docker's build cache (buildkit runs inside dockerd, so freezing the client does
not stop its writes), writes a snapshot to ``--out``, then terminates the producer's process
group so the step fails cleanly and the always() steps that follow can upload the evidence.

The hosted runner has one disk: ``/mnt`` is a directory on ``/`` (run 43554266906 reported both
as ``/ 145G``), so ``--out`` shares the disk being watched and the snapshot is kept small.

Run it as ``python3 -u`` (its stdout is a pipe, so unbuffered output is what survives a
SIGTERM). Everything it writes lands under ``--out``; stdout carries only sparse ``::notice::`` and
``::warning::`` annotations (the runner's own log lives on ``/``, which is the disk at risk).
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

DEFAULT_FLOOR_MB = 3072
DEFAULT_INTERVAL_S = 10.0
SAMPLE_EVERY_S = 120.0
LOW_WARNING_MB = 15000
MAX_LOW_WARNINGS = 3
TOP_N = 20
PRUNE = ("docker", "buildx", "prune", "--builder", "default", "--all", "--force")

Runner = Callable[[Sequence[str]], str]


def run_command(argv: Sequence[str], timeout: float = 120) -> str:
    """Best-effort command output; a missing tool or a timeout is recorded, never raised."""
    try:
        done = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"<{argv[0]} unavailable: {type(exc).__name__}>\n"
    return done.stdout + (done.stderr if done.returncode else "")


def root_free_mb(path: str = "/") -> int:
    return shutil.disk_usage(path).free // (1024 * 1024)


def memory_mb(meminfo: Path = Path("/proc/meminfo")) -> tuple[int, int]:
    """``(MemAvailable, SwapFree)`` in MB, or ``(-1, -1)`` when unreadable."""
    try:
        text = meminfo.read_text()
    except OSError:
        return (-1, -1)
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        number = rest.split()[0] if rest.split() else ""
        if number.isdigit():
            values[key] = int(number) // 1024
    return (values.get("MemAvailable", -1), values.get("SwapFree", -1))


def top_consumers(du_output: str, count: int) -> list[tuple[int, str]]:
    """The largest ``(kilobytes, path)`` rows of ``du -k`` output, largest first."""
    rows: list[tuple[int, str]] = []
    for line in du_output.splitlines():
        size, _, path = line.partition("\t")
        if size.strip().isdigit() and path:
            rows.append((int(size), path))
    rows.sort(reverse=True)
    return rows[:count]


def snapshot(out: Path, run: Runner = run_command, *, reason: str, extra: str = "") -> Path:
    """Write one evidence file under *out* and fsync it; returns its path."""
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H%M%S", time.gmtime())
    target = out / f"snapshot-{stamp}.txt"
    root_du = run(["sudo", "timeout", "100", "du", "-xk", "--max-depth=2", "/"])
    sections = [
        f"reason: {reason}",
        *([extra] if extra else []),
        "df -h /:\n" + run(["df", "-h", "/"]),
        "top of / (KiB):\n"
        + "".join(f"{size}\t{path}\n" for size, path in top_consumers(root_du, TOP_N)),
        "docker system df:\n" + run(["docker", "system", "df"]),
        "du /mnt (top):\n"
        + "".join(
            f"{size}\t{path}\n"
            for size, path in top_consumers(
                run(["sudo", "timeout", "100", "du", "-xk", "--max-depth=2", "/mnt"]), TOP_N
            )
        ),
    ]
    with target.open("w") as handle:
        handle.write("\n".join(sections))
        handle.flush()
        os.fsync(handle.fileno())
    return target


def signal_group(pid: int, sig: int) -> bool:
    """Signal the producer's process group, which ``setsid`` made with ``pgid == pid``.

    If *pid* is not its own group leader (``setsid`` forked, so *pid* is not the producer), a
    group signal could reach the step's own shell; signal the single process instead.
    """
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    return True


def watch(
    pid: int,
    out: Path,
    *,
    floor_mb: int = DEFAULT_FLOOR_MB,
    interval_s: float = DEFAULT_INTERVAL_S,
    free_mb: Callable[[], int] = root_free_mb,
    memory: Callable[[], tuple[int, int]] = memory_mb,
    run: Runner = run_command,
    send: Callable[[int, int], bool] = signal_group,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    alive: Callable[[int], bool] | None = None,
    emit: Callable[[str], None] = print,
) -> int:
    """Poll until the producer exits or ``/`` falls below the floor. Returns 0 or 3 (killed)."""
    out.mkdir(parents=True, exist_ok=True)
    log = out / "disk-sampler.log"
    is_alive = alive or _pid_alive
    next_sample = now()
    sample = 0
    warned = 0
    while is_alive(pid):
        free = free_mb()
        if free < floor_mb:
            # Annotate first (a SIGTERM must not race it), freeze the producer, prune the build
            # cache (buildkit lives in dockerd, so the freeze alone does not stop / filling),
            # snapshot, then stop it for good. SIGCONT delivers the pending SIGTERM to a
            # stopped group.
            emit(f"::error title=root disk floor::/ down to {free} MB; stopping the producer")
            send(pid, signal.SIGSTOP)
            try:
                try:
                    pruned = "build cache prune:\n" + run(list(PRUNE))
                except Exception as exc:  # best effort: the stop must go on
                    pruned = f"build cache prune failed: {type(exc).__name__}"
                path = snapshot(
                    out,
                    run,
                    reason=f"/ has {free} MB free, floor {floor_mb} MB",
                    extra=pruned,
                )
            finally:
                # A failing snapshot must never leave the producer frozen.
                send(pid, signal.SIGTERM)
                send(pid, signal.SIGCONT)
            emit(f"::notice title=root disk snapshot::{path}")
            return 3
        if now() >= next_sample:
            available, swap = memory()
            line = (
                f"disk {time.strftime('%H:%M:%S', time.gmtime())} root_free_mb={free}"
                f" mem_available_mb={available} swap_free_mb={swap}"
            )
            with log.open("a") as handle:
                handle.write(line + "\n")
                handle.write(run(["docker", "system", "df"]))
            if sample % 10 == 0:
                emit(f"::notice title=disk sample {sample}::{line}")
            if free < LOW_WARNING_MB and warned < MAX_LOW_WARNINGS:
                warned += 1
                emit(f"::warning title=root disk low::{line}")
            sample += 1
            next_sample = now() + SAMPLE_EVERY_S
        sleep(interval_s)
    return 0


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def summarize(out: Path, run: Runner = run_command, count: int = 5) -> str:
    """The top consumers of ``/`` for an annotation: the last snapshot, else a fresh ``du``."""
    for candidate in sorted(out.glob("snapshot-*.txt"), reverse=True):
        text = candidate.read_text()
        marker = "top of / (KiB):\n"
        if marker in text:
            body = text.split(marker, 1)[1].split("\n\n", 1)[0].splitlines()
            return "; ".join(line.replace("\t", " ") for line in body[:count])
    rows = top_consumers(run(["sudo", "timeout", "60", "du", "-xk", "--max-depth=2", "/"]), count)
    return "; ".join(f"{size} {path}" for size, path in rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    watch_parser = sub.add_parser("watch")
    watch_parser.add_argument("--pid", type=int, required=True)
    watch_parser.add_argument("--out", type=Path, required=True)
    watch_parser.add_argument("--floor-mb", type=int, default=DEFAULT_FLOOR_MB)
    watch_parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    summary_parser = sub.add_parser("summarize")
    summary_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "watch":
        return watch(args.pid, args.out, floor_mb=args.floor_mb, interval_s=args.interval)
    print(f"::warning title=top / consumers::{summarize(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
