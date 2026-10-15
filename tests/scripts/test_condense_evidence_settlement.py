"""REQ-testing-053: cancelled proof sides settle with complete observations."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_condensation_ledger as consumer  # noqa: E402
import condense_evidence as evidence  # noqa: E402

pytestmark = pytest.mark.unit

SLEEPER = (
    "import os, sys, time\n"
    "open(sys.argv[1] + '.tmp', 'w').write(f'{os.getppid()} {os.getpid()}')\n"
    "os.rename(sys.argv[1] + '.tmp', sys.argv[1])\n"
    "time.sleep(120)\n"
)
ROOT = (
    "import subprocess, sys, time\n"
    "subprocess.run([sys.executable, '-I', '-c', 'pass'], check=True)\n"
    f"subprocess.Popen([sys.executable, '-I', '-c', {SLEEPER!r}, sys.argv[1]])\n"
    "time.sleep(120)\n"
)


def test_killed_tracees_record_their_exits_without_signal_records(tmp_path):
    """REQ-testing-053: an owned cancellation's SIGKILLed tracees still record exits."""
    tool = shutil.which("strace")
    assert tool is not None, "the request-access observer requires installed strace"
    trace, marker = tmp_path / "trace", tmp_path / "ready"
    observer = subprocess.Popen(
        [tool, *consumer.REQUEST_TRACE_OPTIONS, "-o", str(trace)]
        + [sys.executable, "-I", "-c", ROOT, str(marker)],
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 60
        while not marker.exists():
            assert observer.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        root, child = (int(pid) for pid in marker.read_text().split())
        for pid in (root, child):
            os.kill(pid, signal.SIGKILL)
        observer.wait(timeout=60)
    finally:
        try:
            os.killpg(observer.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        observer.wait(timeout=60)
    lines = [line for line in trace.read_bytes().splitlines() if line]
    stream = evidence.RequestAccessStream(
        tmp_path, {"absent.json"}, tmp_path / "run.request.json", tmp_path / "run.json", "0" * 64
    )
    for line in lines:
        stream.consume(line)
    # Canonical delivered signals are nonterminal; the reaped ordinary child
    # still records its normal exit beside both killed tracees.
    assert not stream.failed and not stream.pending
    assert {root, child} < stream.actors and stream.actors == stream.exits
    assert {line.split()[0] for line in lines if line.endswith(b"+++ killed by SIGKILL +++")} == {
        str(root).encode(),
        str(child).encode(),
    }
    assert _ordinary_signal_history(tmp_path / "ordinary")


def _ordinary_signal_history(root):
    """Exercise genuine ordinary history and drop only an observed killed exit."""
    root.mkdir()
    control = root / "excluded.json"
    control.write_bytes(b'{"synthetic": "excluded handler input"}\n')
    records = consumer.source_records(root, [control.name])
    boundary = {"inputs": [control.name], "runtime_read": False, "descendant_execution": True}
    original = evidence.RequestAccessStream.consume
    outcomes = {}
    for species in ("complete", "missing-terminal", "handler-read"):
        output = root / (species + ".json")
        request = output.with_suffix(".request.json")
        nonce = os.urandom(32).hex()
        evidence.publish(request, {"nonce": nonce})
        output.with_suffix(".subject-probe").write_bytes(nonce.encode())
        sleeper = (
            "import os,sys,time\n"
            "assert os.getpgrp()==int(sys.argv[2])\n"
            "open(sys.argv[1], 'w').write('ready')\n"
            "time.sleep(120)\n"
        )
        program = (
            "import os,signal,subprocess,sys,time\n"
            f"assert open({str(request)!r},'rb').read()\n"
            f"assert open({str(output.with_suffix('.subject-probe'))!r},'rb').read()=={nonce.encode()!r}\n"
            "handled=[]\n"
            "def handler(signum,frame):\n"
            + (
                f"    assert open({str(control)!r},'rb').read()\n"
                if species == "handler-read"
                else ""
            )
            + "    handled.append(signum)\n"
            "signal.signal(signal.SIGUSR1,handler)\n"
            "os.kill(os.getpid(),signal.SIGUSR1)\n"
            "signal.raise_signal(signal.SIGUSR1)\n"
            "assert handled==[signal.SIGUSR1,signal.SIGUSR1]\n"
            "children=[]\n"
            "for name in ('term','kill'):\n"
            f"    marker={str(root)!r}+'/'+{species!r}+'-'+name\n"
            f"    child=subprocess.Popen([sys.executable,'-I','-c',{sleeper!r},marker,str(os.getpgrp())])\n"
            "    children.append(child)\n"
            "    deadline=time.monotonic()+50\n"
            "    while not os.path.exists(marker):\n"
            "        assert child.poll() is None and time.monotonic()<deadline\n"
            "        time.sleep(0.01)\n"
            "term,killed=children\n"
            "os.kill(term.pid,signal.SIGSTOP)\n"
            "pid,status=os.waitpid(term.pid,os.WUNTRACED)\n"
            "assert pid==term.pid and os.WIFSTOPPED(status)\n"
            "os.kill(term.pid,signal.SIGCONT)\n"
            "term.terminate()\n"
            "assert term.wait(timeout=5)==-signal.SIGTERM\n"
            "killed.kill()\n"
            "assert killed.wait(timeout=5)==-signal.SIGKILL\n"
            "subprocess.run([sys.executable,'-I','-c','pass'],check=True)\n"
            "assert handled==[signal.SIGUSR1,signal.SIGUSR1]\n"
        )
        notifications, terminals, streams, omitted = [], [], [], []

        def consume(stream, raw):
            if not streams:
                streams.append(stream)
            if b"--- " in raw:
                notifications.append(raw.decode("ascii"))
            if b"+++ " in raw:
                terminals.append(raw.decode("ascii"))
            if species == "missing-terminal" and raw.endswith(b"+++ killed by SIGTERM +++"):
                omitted.append(raw.decode("ascii"))
                return
            original(stream, raw)

        evidence.RequestAccessStream.consume = consume
        try:
            code, cleaned, access = evidence.request_access_run(
                [sys.executable, "-I", "-c", program],
                root,
                request,
                output,
                dict(os.environ),
                60,
                {control.name},
            )
        finally:
            evidence.RequestAccessStream.consume = original
        assert code == 0 and cleaned
        assert access["probe_accesses"] > 0 and access["subject_probes"] == 1
        assert access["subject_descendants"] == 3 and len(access["actors"]) == 4
        assert len(terminals) == 4 and len(set(terminals)) == 4
        assert sum("+++ killed by SIGTERM +++" in line for line in terminals) == 1
        assert sum("+++ killed by SIGKILL +++" in line for line in terminals) == 1
        assert any("--- SIGUSR1 " in line and "SI_USER" in line for line in notifications)
        assert any("--- SIGUSR1 " in line and "SI_TKILL" in line for line in notifications)
        assert any("--- stopped by SIGSTOP ---" in line for line in notifications)
        assert any("--- SIGCHLD " in line for line in notifications)
        if species == "complete":
            assert access["complete"] and access["accesses"] == access["reads"] == 0
            consumer.require_request_observation(access, boundary, {control.name}, records)
        else:
            with pytest.raises(
                consumer.EvidenceError, match="execution-request-access-observation-unproved"
            ):
                consumer.require_request_observation(access, boundary, {control.name}, records)
            if species == "missing-terminal":
                assert len(omitted) == 1 and not access["complete"]
                assert access["accesses"] == access["reads"] == 0
                assert not streams[0].failed and not streams[0].pending
                assert len(streams[0].actors - streams[0].exits) == 1
                assert sum(not actor["exited"] for actor in access["actors"]) == 1
            else:
                assert access["complete"] and access["accesses"] > 0 and access["reads"] > 0
        outcomes[species] = {
            "code": code,
            "cleaned": cleaned,
            "access": access,
            "notifications": notifications,
            "terminals": terminals,
            "omitted": omitted,
        }
    # A delivery can interrupt an unfinished call but cannot finish or erase it.
    notification = b"1 --- SIGUSR1 {si_signo=SIGUSR1, si_code=SI_USER, si_pid=1, si_uid=1} ---"
    stream = evidence.RequestAccessStream(root, {control.name}, request, output, nonce)
    stream.consume(b"1 wait4(2,  <unfinished ...>")
    stream.consume(notification)
    assert not stream.failed and stream.pending == {1: "wait4(2, "}
    assert not stream.exits and not stream.parents and not stream.executables
    assert stream.probes == stream.subject_reads == stream.accesses == stream.reads == 0
    stream.consume(b"1 +++ killed by SIGTERM +++")
    assert not stream.failed and stream.pending and not stream.finish()[0]
    stream.consume(notification)
    assert stream.failed  # A canonical notification after an exit is still invalid.
    for malformed in (
        notification[:-4],
        notification.replace(b"si_signo=SIGUSR1", b"si_signo=SIGTERM"),
        notification.replace(b"SI_USER", b"SI_UNSUPPORTED"),
        notification.replace(b"SIGUSR1", b"SIGINVENTED"),
        b"1 --- stopped by SIGUSR1 ---",
        b"1 --- SIGUSR1 arbitrary text ---",
    ):
        stream = evidence.RequestAccessStream(root, {control.name}, request, output, nonce)
        stream.consume(malformed)
        assert stream.failed and not stream.exits and not stream.finish()[0]
    stream = evidence.RequestAccessStream(root, {control.name}, request, output, nonce)
    stream.consume(b"1 +++ killed by SIGKILL +++")
    stream.consume(b"1 +++ killed by SIGKILL +++")
    assert stream.failed
    assert consumer.source_records(root, [control.name]) == records
    evidence.publish(root / "signal-contract.json", outcomes)
    return True
