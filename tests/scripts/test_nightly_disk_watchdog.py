"""The nightly watchdog stops the producer below a floor and leaves evidence first."""

import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "_nightly_disk_watchdog", ROOT / "scripts/nightly_disk_watchdog.py"
)
assert SPEC is not None and SPEC.loader is not None
wd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wd)

DU = "5\t/a\n900\t/var\n40\t/b\n7000000\t/var/lib/containerd\n300\t/etc\nnoise\n"


def fake_run(argv):
    if argv[-1] == "/mnt":
        return "11\t/mnt/docker\n"
    if argv[:2] == ["df", "-h"]:
        return "Filesystem Size Used Avail\n/dev/root 70G 69G 1G\n"
    if argv[:2] == ["docker", "system"]:
        return "Images 3 1GB\n"
    return DU


def test_top_consumers_are_numeric_sorted_and_bounded():
    assert wd.top_consumers(DU, 3) == [
        (7000000, "/var/lib/containerd"),
        (900, "/var"),
        (300, "/etc"),
    ]


def test_snapshot_records_every_source_on_the_out_disk(tmp_path):
    path = wd.snapshot(tmp_path / "out", fake_run, reason="low")
    text = path.read_text()
    assert path.parent == tmp_path / "out"
    for needle in (
        "reason: low",
        "df -h /:",
        "7000000\t/var/lib/containerd",
        "docker system df",
        "du /mnt (top):",
        "11\t/mnt/docker",
    ):
        assert needle in text


def test_below_the_floor_it_snapshots_then_kills_the_producer_group(tmp_path):
    events = []
    code = wd.watch(
        4242,
        tmp_path,
        floor_mb=3072,
        free_mb=lambda: 100,
        run=lambda argv: (events.append("run"), fake_run(argv))[1],
        kill=lambda pid: events.append(("kill", pid)) or True,
        sleep=lambda _s: None,
        alive=lambda _pid: True,
        emit=events.append,
    )
    assert code == 3
    assert list(tmp_path.glob("snapshot-*.txt"))
    kill_at = events.index(("kill", 4242))
    assert "run" in events[:kill_at]  # the evidence is written before the kill
    assert any("::error title=root disk floor" in str(e) for e in events)


def test_a_healthy_run_only_samples_and_never_kills(tmp_path):
    ticks = iter(range(0, 1000, 60))
    alive = iter([True, True, True, True, False])
    killed = []
    out = []
    code = wd.watch(
        1,
        tmp_path,
        free_mb=lambda: 40000,
        run=fake_run,
        kill=lambda pid: killed.append(pid) or True,
        sleep=lambda _s: None,
        now=lambda: float(next(ticks)),
        alive=lambda _pid: next(alive),
        emit=out.append,
    )
    assert code == 0 and killed == []
    assert not list(tmp_path.glob("snapshot-*.txt"))
    assert (tmp_path / "disk-sampler.log").read_text().startswith("disk ")
    assert not any("root disk low" in line for line in out)


def test_a_low_but_above_floor_reading_warns_at_most_three_times(tmp_path):
    ticks = iter(range(0, 100000, 200))
    alive = iter([True] * 8 + [False])
    out = []
    wd.watch(
        1,
        tmp_path,
        floor_mb=1000,
        free_mb=lambda: 5000,
        run=fake_run,
        kill=lambda _pid: True,
        sleep=lambda _s: None,
        now=lambda: float(next(ticks)),
        alive=lambda _pid: next(alive),
        emit=out.append,
    )
    assert sum("root disk low" in line for line in out) == 3


def test_summary_prefers_the_last_snapshot_then_a_fresh_du(tmp_path):
    wd.snapshot(tmp_path, fake_run, reason="low")
    summary = wd.summarize(tmp_path, fake_run, count=2)
    assert summary == "7000000 /var/lib/containerd; 900 /var"
    assert wd.summarize(tmp_path / "none", fake_run, count=1) == "7000000 /var/lib/containerd"


def test_the_real_kill_takes_down_the_whole_process_group():
    child = subprocess.Popen(["sh", "-c", "sleep 60 & sleep 60; wait"], start_new_session=True)
    try:
        time.sleep(0.2)
        assert wd.kill_group(child.pid) is True
        assert child.wait(timeout=5) == -signal.SIGTERM
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            pass
        else:
            time.sleep(0.3)
            try:
                os.killpg(child.pid, 0)
                raise AssertionError("group still alive")
            except ProcessLookupError:
                pass
        assert wd.kill_group(child.pid) is False
    finally:
        child.kill()


def test_the_command_line_emits_the_top_consumers_warning(tmp_path):
    wd.snapshot(tmp_path, fake_run, reason="low")
    out = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(ROOT / "scripts/nightly_disk_watchdog.py"),
            "summarize",
            "--out",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.startswith("::warning title=top / consumers::")
    assert "7000000 /var/lib/containerd" in out
