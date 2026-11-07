"""Software controls only; these do not execute the required PostgreSQL witness."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fact_source_capture_before_control as control  # noqa: E402
import fact_source_capture_before_observer as observer  # noqa: E402

pytestmark = pytest.mark.unit


def phase(name):
    return {
        "schema": "fact-capture-pg-phase.v1",
        "binding": {"nonce": "a" * 64},
        "phase": name,
        "collected": 1,
        "starts": 1,
        "phases": {
            "setup": "passed",
            "call": "failed" if name == "before" else "passed",
            "teardown": "passed",
        },
        "exception": "TimeoutError" if name == "before" else "none",
        "nested_acquire": name == "before",
        "owner_frame": name == "before",
        "pool_capacity_one": True,
        "runtime_role": True,
        "registered_mcp": True,
        "lookup_reached": "UNKNOWN",
        "capture_tasks_settled": True,
        "pool_reacquired": True,
        "durable_partial_reports": 0,
        "memory_partial_reports": 0 if name == "before" else None,
        "process_cleanup": True,
        "process_cleanup_basis": control.SETTLEMENT_BASIS,
        "process_cancelled": False,
        "sentinel_preserved": True,
        "pytest_exit": 1 if name == "before" else 0,
        "terminal": True,
    }


def test_only_executing_old_red_and_paired_green_with_complete_cleanup_qualify():
    """Setup/no-CALL, healthy old code, wrong failure and missing cleanup cannot qualify."""
    before, after = phase("before"), phase("after")
    import ci_lanes

    # The current fan-in consumes strict lane counts; phase diagnostics are not counts.
    with tempfile.TemporaryDirectory() as directory:
        counts = Path(directory) / "lane-counts.json"
        counts.write_text(json.dumps({"unit": 1, "integration": 1}))
        assert ci_lanes.read_lane_counts(counts) == {"unit": 1, "integration": 1}
        counts.write_text(json.dumps(before))
        with pytest.raises(ValueError):
            ci_lanes.read_lane_counts(counts)
    assert control.require_witness(before, after, {"nonce": "a" * 64}) == "WITNESS_COMPLETE"
    variants = [
        ("collected", 0),
        ("starts", 0),
        ("exception", "AssertionError"),
        ("nested_acquire", False),
        ("owner_frame", False),
        ("pool_capacity_one", False),
        ("runtime_role", False),
        ("registered_mcp", False),
        ("capture_tasks_settled", False),
        ("pool_reacquired", False),
        ("durable_partial_reports", 1),
        ("memory_partial_reports", 1),
        ("process_cleanup", False),
        ("process_cleanup_basis", "UNKNOWN"),
        ("process_cancelled", True),
        ("sentinel_preserved", False),
        ("terminal", False),
        ("pytest_exit", 0),
        ("pytest_exit", True),
        ("phases", {"setup": "failed"}),
        ("phases", {"setup": "passed", "call": "passed", "teardown": "passed"}),
        ("phases", {"setup": "passed", "call": "failed", "teardown": "failed"}),
        ("binding", {"nonce": "b" * 64}),
        ("untrusted", "PRIVATE_VALUE"),
    ]
    for key, value in variants:
        bad = copy.deepcopy(before)
        bad[key] = value
        with pytest.raises(ValueError):
            control.require_witness(bad, after, {"nonce": "a" * 64})
    for key, value in [
        ("pytest_exit", 1),
        ("phases", {"call": "failed"}),
        ("process_cleanup", False),
        ("terminal", False),
    ]:
        bad = copy.deepcopy(after)
        bad[key] = value
        with pytest.raises(ValueError):
            control.require_witness(before, bad, {"nonce": "a" * 64})


def test_disposable_workload_allows_only_the_exact_immutable_runtime_overlay(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    runtime = tmp_path / control.RUNTIME
    runtime.parent.mkdir(parents=True)
    runtime.write_text("old runtime\n")
    (tmp_path / "uv.lock").write_text("locked\n")
    git("add", ".")
    git(
        "-c",
        "user.name=Control",
        "-c",
        "user.email=control@example.invalid",
        "commit",
        "-qm",
        "old fixture",
    )
    old = git("rev-parse", "HEAD:" + control.RUNTIME)
    runtime.write_text("new runtime\n")
    git("add", ".")
    git(
        "-c",
        "user.name=Control",
        "-c",
        "user.email=control@example.invalid",
        "commit",
        "-qm",
        "candidate fixture",
    )
    monkeypatch.setattr(control, "CANDIDATE", git("rev-parse", "HEAD"))
    monkeypatch.setattr(control, "CANDIDATE_TREE", git("rev-parse", "HEAD^{tree}"))
    monkeypatch.setattr(control, "OLD_BLOB", old)
    monkeypatch.setattr(control, "NEW_BLOB", git("rev-parse", "HEAD:" + control.RUNTIME))
    healthy = control.workload_state(tmp_path, "after")
    assert healthy["differences"] == []
    control.overlay(tmp_path, "before")
    assert control.workload_state(tmp_path, "before")["differences"] == [control.RUNTIME]
    runtime.write_text("unapproved runtime\n")
    with pytest.raises(ValueError):
        control.workload_state(tmp_path, "before")
    control.overlay(tmp_path, "before")
    (tmp_path / "uv.lock").write_text("changed lock\n")
    with pytest.raises(ValueError):
        control.workload_state(tmp_path, "before")
    git("restore", "uv.lock")
    (tmp_path / "injected.py").write_text("unexpected\n")
    with pytest.raises(ValueError):
        control.workload_state(tmp_path, "before")
    (tmp_path / "injected.py").unlink()
    control.overlay(tmp_path, "after")
    assert control.workload_state(tmp_path, "after") == healthy


def _owned_retirement_kernel_fixture():
    return r'''"""Owned synthetic actors only; all process identities stay in RAM."""
import ctypes
import importlib.util
import importlib.machinery
import json
import os
import pathlib
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve()
OLD = NEW = pathlib.Path(os.environ['OWNED_SUPERVISOR'])

CLOSED_EVENTS = []
STAGE = 'root-start'
COUNTS = None
PRIMARY = None
PRIMARY_ERROR = None
ALLOWED_LINES = frozenset(json.loads(os.environ['KERNEL_ALLOWED_LINES']))
STAGES = frozenset(('root-start','before-wait','after-wait','before-cardinality','after-cardinality','cleanup','kernel-finished','canary','wrapper-import','wrapper-bind','wrapper-main','wrapper-verdict'))
def fault(error):
    line = None
    frame = error.__traceback__ if isinstance(error, BaseException) else None
    count = 0
    while frame is not None and count < 48:
        if frame.tb_frame.f_code.co_filename == str(HERE) and type(frame.tb_lineno) is int and frame.tb_lineno in ALLOWED_LINES:
            line = frame.tb_lineno
        frame = frame.tb_next
        count += 1
    if frame is not None:
        line = None
    return {'class': type(error).__name__ if type(error).__name__ in {'AssertionError','TimeoutError','OSError','ValueError','KeyError'} else 'other',
            'line': line, 'stage': STAGE if STAGE in STAGES else None}
def diagnostic_canary():
    private_local = 'PRIVATE_CANARY_LOCAL'
    assert False, 'PRIVATE_CANARY_MESSAGE'
def cardinality(values):
    return '0' if len(values) == 0 else '1' if len(values) == 1 else 'many'

def send(value):
    if sys.argv[1] in ('run', 'run-before-stat'):
        CLOSED_EVENTS.append(value)
    os.write(1, (json.dumps(value, separators=(',', ':'))+'\n').encode())

def owner(address, case):
    channel = socket.socket(socket.AF_UNIX)
    channel.settimeout(55)
    channel.connect(address)
    ready, leave = threading.Event(), threading.Event()
    actor = {}
    def worker():
        code = ("import os,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "p=os.fork();os.setsid() if p==0 else None;time.sleep(50)")
        child = subprocess.Popen([sys.executable, '-c', code],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        # Confirm the resistant intermediary forked before exposing the actor.
        until = time.monotonic()+5
        while not pathlib.Path(f'/proc/{child.pid}/task/{child.pid}/children').read_text().strip():
            assert time.monotonic() < until
            time.sleep(0.001)
        actor.update(pid=os.getpid(), tid=threading.get_native_id(), child=child.pid)
        ready.set()
        if case == 'leader-thread':
            channel.sendall(json.dumps(actor).encode()+b'\n')
            while channel.recv(64):
                pass
        else:
            leave.wait(50)
    thread = threading.Thread(target=worker)
    thread.start()
    assert ready.wait(5)
    if case == 'leader-thread':
        ctypes.CDLL(None).pthread_exit(None)
        raise AssertionError('leader did not exit')
    channel.sendall(json.dumps(actor).encode()+b'\n')
    while True:
        command = channel.recv(64)
        if command == b'exit-thread':
            leave.set()
            thread.join(5)
            assert not thread.is_alive()
            channel.sendall(b'thread-exited')
        elif not command:
            break
    leave.set()
    thread.join(5)


def await_retirement(owner_probe, task_probe, owner_start, task_start, clock, pause):
    assert type(owner_start) is int and owner_start > 0
    assert type(task_start) is int and task_start > 0
    until = clock() + 0.05
    for _ in range(64):
        owner = owner_probe()
        assert isinstance(owner, tuple) and len(owner) == 2
        assert type(owner[0]) is int and owner[0] == owner_start
        assert type(owner[1]) is str and owner[1] in 'RSDTtPIWK' and owner[1] not in 'ZX'
        task, category = task_probe()
        if category == 'absent':
            assert task is None
            return
        assert category == 'known' and isinstance(task, tuple) and len(task) == 2
        assert type(task[0]) is int and task[0] == task_start
        assert type(task[1]) is str and task[1] in 'RSDTtXZPIWK'
        assert clock() < until
        pause(0.001)
    raise AssertionError('bounded retirement')

def wrapper(case, address, parent, parent_start):
    global STAGE
    STAGE = 'wrapper-import'
    module_path = OLD if case == 'original-gap' else NEW
    loader = importlib.machinery.SourceFileLoader('owned_synthetic_supervisor', str(module_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    STAGE = 'wrapper-bind'
    server = socket.socket(socket.AF_UNIX)
    server.bind(address)
    server.listen(1)
    server.settimeout(5)
    connection = None
    actor = None
    positioned = False
    adopted = False
    original_listdir, original_open, original_waitpid = os.listdir, open, os.waitpid
    original_emit = module.emit
    original_process, original_poll = module.process, module.select.poll
    cached_tasks = None
    foreign_scans = 0
    omitted = 0
    all_wait_pending = False
    adopted_count = 0
    def get_actor():
        nonlocal actor, connection
        if actor is None:
            connection, _ = server.accept()
            connection.settimeout(5)
            actor = json.loads(connection.recv(512))
            assert set(actor) == {'pid', 'tid', 'child'}
            assert all(type(n) is int and n > 1 for n in actor.values())
            send({'fixture': 'owned-actors', 'actor': actor})
        return actor
    def task_probe(a):
        try:
            with original_open(f"/proc/{a['pid']}/task/{a['tid']}/stat") as stream:
                raw = stream.read(4096)
            fields = raw[raw.rfind(')')+2:].split()
            return (int(fields[19]), fields[0]), 'known'
        except FileNotFoundError:
            return None, 'absent'
    def emitting(value):
        nonlocal positioned, cached_tasks
        if value.get('event') == 'ready':
            a = get_actor()
            cached_tasks = original_listdir(f"/proc/{a['pid']}/task")
            assert str(a['tid']) in cached_tasks
            if case == 'leader-thread':
                until = time.monotonic()+0.05
                while module.process(a['pid'])[1] != 'Z':
                    assert time.monotonic() < until
                    time.sleep(0.001)
                assert task_probe(a)[1] == 'known'
            else:
                bound_owner, (bound_task, category) = module.process(a['pid']), task_probe(a)
                assert bound_owner is not None and bound_owner[1] not in ('Z', 'X')
                assert category == 'known' and bound_task is not None
                connection.sendall(b'exit-thread')
                assert connection.recv(64) == b'thread-exited'
                await_retirement(lambda: module.process(a['pid']), lambda: task_probe(a),
                                 bound_owner[0], bound_task[0], time.monotonic, time.sleep)
            positioned = True
            send({'fixture': 'positioned', 'thread_entry_absent': case != 'leader-thread'})
        return original_emit(value)
    def listing(path):
        nonlocal foreign_scans
        if actor is not None and path == f"/proc/{actor['pid']}/task":
            foreign_scans += 1
            # This is the exact real listing captured before the joined task
            # retired, delayed until the legacy scanner consumes it. The new
            # protocol never requests a foreign task list.
            return list(cached_tasks)
        return original_listdir(path)
    def opening(path, *args, **kwargs):
        nonlocal omitted
        if (case == 'omitted-direct' and positioned
                and str(path) == f'/proc/{os.getpid()}/task/{os.getpid()}/children'):
            import io
            with original_open(path, *args, **kwargs) as stream:
                actual = stream.read(65536)
            if actual.strip():
                omitted += 1
            # Discovery is allowed to omit. It must not become emptiness proof.
            return io.StringIO('')
        return original_open(path, *args, **kwargs)
    def waiting(*args):
        nonlocal adopted, adopted_count, all_wait_pending
        result = original_waitpid(*args)
        if actor is not None:
            if result == (0, 0) and args[1] & 0x40000000:
                all_wait_pending = True
            if result[0] > 0 and result[0] != actor['pid']:
                adopted_count += 1
            if result[0] == actor['child']:
                adopted = True
        return result
    def process_probe(pid):
        if case == 'parent-loss' and positioned and pid == int(parent):
            return None
        return original_process(pid)
    class ReaderPoll:
        def __init__(self):
            self.actual = original_poll()
        def register(self, *args):
            return self.actual.register(*args)
        def poll(self, *args):
            if case == 'reader-loss' and positioned:
                return [(1, 16)]
            return self.actual.poll(*args)
    module.process = process_probe
    module.select.poll = ReaderPoll
    module.os.listdir = listing
    module.open = opening
    module.os.waitpid = waiting
    module.emit = emitting
    sys.argv = [str(module_path), parent, parent_start, sys.executable, str(HERE), 'owner', address, case]
    code = 2
    try:
        STAGE = 'wrapper-main'
        code = module.main()
        STAGE = 'wrapper-verdict'
        try:
            original_waitpid(-1, os.WNOHANG | 0x40000000)
            echild = False
        except ChildProcessError:
            echild = True
        send({'fixture': 'kernel-closure', 'ECHILD': echild,
              'adopted_descendant_reaped': adopted,
              'descendant_absent': actor is not None and module.process(actor['child']) is None,
              'positioned': positioned, 'foreign_task_scans': foreign_scans,
              'omitted_discoveries': omitted, 'all_child_wait_pending': all_wait_pending,
              'adopted_count': adopted_count})
    finally:
        module.os.listdir = original_listdir
        module.open = original_open
        module.os.waitpid = original_waitpid
        module.emit = original_emit
        module.process = original_process
        module.select.poll = original_poll
        if connection is not None:
            connection.close()
        server.close()
    return code


def start_ticks(pid):
    value = pathlib.Path(f'/proc/{pid}/stat').read_text()
    return int(value[value.rfind(')')+2:].split()[19])


def run():
    global STAGE, COUNTS, PRIMARY, PRIMARY_ERROR
    assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
    sentinel = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    rows = []
    known = {}
    active = None
    try:
        with tempfile.TemporaryDirectory(prefix='owned-task-gap-') as directory:
            for case in ((sys.argv[2],) if len(sys.argv) > 2 else ('retired-task',)):
                events = []
                begin = time.monotonic()
                active = subprocess.Popen([sys.executable, str(HERE), 'wrapper', case,
                                           str(pathlib.Path(directory)/(case+'.sock')),
                                           str(os.getpid()), str(start_ticks(os.getpid()))],
                                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, start_new_session=True)
                known[active.pid] = start_ticks(active.pid)
                selector = selectors.DefaultSelector()
                selector.register(active.stdout, selectors.EVENT_READ)
                wire = bytearray()
                stopped = False
                while selector.get_map() or active.poll() is None:
                    assert time.monotonic()-begin < 60, 'owned child bound'
                    for key, _ in selector.select(0.05):
                        chunk = os.read(key.fileobj.fileno(), 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        wire.extend(chunk)
                        assert len(wire) <= 16384
                        while b'\n' in wire:
                            line, _, tail = wire.partition(b'\n')
                            wire[:] = tail
                            value = json.loads(line)
                            if value.get('fixture') == 'owned-actors':
                                for owned in (value['actor']['pid'], value['actor']['child']):
                                    known[owned] = start_ticks(owned)
                            else:
                                events.append(value)
                                if value.get('fixture') == 'wrapper-fault':
                                    CLOSED_EVENTS.append(value)
                            if value.get('fixture') == 'positioned' and not stopped:
                                active.send_signal(signal.SIGTERM)
                                stopped = True
                STAGE = 'before-wait'
                active.wait(timeout=5)
                STAGE = 'after-wait'
                selector.close()
                active.stdout.close()
                assert not wire
                diagnostics = [x for x in events if x.get('event') == 'diagnostic']
                settled = [x for x in events if x.get('event') == 'settled']
                closures = [x for x in events if x.get('fixture') == 'kernel-closure']
                STAGE = 'before-cardinality'
                COUNTS = [cardinality(x) for x in (diagnostics, settled, closures)]
                assert len(diagnostics) == len(settled) == len(closures) == 1
                STAGE = 'after-cardinality'
                d, s, closure = diagnostics[0], settled[0], closures[0]
                send({'fixture': 'case-summary', 'case': case, 'stage': 'kernel-verdict', 'exit': active.returncode, 'diagnostic': d, 'settled': s, 'closure': closure})
                assert closure['positioned']
                assert sentinel.poll() is None
                row = {'case': case, 'exit': active.returncode,
                       'elapsed_s': time.monotonic()-begin,
                       'first_scan_refusal': d.get('first_scan_refusal'),
                       'first_task_scan': d.get('first_task_scan'),
                       'counts': d['counts'], 'reason': d['reason'],
                       'kernel_children_closed': d['kernel_children_closed'],
                       'settled_complete': s['complete'],
                       'adopted_descendant_reaped': closure['adopted_descendant_reaped'], 'real_ECHILD': closure['ECHILD'],
                       'sentinel_preserved': True}
                row.update(foreign_task_scans=closure['foreign_task_scans'],
                           all_child_wait_pending=closure['all_child_wait_pending'],
                           omitted_discoveries=closure['omitted_discoveries'],
                           adopted_count=closure['adopted_count'],
                           kernel_domain_basis=s.get('basis'), supervisor_cancelled=s.get('cancelled'))
                if case in ('parent-loss', 'reader-loss'):
                    assert active.returncode == 2 and s['complete'] is False
                    assert closure['ECHILD'] and closure['adopted_descendant_reaped']
                    assert d['reason'] == 'latched_refusal' and d['counts']['scan_refused'] > 0
                elif case == 'omitted-direct':
                    assert closure['all_child_wait_pending']
                    assert closure['omitted_discoveries'] > 0
                    assert closure['ECHILD'] is False
                    assert active.returncode == 2 and s['complete'] is False, 'pending child must refuse'
                    assert d['reason'] == 'settlement_deadline'
                else:
                    assert active.returncode == 3 and s['complete'], 'positive kernel settlement after cancellation required'
                    assert s['cancelled'] is True
                    assert s['basis'] == 'linux-subreaper-all-child-v2'
                    assert closure['ECHILD'] and closure['adopted_descendant_reaped']
                    assert closure['adopted_count'] >= 2
                    assert closure['descendant_absent'] and sentinel.poll() is None
                    assert closure['foreign_task_scans'] == 0
                    assert d['first_scan_refusal'] is None and d['reason'] == 'settled'
                rows.append(row)
                active = None
        return {'cases': rows, 'real_P0_PG_UV_workload': False}
    except BaseException as error:
        PRIMARY = fault(error)
        PRIMARY_ERROR = error
        raise
    finally:
        STAGE = 'cleanup'
        if active is not None and active.poll() is None:
            active.kill()
            active.wait(timeout=5)
        sentinel.terminate()
        sentinel.wait(timeout=5)
        until = time.monotonic()+5
        while True:
            try:
                while os.waitpid(-1, os.WNOHANG | 0x40000000)[0]:
                    pass
            except ChildProcessError:
                break
            assert time.monotonic() < until
            direct = pathlib.Path(f"/proc/{os.getpid()}/task/{threading.get_native_id()}/children")
            children = direct.read_text().split()
            assert len(children) <= 256
            for value in children:
                pid = int(value)
                # WNOWAIT proves held direct ownership; no consuming wait
                # between that proof and this signal.
                os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT | 0x40000000)
                os.kill(pid, signal.SIGKILL)
            time.sleep(0.01)
        # This process owns no children after its bounded cleanup.
        groups_absent = True
        for group in (*known, sentinel.pid):
            try:
                os.killpg(group, 0)
                groups_absent = False
            except ProcessLookupError:
                pass
        send({'fixture': 'final-cleanup', 'all_owned_groups_absent': groups_absent,
              'real_ECHILD': True, 'sentinel_reaped': sentinel.returncode is not None})
        assert groups_absent

if __name__ == '__main__':
    if sys.argv[1] == 'owner':
        owner(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == 'wrapper':
        try:
            code = wrapper(*sys.argv[2:])
        except BaseException as error:
            send({'fixture': 'wrapper-fault', 'fault': fault(error)})
            code = 2
        raise SystemExit(code)
    else:
        failure = None
        cleanup_failure = None
        result = None
        try:
            result = run()
        except BaseException as error:
            failure = fault(error)
            cleanup_failure = failure if PRIMARY and error is not PRIMARY_ERROR else None
        projection = {'schema': 'owned-kernel-closed.v1', 'stage': 'kernel-finished', 'exception_class': None if failure is None else failure['class'], 'first_fault': PRIMARY or failure, 'cleanup_fault': cleanup_failure, 'cardinality': COUNTS, 'events': CLOSED_EVENTS, 'result': result}
        encoded = json.dumps(projection)
        assert len(encoded.encode()) <= 16384
        descriptor = os.open(os.environ['KERNEL_PROJECTION'], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(encoded)
        raise SystemExit(2 if failure else 0)
'''


def test_owned_domain_settles_resistant_descendant_and_preserves_unrelated_sentinel(
    tmp_path, monkeypatch
):
    sentinel = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    try:
        healthy = control.run_owned([sys.executable, "-c", "pass"], os.environ.copy(), 5)
        assert healthy["exit"] == 0 and healthy["cleanup_complete"], healthy["observation"]
        assert healthy["observation"]["child_exit"] == 0
        assert healthy["observation"]["supervisor_exit"] == 0
        assert healthy["observation"]["settled_complete"] is True
        assert healthy["observation"].get("first_scan_refusal") is None
        refused = control.run_owned(
            [sys.executable, "-c", "import sys;print('PRIVATE_CHILD');sys.exit(7)"],
            os.environ.copy(),
            5,
        )
        assert refused["exit"] == 7 and refused["cleanup_complete"], refused["observation"]
        assert refused["observation"]["child_exit"] == 7
        assert refused["observation"]["reason"] == "settled"
        diagnostic = control.refusal(
            ValueError("environment-prepare"),
            {"stage": "environment-prepare", "preparation": refused["observation"]},
            {"nonce": "a" * 64},
        )
        assert diagnostic["preparation"]["child_exit"] == 7
        assert diagnostic["pg_call"] == diagnostic["cleanup"] == "UNKNOWN"
        assert "PRIVATE" not in json.dumps(diagnostic)
        escaped = tmp_path / "escaped"
        code = (
            "import os,signal,time,pathlib;"
            "p=os.fork();"
            f"pathlib.Path({str(escaped)!r}).write_text(str(p));"
            "os.setsid() if p==0 else None;"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
        )
        hung = control.run_owned([sys.executable, "-c", code], os.environ.copy(), 2)
        assert hung["timed_out"] and hung["cleanup_complete"] and hung["exit"] != 0, hung[
            "observation"
        ]
        assert hung["observation"]["timed_out"] is True
        assert hung["observation"]["child_signal"] == "SIGKILL"
        assert hung["observation"]["kernel_children_closed"] is True
        private = control.lifecycle_observation(
            [
                {"event": "outcome", "code": "PRIVATE_EXIT", "signal": "PRIVATE_SIGNAL"},
                {
                    "event": "diagnostic",
                    "reason": "PRIVATE_REASON",
                    "counts": {"scan_refused": "PRIVATE_COUNT"},
                    "counts_saturated": "PRIVATE_BOOL",
                },
            ],
            "PRIVATE_SUPERVISOR",
            False,
            False,
            True,
        )
        assert private["child_exit"] is private["supervisor_exit"] is None
        assert private["counts"]["scan_refused"] is None
        assert private["protocol_refused"] and "PRIVATE" not in json.dumps(private)
        latched = control.lifecycle_observation(
            [
                {"event": "outcome", "code": 1},
                {"event": "settled", "complete": False},
                {
                    "event": "diagnostic",
                    "reason": "latched_refusal",
                    "counts": {key: int(key == "scan_refused") for key in control.REFUSAL_COUNTS},
                    "counts_saturated": False,
                    "first_scan_refusal": {"s": 2, "k": 1, "o": 2, "b": 7},
                    "kernel_children_closed": True,
                },
            ],
            2,
            False,
            False,
            False,
        )
        assert latched["reason"] == "latched_refusal" and latched["counts"]["scan_refused"] == 1
        assert latched["settled_complete"] is False and latched["supervisor_exit"] == 2
        assert latched.get("first_scan_refusal") == {"s": 2, "k": 1, "o": 2, "b": 7}
        # Exercise the actual local producer's closed refusal branch without procfs IO.
        import builtins
        import errno
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_fact_capture_supervisor", control.SUPERVISOR
        )
        supervisor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(supervisor)
        # Module-level failures preserve exit2 and never render exception input/context.
        boundary = subprocess.run(
            [
                sys.executable,
                str(control.SUPERVISOR),
                "PRIVATE_PARENT",
                "PRIVATE_START",
                sys.executable,
                "-c",
                "pass",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5,
        )
        assert boundary.returncode == 2 and boundary.stdout == boundary.stderr == b""
        producer = {
            "counts": dict.fromkeys(supervisor.REFUSAL_KEYS, 0),
            "counts_saturated": False,
            "first_scan_refusal": None,
        }
        with monkeypatch.context() as context:
            context.setattr(supervisor, "process", lambda pid: (101, "S"))
            context.setattr(supervisor.os, "listdir", lambda path: [str(os.getpid())])

            def refused_open(*args, **kwargs):
                raise FileNotFoundError(errno.ENOENT, "PRIVATE_ERROR", "PRIVATE_PATH")

            context.setattr(builtins, "open", refused_open)
            children, truncated = supervisor.direct_children(producer)
        assert children == set() and truncated is True
        assert producer["counts"]["scan_refused"] == 1
        assert producer.get("first_scan_refusal") == {"s": 1, "k": 1, "o": 2, "b": 7}
        events = [
            {"event": "outcome", "code": 0},
            {"event": "settled", "complete": False},
            {"event": "diagnostic", "reason": "latched_refusal", **producer},
        ]
        projected = control.lifecycle_observation(events, 2, False, False, False)
        assert projected["first_scan_refusal"] == producer["first_scan_refusal"]
        assert projected["supervisor_exit"] == 2 and projected["settled_complete"] is False
        assert "PRIVATE" not in json.dumps(projected)
        # Only one well-formed closed tuple may be exported; values never escape.
        malformed = [
            None,
            "PRIVATE_VALUE",
            {},
            {**producer["first_scan_refusal"], "path": "PRIVATE_PATH"},
        ]
        for key, value in (
            ("s", True),
            ("s", 0),
            ("s", 4),
            ("s", "PRIVATE_SITE"),
            ("k", 0),
            ("k", 7),
            ("o", 0),
            ("o", 5),
            ("b", -1),
            ("b", 32),
            ("b", 4),
            ("b", 8),
            ("b", 15),
            ("b", 31),
        ):
            malformed.append({**producer["first_scan_refusal"], key: value})
        for value in malformed:
            invalid = copy.deepcopy(events)
            invalid[-1]["first_scan_refusal"] = value
            closed = control.lifecycle_observation(invalid, 2, False, False, False)
            assert closed["first_scan_refusal"] is None
            assert closed["supervisor_exit"] == 2 and closed["settled_complete"] is False
            assert "PRIVATE" not in json.dumps(closed)
        for count in (0, True, -1, 65536, "PRIVATE_COUNT"):
            invalid = copy.deepcopy(events)
            invalid[-1]["counts"]["scan_refused"] = count
            assert (
                control.lifecycle_observation(invalid, 2, False, False, False)["first_scan_refusal"]
                is None
            )
        for invalid in (events[:-1], [*events, copy.deepcopy(events[-1])]):
            assert (
                control.lifecycle_observation(invalid, 2, False, False, False)["first_scan_refusal"]
                is None
            )

        # Diagnostic-only task metadata retains refusal and never exports identities.
        for stage, operation in (
            ("child-open", 2),
            ("child-context-enter", 2),
            ("child-read", 3),
            ("child-parse", 4),
            ("child-context-exit", 2),
        ):
            for before, after in (
                (((123, "R"), "known"), (None, "absent")),
                (((123, "R"), "known"), ((123, "R"), "known")),
                (((123, "R"), "known"), ((456, "R"), "known")),
                (((123, "R"), "known"), ((123, "Z"), "known")),
                ((None, "unknown"), (None, "denied")),
                ((None, "invalid"), (None, "other")),
            ):
                first, first_category = before
                last, last_category = after
                task = {
                    "phase": stage,
                    "before_probe": first_category,
                    "after_probe": last_category,
                    "before_known": first is not None,
                    "after_known": last is not None,
                    "same_task_start": first[0] == last[0] if first and last else None,
                    "before_dead": first[1] in ("Z", "X") if first else None,
                    "after_dead": last[1] in ("Z", "X") if last else None,
                    "classification": "task-entry-absent"
                    if last_category == "absent"
                    else "task-identity-changed"
                    if first and last and first[0] != last[0]
                    else "task-known"
                    if last
                    else "task-probe-unknown",
                }
                diagnostic = {
                    "counts": {"scan_refused": 1},
                    "first_scan_refusal": {"s": 2, "k": 1, "o": operation, "b": 7},
                    "first_task_scan": task,
                }
                assert control.task_scan_observation(diagnostic) == task
                assert "123" not in json.dumps(task) and "456" not in json.dumps(task)
                for key, value in (
                    ("phase", []),
                    ("before_probe", {}),
                    ("before_known", 1),
                    ("same_task_start", 1),
                    ("classification", "PRIVATE"),
                    ("path", "PRIVATE"),
                ):
                    bad = copy.deepcopy(diagnostic)
                    bad["first_task_scan"][key] = value
                    assert control.task_scan_observation(bad) is None
        assert control.task_scan_observation({}) is None
        assert projected["first_task_scan"] is None

        # Recursive foreign-task census/rescan is retired. Direct ownership,
        # bounded discovery and all-child wait survivors execute in the focused
        # kernel-domain node and software protocol controls below.
        timeout_checker = tmp_path / "owned-timeout-checker.py"
        timeout_checker.write_text(
            "import importlib.util,pathlib,os,sys,subprocess,json\nspec=importlib.util.spec_from_file_location('_checker_control',os.environ['CONTROL_SOURCE']);control=importlib.util.module_from_spec(spec);spec.loader.exec_module(control)\ncontrol.SUPERVISOR=pathlib.Path(os.environ['INNER_SUPERVISOR'])\nsentinel=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)\nrow={'stage':'timeout-check','assertion_RED':False,'sentinel_preserved':False}\ntry:\n    code='import os,signal,time; p=os.fork(); os.setsid() if p==0 else None; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)'\n    result=control.run_owned([sys.executable,'-c',code],os.environ.copy(),0.2)\n    row.update(timed_out=result['timed_out'],cleanup_complete=result['cleanup_complete'],kernel_children_closed=result['observation']['kernel_children_closed'],settled_complete=result['observation']['settled_complete'])\n    row['sentinel_preserved']=sentinel.poll() is None\n    try:\n        assert result['timed_out'] is True\n        assert result['cleanup_complete'] is True\n        assert result['observation']['kernel_children_closed'] is True\n        assert result['observation']['settled_complete'] is True\n        assert sentinel.poll() is None\n    except AssertionError:\n        row['assertion_RED']=True\n        raise\nfinally:\n    sentinel.terminate();sentinel.wait(timeout=5)\n    row['sentinel_reaped']=sentinel.returncode is not None\n    fd=os.open(os.environ['TIMEOUT_CLOSED'],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)\n    with os.fdopen(fd,'w') as stream:json.dump(row,stream)\n"
        )
        timeout_omission = tmp_path / "omitted-cleanup-supervisor.py"
        supervisor_bytes = control.SUPERVISOR.read_text()
        assert supervisor_bytes.count("os.kill(pid, signum)") == 1
        timeout_omission.write_text(
            supervisor_bytes.replace(
                "os.kill(pid, signum)", "os.kill(pid, signum) if pid == child.pid else None"
            )
        )
        for timeout_name, timeout_supervisor in (
            ("positive", control.SUPERVISOR),
            ("inverse", timeout_omission),
            ("restored", control.SUPERVISOR),
        ):
            timeout_closed = tmp_path / (timeout_name + "-closed.json")
            timeout_result = control.run_owned(
                [sys.executable, str(timeout_checker)],
                {
                    **os.environ,
                    "CONTROL_SOURCE": str(Path(control.__file__)),
                    "INNER_SUPERVISOR": str(timeout_supervisor),
                    "TIMEOUT_CLOSED": str(timeout_closed),
                },
                10,
            )
            assert timeout_result["cleanup_complete"] is True, timeout_result["observation"]
            assert timeout_result["timed_out"] is False and timeout_result["cancelled"] is False
            timeout_facts = json.loads(timeout_closed.read_text())
            assert timeout_facts["sentinel_reaped"] is True
            assert timeout_facts["sentinel_preserved"] is True
            if timeout_name == "inverse":
                assert timeout_result["exit"] == 1
                assert timeout_facts["assertion_RED"] is True
                assert timeout_facts["cleanup_complete"] is False
            else:
                assert timeout_result["exit"] == 0
                assert timeout_facts["assertion_RED"] is False
                assert timeout_facts["cleanup_complete"] is True
        kernel_fixture = tmp_path / "owned-retirement-kernel.py"
        kernel_fixture.write_text(_owned_retirement_kernel_fixture())
        import ast
        import hashlib
        import stat

        frozen_fixture = kernel_fixture.read_bytes()
        frozen_digest = hashlib.sha256(frozen_fixture).hexdigest()
        syntax = ast.parse(frozen_fixture)
        allowed = sorted(
            {
                node.lineno
                for node in ast.walk(syntax)
                if isinstance(node, (ast.Assert, ast.Call, ast.Raise))
            }
        )
        diagnostic_nodes = [
            node
            for node in syntax.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"fault", "diagnostic_canary", "cardinality", "await_retirement"}
        ]
        diagnostic_namespace = {
            "HERE": kernel_fixture,
            "ALLOWED_LINES": frozenset(allowed),
            "STAGE": "canary",
            "STAGES": frozenset({"canary"}),
        }
        exec(
            compile(
                ast.Module(body=diagnostic_nodes, type_ignores=[]), str(kernel_fixture), "exec"
            ),
            diagnostic_namespace,
        )
        try:
            diagnostic_namespace["diagnostic_canary"]()
        except AssertionError as canary:
            first = diagnostic_namespace["fault"](canary)
        expected_line = next(
            node.lineno
            for node in ast.walk(syntax)
            if isinstance(node, ast.Assert)
            and isinstance(node.msg, ast.Constant)
            and node.msg.value == "PRIVATE_CANARY_MESSAGE"
        )
        assert first == {"class": "AssertionError", "line": expected_line, "stage": "canary"}
        assert "PRIVATE" not in json.dumps(first)
        assert diagnostic_namespace["fault"](None)["line"] is None
        try:
            raise ValueError("PRIVATE_UNMATCHED")
        except ValueError as unmatched:
            assert diagnostic_namespace["fault"](unmatched)["line"] is None
        assert diagnostic_namespace["cardinality"]([]) == "0"
        assert diagnostic_namespace["cardinality"]([1]) == "1"
        assert diagnostic_namespace["cardinality"]([1, 2]) == "many"
        assert hashlib.sha256(kernel_fixture.read_bytes()).hexdigest() == frozen_digest
        wait_retired = diagnostic_namespace["await_retirement"]

        def controlled_wait(function, mode):
            state = {"absent": False, "tick": 0.0}

            def owner_probe():
                return (
                    None
                    if mode == "owner-dead"
                    else (
                        102 if mode == "owner-reuse" else 101,
                        "Z" if mode == "owner-zombie" else "S",
                    )
                )

            def task_probe():
                if state["absent"]:
                    return None, "absent"
                return (
                    ((203 if mode == "task-reuse" else 202, "S"), "known")
                    if mode not in {"denied", "invalid", "unknown", "other"}
                    else (None, mode)
                )

            def clock():
                return state["tick"]

            def pause(seconds):
                state["tick"] += seconds
                if mode == "positive":
                    state["absent"] = True

            function(owner_probe, task_probe, 101, 202, clock, pause)
            assert task_probe() == (None, "absent"), "actual retirement required"

        controlled_wait(wait_retired, "positive")
        for refused_mode in (
            "deadline",
            "task-reuse",
            "owner-reuse",
            "owner-dead",
            "owner-zombie",
            "denied",
            "invalid",
            "unknown",
            "other",
        ):
            with pytest.raises(AssertionError):
                controlled_wait(wait_retired, refused_mode)
        waiting_node = next(node for node in diagnostic_nodes if node.name == "await_retirement")
        omission_tree = copy.deepcopy(waiting_node)
        for child in ast.walk(omission_tree):
            if isinstance(child, ast.For):
                child.body[-1] = ast.Return(value=None)
        omitted_namespace = {}
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[omission_tree], type_ignores=[])),
                str(kernel_fixture),
                "exec",
            ),
            omitted_namespace,
        )
        with pytest.raises(AssertionError, match="actual retirement required"):
            controlled_wait(omitted_namespace["await_retirement"], "positive")
        controlled_wait(wait_retired, "positive")
        kernel_projection = tmp_path / "owned-kernel-closed.json"
        kernel_result = control.run_owned(
            [sys.executable, str(kernel_fixture), "run"],
            {
                **os.environ,
                "OWNED_SUPERVISOR": str(control.SUPERVISOR),
                "KERNEL_PROJECTION": str(kernel_projection),
                "KERNEL_ALLOWED_LINES": json.dumps(allowed),
            },
            10,
        )
        assert kernel_result["cleanup_complete"] is True, kernel_result["observation"]
        assert stat.S_IMODE(kernel_projection.stat().st_mode) == 0o600
        raw_projection = kernel_projection.read_bytes()
        assert len(raw_projection) <= 16384
        kernel_closed = json.loads(raw_projection)
        assert set(kernel_closed) == {
            "schema",
            "stage",
            "exception_class",
            "first_fault",
            "cleanup_fault",
            "cardinality",
            "events",
            "result",
        }
        assert kernel_closed["schema"] == "owned-kernel-closed.v1"
        for closed_fault in (kernel_closed["first_fault"], kernel_closed["cleanup_fault"]):
            if closed_fault is not None:
                assert set(closed_fault) == {"class", "line", "stage"}
                assert closed_fault["line"] is None or (
                    type(closed_fault["line"]) is int and closed_fault["line"] in allowed
                )
                assert closed_fault["stage"] is None or closed_fault["stage"] in {
                    "root-start",
                    "before-wait",
                    "after-wait",
                    "before-cardinality",
                    "after-cardinality",
                    "cleanup",
                    "kernel-finished",
                    "canary",
                }

        assert kernel_result["exit"] == 0, kernel_closed
        assert kernel_result["timed_out"] is False
        assert kernel_result["cancelled"] is False
        assert kernel_closed["exception_class"] is None, kernel_closed
        kernel_events = kernel_closed["events"]
        assert kernel_events[-1]["all_owned_groups_absent"] is True
        assert kernel_events[-1]["real_ECHILD"] is True
        kernel_case = kernel_closed["result"]["cases"][0]
        assert kernel_case["kernel_domain_basis"] == control.SETTLEMENT_BASIS
        assert kernel_case["foreign_task_scans"] == 0
        assert kernel_case["settled_complete"] is True
        assert kernel_case["sentinel_preserved"] is True

        # An absolute script path does not choose its editable-repair target.
        controller = tmp_path / "controller"
        controller.mkdir()
        original = controller / "original"
        original.write_text("unchanged")
        workload = tmp_path / "workload"
        (workload / "scripts").mkdir(parents=True)
        prepare = workload / "scripts/ci_environment.py"
        prepare.write_text(
            "import pathlib,sys\n"
            "assert sys.argv[1:] == ['prepare']\n"
            "(pathlib.Path.cwd()/'prepared').write_text('software-only')\n"
        )
        with monkeypatch.context() as context:
            context.chdir(controller)
            prepared = control.run_owned(
                [sys.executable, str(prepare), "prepare"],
                os.environ.copy(),
                5,
                cwd=workload,
            )
        assert prepared["exit"] == 0 and prepared["cleanup_complete"], prepared["observation"]
        assert (workload / "prepared").read_text() == "software-only"
        assert sorted(p.name for p in controller.iterdir()) == ["original"]
        assert original.read_text() == "unchanged" and sentinel.poll() is None
        # Exercise the real unchanged installer through the producer boundary.
        # Only curl is a software stand-in; no network or installed shared files.
        binaries = tmp_path / "bin"
        binaries.mkdir()
        curl = binaries / "curl"
        curl.write_text(
            "#!/bin/sh\nprintf '%s\\n' 'mkdir -p \"$HOME/.local/bin\"' 'touch \"$HOME/.local/bin/uv\"'\n"
        )
        curl.chmod(0o755)
        home = tmp_path / "home"
        home.mkdir()
        environment = {
            **os.environ,
            "HOME": str(home),
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        }
        installed = control.prepare_uv(
            Path(__file__).resolve().parents[2], tmp_path, environment, 10
        )
        assert (home / ".local/bin/uv").is_file()
        assert (tmp_path / "installer-path").read_text() == str(home / ".local/bin") + "\n"
        assert installed["PATH"].split(os.pathsep)[0] == str(home / ".local/bin")
        with pytest.raises(ValueError, match="uv-install"):
            control.prepare_uv(tmp_path / "missing-source", tmp_path, environment, 5)
        assert sentinel.poll() is None
        assert escaped.exists(), "negative child actually reached"
    finally:
        sentinel.kill()
        sentinel.wait(timeout=5)


def test_exception_observation_discards_values_and_never_infers_missing_old_await(
    tmp_path, monkeypatch
):
    issuer = type("Issuer", (), {"_reports": {}})()
    inboxes = [uuid.uuid4() for _ in range(3)]
    namespace = {"issuer": issuer, "inboxes": inboxes}
    exec(
        compile(
            "def test_capacity_one_registered_owner_capture_and_concurrent_sources(issuer,inboxes):\n"
            ' raise TimeoutError("PRIVATE_ERROR")\n',
            str(tmp_path / control.NODE.split("::")[0]),
            "exec",
        ),
        namespace,
    )
    try:
        namespace["test_capacity_one_registered_owner_capture_and_concurrent_sources"](
            issuer, inboxes
        )
    except TimeoutError as error:
        facts, refs = observer.exception_facts(error, tmp_path)
    assert facts["exception"] == "TimeoutError" and facts["nested_acquire"] is False
    assert "PRIVATE" not in json.dumps(facts)
    assert refs is not None and refs[0] is issuer and refs[1] == inboxes
    # Reach the real observer's exception-chain branch using software-only frames.
    import asyncio

    namespace["asyncio"] = asyncio
    exec(
        compile(
            "def _acquire():\n raise asyncio.CancelledError()\n",
            str(tmp_path / "asyncpg/pool.py"),
            "exec",
        ),
        namespace,
    )
    exec(
        compile(
            "def capture_accepted_report():\n _acquire()\n", str(tmp_path / control.RUNTIME), "exec"
        ),
        namespace,
    )
    exec(
        compile(
            "def test_capacity_one_registered_owner_capture_and_concurrent_sources(issuer,inboxes):\n"
            " try:capture_accepted_report()\n"
            " except asyncio.CancelledError as error:raise TimeoutError() from error\n",
            str(tmp_path / control.NODE.split("::")[0]),
            "exec",
        ),
        namespace,
    )
    try:
        namespace["test_capacity_one_registered_owner_capture_and_concurrent_sources"](
            issuer, inboxes
        )
    except TimeoutError as error:
        facts, refs = observer.exception_facts(error, tmp_path)
    assert facts["nested_acquire"] and facts["owner_frame"] and refs[0] is issuer
    facts, refs = observer.exception_facts(ValueError("PRIVATE_DATA"), tmp_path)
    assert facts["exception"] == "other" and refs is None

    # Exercise the existing observer-fixture SQL boundary with software-only pool
    # probes. These controls do not execute or claim a PostgreSQL/MCP witness.
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import asyncpg

    from butlers.core import fact_authority

    observed_ids = [uuid.uuid4() for _ in range(3)]
    reads = []
    report_rows = [{"id": key, "report": None} for key in observed_ids]

    class ProbePool(asyncpg.Pool):
        def __init__(self):
            pass

        def get_max_size(self):
            return 1

        async def fetchval(self, query):
            assert query == "SELECT current_user"
            return "butler_switchboard_rw"

        @asynccontextmanager
        async def acquire(self, **kwargs):
            class Connection:
                def is_in_transaction(self):
                    return False

                async def fetchval(self, query):
                    assert query == "SELECT 1"
                    return 1

            yield Connection()

    class Reports:
        async def fetch(self, query, *args):
            assert "WHERE id = ANY($1::uuid[])" in query, "unscoped inbox read"
            assert len(args) == 1 and set(args[0]) == set(observed_ids)
            reads.append(tuple(args[0]))
            return list(report_rows)

    probe_pool = ProbePool()
    registry = fact_authority.FactSourceContextRegistry(probe_pool)
    runtime = Path(fact_authority.__file__).resolve()
    env = SimpleNamespace(sw=Reports(), _registered_endpoint="http://127.0.0.1:1")
    request = SimpleNamespace(node=SimpleNamespace(nodeid=control.NODE))
    config = {
        "phase": "before",
        "root": str(runtime.parents[3]),
        "runtime_sha256": __import__("hashlib").sha256(runtime.read_bytes()).hexdigest(),
    }

    async def observe(refs):
        monkeypatch.setattr(observer, "CONFIG", config)
        state = phase(config["phase"])
        state.update(durable_partial_reports=None, memory_partial_reports=None)
        monkeypatch.setattr(observer, "STATE", state)
        monkeypatch.setattr(observer, "REFERENCES", refs)
        fixture = observer.observe_registered_context.__wrapped__(request, env, probe_pool)
        await anext(fixture)
        with pytest.raises(StopAsyncIteration):
            await anext(fixture)

    asyncio.run(observe((registry, observed_ids)))
    assert len(reads) == 1 and observer.STATE["durable_partial_reports"] == 0
    assert observer.STATE["memory_partial_reports"] == 0
    reads.clear()
    for missing in (None, (registry, observed_ids[:2]), (registry, observed_ids[:1] * 3)):
        asyncio.run(observe(missing))
        assert reads == []
        assert observer.STATE["durable_partial_reports"] is None
        assert observer.STATE["memory_partial_reports"] is None
    report_rows.pop()
    asyncio.run(observe((registry, observed_ids)))
    assert observer.STATE["durable_partial_reports"] is None
    assert observer.STATE["memory_partial_reports"] is None
    report_rows[:] = [{"id": key, "report": {}} for key in observed_ids]
    config["phase"] = "after"
    asyncio.run(observe((registry, observed_ids)))
    assert observer.STATE["durable_partial_reports"] == 0
    assert observer.STATE["memory_partial_reports"] is None

    # The narrow call observer retains only this node's references on success or
    # failure and restores tracing. Foreign frames and an existing tracer stay opaque.
    monkeypatch.setattr(observer, "CONFIG", {"root": str(tmp_path)})
    target = "test_capacity_one_registered_owner_capture_and_concurrent_sources"
    for filename, outcome in (
        (control.NODE.split("::")[0], "passed"),
        (control.NODE.split("::")[0], "failed"),
        ("unrelated.py", "passed"),
    ):
        namespace = {}
        exec(
            compile(
                f"def {target}(issuer, inboxes):\n"
                + (" raise ValueError('PRIVATE_BODY')\n" if outcome == "failed" else " return 1\n"),
                str(tmp_path / filename),
                "exec",
            ),
            namespace,
        )
        previous_trace = sys.gettrace()
        sys.settrace(None)
        hook = observer.pytest_runtest_call(SimpleNamespace(nodeid=control.NODE))
        try:
            next(hook)
            if outcome == "failed":
                with pytest.raises(ValueError, match="PRIVATE_BODY"):
                    namespace[target](registry, observed_ids)
                with pytest.raises(ValueError, match="PRIVATE_BODY"):
                    hook.throw(ValueError("PRIVATE_BODY"))
            else:
                assert namespace[target](registry, observed_ids) == 1
                with pytest.raises(StopIteration):
                    next(hook)
            assert sys.gettrace() is None
            if filename == "unrelated.py":
                assert observer.REFERENCES is None
            else:
                assert observer.REFERENCES == (registry, observed_ids)
        finally:
            hook.close()
            sys.settrace(previous_trace)

    def ambient_trace(frame, event, arg):
        return None

    previous_trace = sys.gettrace()
    sys.settrace(ambient_trace)
    try:
        hook = observer.pytest_runtest_call(SimpleNamespace(nodeid=control.NODE))
        next(hook)
        assert sys.gettrace() is ambient_trace
        with pytest.raises(StopIteration):
            next(hook)
        assert sys.gettrace() is ambient_trace and observer.REFERENCES is None
    finally:
        sys.settrace(previous_trace)


def test_real_pytest_observer_records_setup_refusal_without_claiming_pg(tmp_path):
    plugin = Path(observer.__file__).resolve()
    (tmp_path / "test_refusal.py").write_text('def test_refusal():\n assert False,"PRIVATE_BODY"\n')
    config = {
        "binding": {"nonce": "a" * 64},
        "phase": "before",
        "root": str(tmp_path),
        "output": str(tmp_path / "phase.json"),
    }
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(config))
    env = {
        **os.environ,
        "FACT_CAPTURE_CONTROL_CONFIG": str(cfg),
        "PYTHONPATH": str(plugin.parent),
        "PYTEST_ADDOPTS": "",
    }
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            plugin.stem,
            "-q",
            "-o",
            "addopts=",
            "test_refusal.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert run.returncode != 0
    data = json.loads((tmp_path / "phase.json").read_text())
    assert data["phases"].get("setup") != "passed" or data["collected"] != 1
    assert data["registered_mcp"] is False
    assert "PRIVATE" not in json.dumps(data)


def test_terminal_cancellation_revokes_witness_after_owned_cleanup(tmp_path, monkeypatch, capsys):
    """Actual signals isolate terminal admission; no child, PG, install or network."""
    import builtins
    import signal

    real_atomic = control.atomic
    real_witness = control.require_witness
    real_temporary = control.tempfile.TemporaryDirectory
    real_print = builtins.print
    binding = {"nonce": "a" * 64}
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    for seam in (
        "positive",
        "sentinel-wait",
        "summary-validation",
        "private-cleanup",
        "summary-write-before",
        "summary-write-after",
        "success-announcement",
        "after-main-return",
        "supervisor-phase",
        "supervisor-terminal-latch",
    ):
        parent = tmp_path / seam
        root = parent / "controller"
        (root / "scripts").mkdir(parents=True)
        (root / "scripts/fact_source_capture_before_observer.py").write_text("# stand-in\n")

        def cancel_at(location, seam=seam):
            if seam == location:
                os.kill(os.getpid(), signal.SIGTERM)

        class Sentinel:
            def poll(self):
                return None

            def kill(self):
                pass

            def wait(self, timeout):
                cancel_at("sentinel-wait")
                return 0

        class Temporary(real_temporary):
            def __exit__(self, *args):
                result = super().__exit__(*args)
                cancel_at("private-cleanup")
                return result

        def own(command, environment, seconds, *, seam=seam, **kwargs):
            code = 0
            if command[-1] == "--phase-child":
                config = json.loads(Path(environment["FACT_CAPTURE_CONTROL_CONFIG"]).read_text())
                data = phase(config["phase"])
                data["binding"] = config["binding"]
                Path(config["output"]).write_text(json.dumps(data))
                code = data["pytest_exit"]
            return {
                "exit": code,
                "cleanup_complete": True,
                "timed_out": False,
                "cancelled": seam == "supervisor-phase" and command[-1] == "--phase-child",
                "observation": {"completion_basis": control.SETTLEMENT_BASIS},
            }

        def witness(*args, seam=seam):
            cancel_at("summary-validation")
            result = real_witness(*args)
            if seam == "supervisor-terminal-latch":
                control.CANCELLED.append("supervisor")
            return result

        def atomic(path, value):
            if path.name == "summary.json":
                cancel_at("summary-write-before")
            real_atomic(path, value)
            if path.name == "summary.json":
                cancel_at("summary-write-after")

        def announce(*args, **kwargs):
            if args and str(args[0]).startswith("fact-capture control WITNESS_COMPLETE"):
                cancel_at("success-announcement")
            return real_print(*args, **kwargs)

        try:
            with monkeypatch.context() as context:
                context.setattr(control, "CANCELLED", [])
                context.setattr(control, "AGGREGATE_DEADLINE", None)
                context.setattr(control, "__file__", str(root / "scripts/control.py"))
                context.setattr(control, "provider_binding", lambda root: dict(binding))
                context.setattr(control, "git", lambda *args, **kwargs: "")
                context.setattr(
                    control, "workload_state", lambda *args: {"runtime_sha256": "b" * 64}
                )
                context.setattr(control, "overlay", lambda *args: None)
                context.setattr(control, "prepare_uv", lambda *args, **kwargs: {})
                context.setattr(control, "run_owned", own)
                context.setattr(control, "require_witness", witness)
                context.setattr(control, "atomic", atomic)
                context.setattr(control.tempfile, "TemporaryDirectory", Temporary)
                context.setattr(
                    control.subprocess,
                    "run",
                    lambda *args, **kwargs: subprocess.CompletedProcess([], 0),
                )
                context.setattr(control.subprocess, "Popen", lambda *args, **kwargs: Sentinel())
                context.setattr(builtins, "print", announce)
                context.setattr(sys, "argv", ["control"])
                context.setenv("RUNNER_TEMP", str(parent))
                context.setenv("FACT_CAPTURE_CONTROL_STARTED", str(control.time.monotonic()))
                result = control.main()
                evidence = parent / "fact-capture-before-evidence"
                summary = evidence / "summary.json"
                refusal = evidence / "control-refusal.json"
                output = capsys.readouterr().out
                if seam in ("positive", "after-main-return"):
                    assert result == 0 and not control.CANCELLED
                    assert json.loads(summary.read_text())["control_verdict"] == "WITNESS_COMPLETE"
                    assert not refusal.exists()
                    if seam == "after-main-return":
                        # The handler stays armed until process exit: a late signal
                        # revokes the committed file and interrupts the CLI caller.
                        with pytest.raises(ValueError, match="control-cancelled"):
                            cancel_at("after-main-return")
                        assert control.CANCELLED and not summary.exists()
                else:
                    assert result == 2, seam
                    assert control.CANCELLED or seam == "supervisor-phase"
                    assert not summary.exists() and not summary.with_suffix(".pending").exists()
                    data = json.loads(refusal.read_text())
                    assert data["control_verdict"] == "UNKNOWN" and data["complete"] is False
                    assert data["reason"] == "control-cancelled"
                    assert "WITNESS_COMPLETE" not in output
                assert not list(parent.glob("fact-pg-witness-*"))
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def test_cli_rejects_unbound_environment_before_any_execution(tmp_path, monkeypatch, capsys):
    env = {
        "PATH": os.environ["PATH"],
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_REPOSITORY": "other/repository",
        "PRIVATE_VALUE": "never-export",
    }
    run = subprocess.run(
        [sys.executable, str(Path(control.__file__).resolve())],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert run.returncode != 0
    assert b"never-export" not in run.stdout + run.stderr
    assert not list(tmp_path.glob("**/.venv"))

    # A provider-bound refusal must remain diagnostic evidence, never a PG witness.
    binding = {
        "run": "123",
        "attempt": "1",
        "controller_head": "a" * 40,
        "controller_tree": "b" * 40,
        "nonce": "c" * 64,
        "repository": "tzeusy-org/butlers",
        "workflow": "Fact Source Capture BEFORE Control",
        "python": "3.12.15",
    }
    real_binding = control.provider_binding
    monkeypatch.setattr(control, "provider_binding", lambda root: copy.deepcopy(binding))
    monkeypatch.setattr(control.sys, "argv", ["control"])
    monkeypatch.setattr(control.signal, "signal", lambda *args: None)
    real_execute = control.execute
    cases = [
        ("workload-clone", subprocess.TimeoutExpired("PRIVATE_COMMAND", 30), "subprocess-timeout"),
        ("uv-install", ValueError("uv-install"), "value-error"),
        ("environment-prepare", OSError("PRIVATE_PATH"), "os-error"),
        ("aggregate-budget", KeyError("PRIVATE_ENV"), "missing-key"),
        ("PRIVATE_STAGE", ValueError("PRIVATE_PAYLOAD"), "value-error"),
    ]

    def refuse(root, output, progress=None):
        if progress is not None:
            progress["stage"] = stage
        raise error

    for index, (stage, _error, category) in enumerate(cases):
        error = _error
        directory = tmp_path / str(index)
        directory.mkdir()
        monkeypatch.setenv("RUNNER_TEMP", str(directory))

        monkeypatch.setattr(control, "execute", refuse)
        assert control.main() == 2
        evidence = directory / "fact-capture-before-evidence"
        data = json.loads((evidence / "control-refusal.json").read_text())
        assert data["schema"] == "fact-capture-control-refusal.v1"
        assert data["stage"] == (stage if stage != "PRIVATE_STAGE" else "unknown")
        assert data["error_class"] == category
        assert data["binding"] == binding
        assert data["complete"] is False and data["control_verdict"] == "UNKNOWN"
        assert data["pg_call"] == data["cleanup"] == "UNKNOWN"
        assert not (evidence / "summary.json").exists()
        with pytest.raises(ValueError):
            control.safe_phase(data, "before", binding)
        assert "PRIVATE" not in json.dumps(data) + capsys.readouterr().out

    # Execute the real prephase sequence with closed software dependencies. Neither
    # installer failure nor frozen-environment failure may reach the PG child.
    def install(*args, **kwargs):
        calls.append("installer")
        if stage == "uv-install":
            raise ValueError("uv-install")
        return {}

    def prepare(command, environment, seconds, *, cwd):
        calls.append("prepare")
        assert cwd.name == "workload" and command[-1] == "--prepare-child"
        assert Path(environment["FACT_CAPTURE_PREPARE_CONFIG"]).parent == cwd.parent
        return {
            "exit": 0 if label == "environment-cancelled" else 2,
            "cleanup_complete": True,
            "timed_out": False,
            "cancelled": label == "environment-cancelled",
            "observation": control.lifecycle_observation(
                [], 0, False, label == "environment-cancelled", False
            ),
        }

    for label, stage in (
        ("uv-install", "uv-install"),
        ("environment-prepare", "environment-prepare"),
        ("environment-cancelled", "environment-prepare"),
    ):
        directory = tmp_path / ("real-" + label)
        directory.mkdir()
        root = directory / "controller"
        (root / "scripts").mkdir(parents=True)
        (root / "scripts/fact_source_capture_before_observer.py").write_text("# software fixture")
        progress = {}
        calls = []
        with monkeypatch.context() as context:
            context.setenv("FACT_CAPTURE_CONTROL_STARTED", str(control.time.monotonic()))
            context.setattr(control, "git", lambda *args, **kwargs: "")
            context.setattr(control, "workload_state", lambda *args: {})
            context.setattr(control, "overlay", lambda *args: None)
            context.setattr(
                control.subprocess,
                "run",
                lambda *args, **kwargs: subprocess.CompletedProcess([], 0),
            )

            context.setattr(control, "prepare_uv", install)
            context.setattr(control, "run_owned", prepare)
            # Any attempt to start the sentinel/PG child fails the software control.
            context.setattr(
                control.subprocess,
                "Popen",
                lambda *args, **kwargs: pytest.fail("refused prephase must not start a PG child"),
            )
            with pytest.raises(ValueError, match=stage):
                # Preserve the real execute, replaced only in the main() cases above.
                real_execute(root, directory, progress)
        assert progress["stage"] == stage
        assert calls == (["installer"] if stage == "uv-install" else ["installer", "prepare"])

        # A refusal after execute starts must retain its execution nonce and source
        # bindings, rather than main's earlier provider-only preflight binding.
        execution_binding = {**binding, "nonce": "d" * 64}
        bindings = iter((copy.deepcopy(binding), copy.deepcopy(execution_binding)))
        with monkeypatch.context() as context:
            context.setenv("RUNNER_TEMP", str(directory))
            context.setenv("FACT_CAPTURE_CONTROL_STARTED", str(control.time.monotonic()))
            context.setattr(control, "__file__", str(root / "scripts/control.py"))
            context.setattr(control, "execute", real_execute)
            context.setattr(
                control, "provider_binding", lambda root, bindings=bindings: next(bindings)
            )
            context.setattr(control, "git", lambda *args, **kwargs: "")
            context.setattr(
                control,
                "workload_state",
                lambda root, phase: {"phase": phase, "runtime_blob": "e" * 40},
            )
            context.setattr(control, "overlay", lambda *args: None)
            context.setattr(
                control.subprocess,
                "run",
                lambda *args, **kwargs: subprocess.CompletedProcess([], 0),
            )
            context.setattr(control, "prepare_uv", install)
            context.setattr(control, "run_owned", prepare)
            context.setattr(
                control.subprocess,
                "Popen",
                lambda *args, **kwargs: pytest.fail("refused prephase must not start a PG child"),
            )
            assert control.main() == 2
        evidence = directory / "fact-capture-before-evidence"
        data = json.loads((evidence / "control-refusal.json").read_text())
        assert data["binding"]["nonce"] == execution_binding["nonce"]
        assert data["binding"]["workload"] == {"phase": "after", "runtime_blob": "e" * 40}
        assert data["binding"]["phase_states"] == {
            name: {"phase": name, "runtime_blob": "e" * 40} for name in ("before", "after")
        }
        assert data["stage"] == stage and data["complete"] is False
        assert data["pg_call"] == data["cleanup"] == "UNKNOWN"
        assert not (evidence / "summary.json").exists()
        assert "PRIVATE" not in json.dumps(data) + capsys.readouterr().out

    # Controller mismatches remain fail-closed, but expose only which predicate failed.
    with monkeypatch.context() as context:
        context.setenv("GITHUB_REPOSITORY", "tzeusy-org/butlers")
        context.setenv("GITHUB_EVENT_NAME", "pull_request")
        context.setenv("GITHUB_WORKFLOW", "Fact Source Capture BEFORE Control")
        context.setenv("GITHUB_RUN_ID", "123")
        context.setenv("GITHUB_RUN_ATTEMPT", "1")
        context.setenv("GITHUB_SHA", "a" * 40)
        context.setenv("GITHUB_WORKFLOW_SHA", "a" * 40)
        context.setenv(
            "GITHUB_WORKFLOW_REF",
            "tzeusy-org/butlers/.github/workflows/"
            "fact-source-capture-before-control.yml@refs/pull/1/merge",
        )
        context.setattr(control.platform, "python_version", lambda: "3.12.15")
        context.setattr(control, "git", lambda root, *args: "" if args[0] == "status" else "a" * 40)
        assert real_binding(tmp_path)["run"] == "123"
        for key, reason in (
            ("GITHUB_SHA", "controller-sha"),
            ("GITHUB_WORKFLOW_SHA", "controller-workflow-sha"),
            ("GITHUB_WORKFLOW_REF", "controller-workflow-ref"),
        ):
            with monkeypatch.context() as mismatch:
                mismatch.setenv(key, "PRIVATE_MISMATCH")
                with pytest.raises(ValueError, match=reason) as caught:
                    real_binding(tmp_path)
                data = control.refusal(caught.value, {"stage": "controller-identity"}, None)
                assert data["reason"] == reason and data["binding"] is None
                assert "PRIVATE" not in json.dumps(data)
        context.setattr(control.platform, "python_version", lambda: "wrong")
        with pytest.raises(ValueError, match="controller-python"):
            real_binding(tmp_path)
        context.setattr(control.platform, "python_version", lambda: "3.12.15")
        context.setattr(
            control, "git", lambda root, *args: "PRIVATE_PATH" if args[0] == "status" else "a" * 40
        )
        with pytest.raises(ValueError, match="controller-dirty"):
            real_binding(tmp_path)

    # A pre-existing output directory is never overwritten or adopted.
    directory = tmp_path / "existing"
    output = directory / "fact-capture-before-evidence"
    output.mkdir(parents=True)
    marker = output / "summary.json"
    marker.write_text("unchanged")
    monkeypatch.setenv("RUNNER_TEMP", str(directory))
    assert control.main() == 2
    assert marker.read_text() == "unchanged"
    assert not (output / "control-refusal.json").exists()

    # Execute the pinned helper unchanged, directly and through the observer.
    # Only uv is a software fixture: no install, network, provider or PG call.
    helper_bytes = (Path(control.__file__).parent / "ci_environment.py").read_bytes()
    assert control.hashlib.sha256(helper_bytes).hexdigest() == control.PREPARE_HELPER_SHA256
    uv_fixture = """#!/usr/bin/env python3
import os,pathlib,subprocess,sys
mode=os.environ['FIXTURE_MODE']
if sys.argv[1:]==['--version']:
 print('PRIVATE_VERSION_ERROR' if mode=='compatibility-refused' else 'uv software fixture')
 sys.exit(6 if mode=='compatibility-refused' else 0)
if sys.argv[1:]==['lock','--check']:
 print('PRIVATE_LOCK_OUTPUT',file=sys.stderr)
 sys.exit(7 if mode=='lock-refused' else 0)
assert sys.argv[1:]==['sync','--frozen','--dev','--compile-bytecode','--reinstall-package','butlers']
if mode=='sync-refused':
 print('PRIVATE_SYNC_OUTPUT',file=sys.stderr);sys.exit(8)
root=pathlib.Path.cwd()
subprocess.run([sys.executable,'-m','venv','--without-pip',str(root/'.venv')],check=True)
site=next((root/'.venv/lib').glob('python*/site-packages'))
target=root/'src' if mode!='own-source-refused' else root/'outside'
(site/'fixture.pth').write_text(str(target)+'\\n')
"""
    for mode, substage in (
        ("healthy", "metadata-write"),
        ("compatibility-refused", "uv-version"),
        ("lock-refused", "lock-check"),
        ("sync-refused", "frozen-sync"),
        ("own-source-refused", "own-source"),
    ):
        executions = []
        for observed in (False, True):
            parent = tmp_path / (mode + str(observed))
            workload = parent / "workload"
            helper = workload / "scripts/ci_environment.py"
            helper.parent.mkdir(parents=True)
            helper.write_bytes(helper_bytes)
            for name in ("uv.lock", "pyproject.toml"):
                (workload / name).write_text("software fixture\n")
            for source in ("src", "outside"):
                (workload / source / "butlers").mkdir(parents=True)
                (workload / source / "butlers/__init__.py").write_text("# fixture\n")
            binary = workload / "bin/uv"
            binary.parent.mkdir()
            binary.write_text(uv_fixture)
            binary.chmod(0o755)
            config = parent / "prepare-config.json"
            config.write_text(json.dumps({"root": str(workload)}))
            command = (
                [sys.executable, str(Path(control.__file__).resolve()), "--prepare-child"]
                if observed
                else [sys.executable, str(helper), "prepare"]
            )
            result = subprocess.run(
                command,
                cwd=workload,
                env={
                    **os.environ,
                    "FIXTURE_MODE": mode,
                    "FACT_CAPTURE_PREPARE_CONFIG": str(config),
                    "PATH": str(binary.parent) + os.pathsep + os.environ["PATH"],
                },
                capture_output=True,
                timeout=40,
            )
            metadata = workload / ".venv/ci-environment.json"
            executions.append(
                (
                    result.returncode,
                    result.stdout,
                    result.stderr,
                    metadata.read_bytes() if metadata.is_file() else None,
                )
            )
            assert helper.read_bytes() == helper_bytes
        assert executions[0] == executions[1]
        assert result.returncode == (0 if mode == "healthy" else 1)
        diagnostic_path = parent / "prepare-observation.json"
        data = control.preparation_observation(diagnostic_path)
        assert data["substage"] == substage
        assert data["error_class"] == ("none" if mode == "healthy" else "subprocess-error")
        assert "PRIVATE" not in diagnostic_path.read_text()
        for key, value in (
            ("substage", "PRIVATE_STAGE"),
            ("helper_exit", True),
            ("helper_sha256", "wrong"),
            ("error_class", "PRIVATE_ERROR"),
        ):
            bad = {**data, key: value}
            diagnostic_path.write_text(json.dumps(bad))
            assert control.preparation_observation(diagnostic_path) is None

    # Refuse before import: fixed-name preload, foreign/missing origin, and
    # timestamp-valid bytecode (including an active symlink/legacy location).
    import importlib.util
    import py_compile
    from types import ModuleType, SimpleNamespace

    ambient_helper = ModuleType("ci_environment")
    monkeypatch.setitem(sys.modules, "ci_environment", ambient_helper)

    for mode in ("preloaded", "wrong-origin", "missing-spec", "cache", "cache-symlink", "legacy"):
        parent = tmp_path / ("import-" + mode)
        helper = parent / "workload/scripts/ci_environment.py"
        helper.parent.mkdir(parents=True)
        helper.write_bytes(helper_bytes)
        config = parent / "prepare-config.json"
        config.write_text(json.dumps({"root": str(helper.parents[1])}))
        marker = parent / "executed"
        expected = "prepare-helper-cache"
        if mode in ("cache", "cache-symlink", "legacy"):
            active = Path(importlib.util.cache_from_source(str(helper)))
            active.parent.mkdir()
            source = parent / "cache-source.py"
            text = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\ndef main(): return 0\n"
            source.write_text(text + "#" * (len(helper_bytes) - len(text.encode())))
            stamp = helper.stat()
            os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            target = parent / "cache.pyc" if mode == "cache-symlink" else active
            if mode == "legacy":
                target = helper.with_suffix(".pyc")
            py_compile.compile(str(source), cfile=str(target), dfile=str(helper), doraise=True)
            if mode == "cache-symlink":
                active.symlink_to(target)
            # A fresh interpreter exercises actual Python's cache selection.
            result = subprocess.run(
                [sys.executable, str(Path(control.__file__).resolve()), "--prepare-child"],
                cwd=helper.parents[1],
                env={**os.environ, "FACT_CAPTURE_PREPARE_CONFIG": str(config)},
                capture_output=True,
                timeout=10,
            )
            assert not marker.exists()
            assert result.returncode != 0
            assert expected.encode() in result.stderr
        else:
            with monkeypatch.context() as context:
                context.setenv("FACT_CAPTURE_PREPARE_CONFIG", str(config))
                context.setattr(sys, "path", list(sys.path))
                context.setattr(sys, "argv", ["control", "--prepare-child"])
                if mode == "preloaded":
                    context.setitem(sys.modules, "ci_environment", ModuleType("ci_environment"))
                    expected = "prepare-helper-preloaded"
                else:
                    context.delitem(sys.modules, "ci_environment", raising=False)
                    context.setattr(
                        control.util,
                        "find_spec",
                        lambda name, mode=mode, marker=marker: (
                            None
                            if mode == "missing-spec"
                            else SimpleNamespace(
                                origin=str(marker), submodule_search_locations=None
                            )
                        ),
                    )
                    expected = "prepare-helper-origin"
                context.setattr(
                    control, "import_module", lambda name: pytest.fail("refused import executed")
                )
                with pytest.raises(ValueError, match=expected):
                    control.preparation_child()
                assert not marker.exists()
        assert sys.modules["ci_environment"] is ambient_helper

    # Exercise actual prepare_uv through real execute; malformed private values
    # cannot escape its diagnostic, and no failed installer may reach PG.
    for index, (code, complete, timed, cancelled, failed) in enumerate(
        [
            (2, True, False, False, True),
            (0, False, False, False, True),
            (0, True, True, False, True),
            (0, True, False, True, True),
            (-15, True, False, False, True),
            (0, True, False, False, False),
        ]
    ):
        parent = tmp_path / ("uv-diagnostic-" + str(index))
        root = parent / "controller"
        (root / "scripts").mkdir(parents=True)
        (root / "scripts/fact_source_capture_before_observer.py").write_text("# fixture")
        progress = {}
        calls = []
        observed = control.lifecycle_observation([], 0, timed, cancelled, False)
        if code < 0:
            observed["child_exit"] = code
        installed = {
            "exit": code,
            "cleanup_complete": complete,
            "timed_out": timed,
            "cancelled": cancelled,
            "observation": observed,
            "PRIVATE": "never-export",
        }

        def own(
            command,
            environment,
            seconds,
            *,
            calls=calls,
            installed=installed,
            observed=observed,
            **kwargs,
        ):
            if command[-1].endswith("install_ci_uv.py"):
                calls.append("installer")
                return copy.deepcopy(installed)
            calls.append("prepare")
            return {
                "exit": 2,
                "cleanup_complete": True,
                "timed_out": False,
                "cancelled": False,
                "observation": observed,
            }

        with monkeypatch.context() as context:
            context.setenv("FACT_CAPTURE_CONTROL_STARTED", str(control.time.monotonic()))
            context.setattr(control, "git", lambda *args, **kwargs: "")
            context.setattr(control, "workload_state", lambda *args: {})
            context.setattr(control, "overlay", lambda *args: None)
            context.setattr(control, "run_owned", own)
            context.setattr(
                control.subprocess,
                "run",
                lambda *args, **kwargs: subprocess.CompletedProcess([], 0),
            )
            context.setattr(
                control.subprocess,
                "Popen",
                lambda *args, **kwargs: pytest.fail("prephase must not reach PG"),
            )
            with pytest.raises(ValueError) as caught:
                real_execute(root, parent, progress)
        assert calls == (["installer"] if failed else ["installer", "prepare"])
        assert progress["stage"] == ("uv-install" if failed else "environment-prepare")
        data = control.refusal(caught.value, progress, binding)
        expected = {
            "exit": code,
            "cleanup_complete": complete,
            "timed_out": timed,
            "cancelled": cancelled,
            "lifecycle": {
                key: observed[key]
                for key in (
                    "child_exit",
                    "supervisor_exit",
                    "protocol_refused",
                    "outcome_records",
                    "settled_records",
                    "settled_complete",
                )
            },
        }
        if failed:
            assert data.get("uv_install") == expected
        else:
            assert "uv_install" not in data
        assert data["complete"] is False and data["control_verdict"] == "UNKNOWN"
        assert data["pg_call"] == data["cleanup"] == "UNKNOWN"
        assert "PRIVATE" not in json.dumps(data) and "never-export" not in json.dumps(data)

    malformed = control.uv_install_observation(
        {
            "exit": True,
            "cleanup_complete": 1,
            "timed_out": "PRIVATE",
            "cancelled": None,
            "observation": {
                "child_exit": -65,
                "supervisor_exit": 256,
                "outcome_records": True,
                "settled_records": 9,
                "settled_complete": "PRIVATE",
                "protocol_refused": 0,
            },
        }
    )
    assert all(value is None for key, value in malformed.items() if key != "lifecycle")
    assert all(value is None for value in malformed["lifecycle"].values())


def _kernel_domain_case(tmp_path, label, supervisor, case):
    """One finite owned controller; private actor identities never leave its RAM."""
    import ast

    fixture = tmp_path / (label + "-kernel.py")
    fixture.write_text(_owned_retirement_kernel_fixture())
    allowed = sorted(
        {
            node.lineno
            for node in ast.walk(ast.parse(fixture.read_text()))
            if isinstance(node, (ast.Assert, ast.Call, ast.Raise))
        }
    )
    projection = tmp_path / (label + "-closed.json")
    result = control.run_owned(
        [sys.executable, str(fixture), "run", case],
        {
            **os.environ,
            "OWNED_SUPERVISOR": str(supervisor),
            "KERNEL_PROJECTION": str(projection),
            "KERNEL_ALLOWED_LINES": json.dumps(allowed),
        },
        10,
    )
    assert result["cleanup_complete"] is True, result["observation"]
    assert result["timed_out"] is result["cancelled"] is False
    closed = json.loads(projection.read_text())
    assert closed["events"][-1]["all_owned_groups_absent"] is True, closed
    assert closed["events"][-1]["real_ECHILD"] is True
    assert closed["events"][-1]["sentinel_reaped"] is True
    return result, closed


def test_kernel_domain_settlement_survives_retirement_and_rejects_omitted_children(tmp_path):
    """All-child kernel closure, rather than a complete live census, is required."""
    for case in ("retired-task", "leader-thread"):
        result, closed = _kernel_domain_case(tmp_path, case, control.SUPERVISOR, case)
        assert result["exit"] == 0 and closed["exception_class"] is None, closed
        row = closed["result"]["cases"][0]
        assert row["settled_complete"] is True and row["exit"] == 3
        assert row["supervisor_cancelled"] is True
        assert row["kernel_domain_basis"] == control.SETTLEMENT_BASIS
        assert row["foreign_task_scans"] == 0
        assert row["real_ECHILD"] and row["adopted_descendant_reaped"]
        assert row["adopted_count"] >= 2 and row["sentinel_preserved"]
    for case in ("parent-loss", "reader-loss"):
        result, closed = _kernel_domain_case(tmp_path, case, control.SUPERVISOR, case)
        assert result["exit"] == 0 and closed["exception_class"] is None, closed
        row = closed["result"]["cases"][0]
        assert row["settled_complete"] is False and row["exit"] == 2
        assert row["real_ECHILD"] and row["adopted_descendant_reaped"]
        assert row["reason"] == "latched_refusal" and row["counts"]["scan_refused"] > 0
        assert row["sentinel_preserved"] is True
    original = control.SUPERVISOR.read_text()
    # Inverting pending wait0 into ECHILD must fail the very same omitted-child
    # assertion. It is not enough for a complete-looking record to be emitted.
    pending = "if pid == 0:\n                return False"
    assert original.count(pending) == 1
    inverse = tmp_path / "wait0-is-echild.py"
    inverse.write_text(original.replace(pending, "if pid == 0:\n                return True"))
    for label, source in (
        ("omitted", control.SUPERVISOR),
        ("wait0-inverse", inverse),
        ("restored", control.SUPERVISOR),
    ):
        result, closed = _kernel_domain_case(tmp_path, label, source, "omitted-direct")
        if label == "wait0-inverse":
            assert result["exit"] == 2
            assert closed["first_fault"]["class"] == "AssertionError"
            summaries = [x for x in closed["events"] if x.get("fixture") == "case-summary"]
            assert len(summaries) == 1
            assert summaries[0]["settled"]["complete"] is True
            assert summaries[0]["closure"]["ECHILD"] is False
            assert summaries[0]["closure"]["all_child_wait_pending"] is True
        else:
            assert result["exit"] == 0 and closed["exception_class"] is None, closed
            row = closed["result"]["cases"][0]
            assert row["exit"] == 2 and row["settled_complete"] is False
            assert row["reason"] == "settlement_deadline"
            assert row["omitted_discoveries"] > 0 and row["all_child_wait_pending"]
            assert row["real_ECHILD"] is False and row["sentinel_preserved"]


def test_kernel_domain_protocol_refuses_unknown_ownership_and_malformed_proof(
    monkeypatch, tmp_path
):
    """Hard errors, bounded discovery and wait/proof schemas remain fail-closed."""
    import errno
    import importlib.util
    import io
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location("_kernel_domain_software", control.SUPERVISOR)
    supervisor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(supervisor)
    assert supervisor.SETTLEMENT_BASIS == control.SETTLEMENT_BASIS
    calls = []
    for answer in (
        (0, 0),
        (37, 0),
        None,
        (True, 0),
        (37, True),
        (0, 1),
        (-1, 0),
        (1, 0),
        [0, 0],
        (37,),
        (37, 0x7F),
        (37, -1),
        (37, 1 << 20),
    ):

        def wait(pid, flags, answer=answer):
            calls.append((pid, flags))
            if answer is None:
                raise ChildProcessError()
            return answer

        with monkeypatch.context() as context:
            context.setattr(supervisor.os, "waitpid", wait)
            if answer in ((0, 0), (37, 0), None) and type(answer) is not list:
                # bool values compare equal to integers, but must still refuse.
                valid = answer is None or all(type(x) is int for x in answer)
            else:
                valid = False
            if valid:
                assert supervisor.all_child_wait() == answer
            else:
                with pytest.raises(ValueError):
                    supervisor.all_child_wait()
        assert calls[-1] == (-1, supervisor.ALL_CHILDREN | os.WNOHANG)
    for error in (OSError(errno.EINVAL, "unsupported"), PermissionError(errno.EPERM, "denied")):
        with monkeypatch.context() as context:
            context.setattr(
                supervisor.os, "waitpid", lambda *args, error=error: (_ for _ in ()).throw(error)
            )
            with pytest.raises(OSError):
                supervisor.all_child_wait()
    for unsupported in ("waitpid", "waitid"):
        executed = []
        with monkeypatch.context() as context:
            context.setattr(sys, "argv", ["supervisor", str(os.getppid()), "101", "unused"])
            context.setattr(supervisor, "process", lambda pid: (101, "S"))
            context.setattr(supervisor.os, "listdir", lambda path: [str(os.getpid())])
            context.setattr(supervisor.signal, "signal", lambda *args: None)

            def unsupported_wait(*args, unsupported=unsupported):
                if unsupported == "waitpid":
                    raise OSError(errno.EINVAL, "unsupported all-child wait")
                raise ChildProcessError()

            context.setattr(supervisor.os, "waitpid", unsupported_wait)
            context.setattr(
                supervisor.os,
                "waitid",
                lambda *args: (_ for _ in ()).throw(
                    OSError(errno.EINVAL, "unsupported all-child wait")
                ),
            )
            context.setattr(
                supervisor.subprocess,
                "Popen",
                lambda *args, executed=executed, **kwargs: executed.append(True),
            )
            with pytest.raises(OSError):
                supervisor.main()
        assert executed == []
    for info in (
        None,
        SimpleNamespace(si_pid=37, si_code=os.CLD_EXITED, si_status=0),
        SimpleNamespace(si_pid=True, si_code=os.CLD_EXITED, si_status=0),
        SimpleNamespace(si_pid=38, si_code=os.CLD_EXITED, si_status=0),
        SimpleNamespace(si_pid=37, si_code=True, si_status=0),
    ):
        with monkeypatch.context() as context:

            def waiting(*args, info=info):
                calls.append(args)
                return info

            context.setattr(supervisor.os, "waitid", waiting)
            if info is None or (
                type(info.si_pid) is int and info.si_pid == 37 and type(info.si_code) is int
            ):
                supervisor.held_direct_child(37)
            else:
                with pytest.raises(ValueError):
                    supervisor.held_direct_child(37)
        assert calls[-1] == (
            os.P_PID,
            37,
            os.WEXITED | os.WNOHANG | os.WNOWAIT | supervisor.ALL_CHILDREN,
        )
    for error in (ChildProcessError(), PermissionError(errno.EPERM, "denied")):
        with monkeypatch.context() as context:
            context.setattr(
                supervisor.os, "waitid", lambda *args, error=error: (_ for _ in ()).throw(error)
            )
            with pytest.raises(OSError):
                supervisor.held_direct_child(37)
    for mode in ("positive", "omitted", "permission", "invalid", "overflow", "unknown", "threads"):
        diagnostic = {
            "counts": dict.fromkeys(supervisor.REFUSAL_KEYS, 0),
            "counts_saturated": False,
            "first_scan_refusal": None,
            "first_task_scan": None,
        }
        with monkeypatch.context() as context:
            context.setattr(
                supervisor,
                "process",
                lambda pid, mode=mode: None if mode == "unknown" else (101, "R"),
            )
            context.setattr(
                supervisor.os,
                "listdir",
                lambda path, mode=mode: ["37", "38"] if mode == "threads" else [str(os.getpid())],
            )

            def opening(*args, mode=mode):
                if mode == "permission":
                    raise PermissionError(errno.EPERM, "denied")
                return io.StringIO(
                    ""
                    if mode == "omitted"
                    else "bad"
                    if mode == "invalid"
                    else "1" * 65536
                    if mode == "overflow"
                    else "37"
                )

            context.setattr(supervisor, "open", opening, raising=False)
            found, refused = supervisor.direct_children(diagnostic)
        assert refused is (mode not in ("positive", "omitted"))
        assert found == ({37} if mode == "positive" else set())
        if refused:
            assert sum(diagnostic["counts"].values()) > 0
    events = [
        {"event": "ready", "pid": 37, "start_ticks": 101},
        {"event": "outcome", "code": 0, "signal": None},
        {
            "event": "diagnostic",
            "reason": "settled",
            "counts": dict.fromkeys(control.REFUSAL_COUNTS, 0),
            "counts_saturated": False,
            "first_scan_refusal": None,
            "first_task_scan": None,
            "owned_remaining": 0,
            "direct_remaining": 0,
            "kernel_children_closed": True,
        },
        {
            "event": "settled",
            "complete": True,
            "basis": control.SETTLEMENT_BASIS,
            "all_child_echild": True,
            "held_children": 0,
            "root_outcome": True,
            "cancelled": False,
        },
    ]
    assert control.kernel_settlement_complete(events, 0)
    for index in range(4):
        assert not control.kernel_settlement_complete(events[:index] + events[index + 1 :], 0)
        assert not control.kernel_settlement_complete(events + [events[index]], 0)
    for key, value in (
        ("basis", "old-census"),
        ("all_child_echild", False),
        ("held_children", 1),
        ("held_children", False),
        ("root_outcome", False),
        ("complete", 1),
        ("cancelled", True),
        ("cancelled", 0),
    ):
        bad = copy.deepcopy(events)
        bad[3][key] = value
        assert not control.kernel_settlement_complete(bad, 0)
    bad = copy.deepcopy(events)
    bad[2]["counts"]["scan_refused"] = 1
    assert not control.kernel_settlement_complete(bad, 0)
    assert not control.kernel_settlement_complete(events, 2)
    assert not control.kernel_settlement_complete(events, False)
    assert not control.kernel_settlement_complete(list(reversed(events)), 0)
    bad = copy.deepcopy(events)
    bad[2]["unrecognized"] = "private"
    assert not control.kernel_settlement_complete(bad, 0)
    before, after = phase("before"), phase("after")
    for value in (None, "UNKNOWN", "old-census"):
        before["process_cleanup_basis"] = value
        with pytest.raises(ValueError):
            control.require_witness(before, after, {"nonce": "a" * 64})

    # Root outcome starts autonomous supervisor cleanup. Sending TERM again can
    # race CPython finalization after it has restored the default disposition.
    terminal = tmp_path / "terminal-outcome-supervisor.py"
    terminal.write_text(_terminal_outcome_supervisor())
    with monkeypatch.context() as context:
        context.setattr(control, "SUPERVISOR", terminal)
        lifecycle = control.run_owned([sys.executable, "unused"], os.environ.copy(), 2)
    assert lifecycle["exit"] == 0 and lifecycle["cleanup_complete"] is True
    assert lifecycle["observation"]["supervisor_exit"] == 0
    assert lifecycle["timed_out"] is lifecycle["cancelled"] is False


def _terminal_outcome_supervisor():
    """Closed producer-only seam; it never executes the native workload."""
    return (
        "import json,signal,time\n"
        "def emit(value): print(json.dumps(value),flush=True)\n"
        "signal.signal(signal.SIGTERM,signal.SIG_DFL)\n"
        "emit({'event':'ready','pid':37,'start_ticks':101})\n"
        "emit({'event':'outcome','code':0,'signal':None})\n"
        "time.sleep(0.15)\n"
        "emit({'event':'diagnostic','reason':'settled','counts':"
        + repr(dict.fromkeys(control.REFUSAL_COUNTS, 0))
        + ",'counts_saturated':False,'first_scan_refusal':None,'first_task_scan':None,"
        "'owned_remaining':0,'direct_remaining':0,'kernel_children_closed':True})\n"
        "emit({'event':'settled','complete':True,'basis':'linux-subreaper-all-child-v2',"
        "'all_child_echild':True,'held_children':0,'root_outcome':True,'cancelled':False})\n"
    )


def _supervisor_cancellation_fixture():
    return r'''"""Actual supervisor handlers under no-workload stdlib stand-ins."""
import importlib.machinery
import importlib.util
import os
import signal
import sys
from types import SimpleNamespace
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('cancelled_supervisor', os.environ['CANCELLATION_SOURCE'])
spec = importlib.util.spec_from_loader(loader.name, loader)
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)
seam = os.environ['CANCELLATION_SEAM']
assert seam in {'positive', 'ready', 'before-settled', 'after-settled', 'after-main', 'default-handler'}
waits = iter((None, (37, 0), None))
def wait(pid, flags):
    assert pid == -1 and flags == supervisor.WAIT_FLAGS
    result = next(waits)
    if result is None:
        raise ChildProcessError()
    return result
original_emit = supervisor.emit
def emit(value):
    if seam == 'before-settled' and value['event'] == 'settled':
        os.kill(os.getpid(), signal.SIGTERM)
    accepted = original_emit(value)
    if (seam == 'ready' and value['event'] == 'ready'
            or seam == 'after-settled' and value['event'] == 'settled'):
        os.kill(os.getpid(), signal.SIGTERM)
    return accepted
class Poll:
    def register(self, *args):
        pass
    def poll(self, *args):
        return []
status = 2
with (patch.object(sys, 'argv', ['supervisor', str(os.getppid()), '101', 'unused']),
      patch.object(supervisor, 'process', lambda pid: (101, 'S')),
      patch.object(supervisor.os, 'listdir', lambda path: [str(os.getpid())]),
      patch.object(supervisor.os, 'waitpid', wait),
      patch.object(supervisor.os, 'waitid', lambda *args: (_ for _ in ()).throw(ChildProcessError())),
      patch.object(supervisor.ctypes, 'CDLL', lambda *a, **k: SimpleNamespace(prctl=lambda *a: 0)),
      patch.object(supervisor.subprocess, 'Popen', lambda *a, **k: SimpleNamespace(pid=37)),
      patch.object(supervisor.select, 'poll', Poll), patch.object(supervisor, 'emit', emit)):
    try:
        status = supervisor.main()
        if seam == 'after-main':
            os.kill(os.getpid(), signal.SIGTERM)
        if seam == 'default-handler':
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGTERM)
    except SystemExit as stopped:
        status = stopped.code
raise SystemExit(status)
'''


def test_supervisor_cancellation_propagates_without_erasing_process_cleanup(tmp_path, monkeypatch):
    """Actual early/terminal signals veto evidence independently of closed cleanup."""
    fixture = tmp_path / "supervisor-cancellation.py"
    fixture.write_text(_supervisor_cancellation_fixture())
    rows = []
    for seam in (
        "positive",
        "ready",
        "before-settled",
        "after-settled",
        "after-main",
        "default-handler",
    ):
        with monkeypatch.context() as context:
            context.setattr(control, "SUPERVISOR", fixture)
            context.setattr(control, "CANCELLED", [])
            result = control.run_owned(
                [sys.executable, "unused"],
                {
                    **os.environ,
                    "CANCELLATION_SOURCE": str(
                        Path(__file__).resolve().parents[2]
                        / "scripts/fact_source_capture_before_supervisor.py"
                    ),
                    "CANCELLATION_SEAM": seam,
                },
                2,
            )
            assert result["exit"] == 0 and not result["timed_out"], result
            assert result["cancelled"] is (seam != "positive"), result
            assert result["observation"]["cancelled"] is (seam != "positive")
            assert bool(control.CANCELLED) is (seam != "positive")
            assert result["cleanup_complete"] is (seam != "before-settled"), result
            assert result["observation"]["supervisor_exit"] == (
                0 if seam == "positive" else -15 if seam == "default-handler" else 3
            )
            rows.append(
                {
                    "seam": seam,
                    "result": result,
                    "aggregate_cancelled": bool(control.CANCELLED),
                    "no_workload_native_or_install": True,
                }
            )
    (tmp_path / "supervisor-cancellation-results.json").write_text(json.dumps(rows, indent=2))


def test_capture_settlement_observes_exact_tasks_without_cancelling_workload(tmp_path, monkeypatch):
    """Real gather cancellation can return before siblings finish passive unwinding."""
    import asyncio
    import hashlib
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import asyncpg

    from butlers.core import fact_authority

    runtime = Path(fact_authority.__file__).resolve()
    root = runtime.parents[3]
    cases = ("healthy", "delayed", "persistent", "missing", "incomplete", "wrong-owner", "foreign")
    selected = os.environ.get("FACT_CAPTURE_SETTLEMENT_CASE")
    assert selected is None or selected in cases
    if selected:
        cases = (selected,)

    async def exercise(case):
        release = asyncio.Event()
        if case in ("healthy", "incomplete", "wrong-owner", "foreign"):
            release.set()
        captures = []
        pool_reads = []
        inboxes = [uuid.uuid4() for _ in range(3)]

        class ProbePool(asyncpg.Pool):
            def __init__(self):
                pass

            def get_max_size(self):
                return 1

            async def fetchval(self, query):
                assert query == "SELECT current_user"
                return "butler_switchboard_rw"

            @asynccontextmanager
            async def acquire(self, **kwargs):
                task = asyncio.current_task()
                if task.get_coro().cr_code is capture_code:
                    captures.append(task)
                    try:
                        await asyncio.Event().wait()
                    finally:
                        # One child finishes first; gather propagates its cancellation
                        # while the other two are still in real asynchronous cleanup.
                        if task is not captures[0]:
                            await release.wait()
                    raise AssertionError("capture must exit through cancellation")

                class Connection:
                    def is_in_transaction(self):
                        return False

                    async def fetchval(self, query):
                        assert query == "SELECT 1"
                        pool_reads.append(query)
                        if case == "delayed":
                            await release.wait()
                            await asyncio.sleep(0)
                        return 1

                yield Connection()

        pool = ProbePool()
        issuer = fact_authority.FactSourceContextRegistry(pool)
        capture_code = type(issuer).capture_accepted_report.__code__
        namespace = {"asyncio": asyncio}
        target = control.NODE.split("::")[1]
        exec(
            compile(
                f"async def {target}(issuer,inboxes):\n"
                " async with asyncio.timeout(0.01):\n"
                "  await asyncio.gather(*(issuer.capture_accepted_report(i,None) for i in inboxes))\n",
                str(root / control.NODE.split("::")[0]),
                "exec",
            ),
            namespace,
        )
        monkeypatch.setattr(
            observer,
            "CONFIG",
            {
                "root": str(root),
                "phase": "before",
                "runtime_sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
            },
        )
        state = phase("before")
        state.update(
            capture_tasks_settled=False, durable_partial_reports=None, memory_partial_reports=None
        )
        monkeypatch.setattr(observer, "STATE", state)
        request = SimpleNamespace(node=SimpleNamespace(nodeid=control.NODE))

        class Reports:
            async def fetch(self, query, *args):
                assert "WHERE id = ANY($1::uuid[])" in query
                assert len(args) == 1 and set(args[0]) == set(inboxes)
                return [{"id": key, "report": None} for key in inboxes]

        fixture = observer.observe_registered_context.__wrapped__(
            request,
            SimpleNamespace(_registered_endpoint="http://127.0.0.1:1", sw=Reports()),
            pool,
        )
        await anext(fixture)
        previous_trace = sys.gettrace()
        sys.settrace(None)
        hook = observer.pytest_runtest_call(SimpleNamespace(nodeid=control.NODE))
        foreign = None
        release_task = None
        try:
            next(hook)
            with pytest.raises(TimeoutError):
                await namespace[target](issuer, inboxes)
            with pytest.raises(StopIteration):
                next(hook)
            assert sys.gettrace() is None
            assert len(captures) == 3 and len(set(captures)) == 3
            assert observer.REFERENCES == (issuer, inboxes)
            if case in ("delayed", "persistent", "missing"):
                assert sum(not task.done() for task in captures) == 2
            if case == "missing":
                monkeypatch.setattr(observer, "REFERENCES", None)
            if case == "incomplete":
                observer.CAPTURE_TASKS.pop(inboxes[0])
                state["capture_tasks_settled"] = True  # stale software state must fail closed
            if case == "wrong-owner":
                _, task = observer.CAPTURE_TASKS[inboxes[0]]
                observer.CAPTURE_TASKS[inboxes[0]] = (object(), task)
            if case == "foreign":

                async def unrelated():
                    await asyncio.Event().wait()

                unrelated.__qualname__ = "FactSourceContextRegistry.capture_accepted_report"
                foreign = asyncio.create_task(unrelated())
                await asyncio.sleep(0)
            if case == "delayed":

                async def finish_unwinding():
                    await asyncio.sleep(0)
                    release.set()

                release_task = asyncio.create_task(finish_unwinding())
            cancellations = [task.cancelling() for task in captures]
            with pytest.raises(StopAsyncIteration):
                await anext(fixture)
            assert pool_reads == ["SELECT 1"]
            assert [task.cancelling() for task in captures] == cancellations, (
                "observer cancelled workload"
            )
            if case in ("healthy", "delayed", "foreign"):
                assert all(task.done() for task in captures)
                assert state["capture_tasks_settled"] is True, (
                    "settled exact-owned tasks must be positively observed"
                )
            else:
                assert state["capture_tasks_settled"] is False
                if case in ("persistent", "missing"):
                    assert any(not task.done() for task in captures), (
                        "negative must reach actual pending work"
                    )
                else:
                    assert all(task.done() for task in captures), (
                        "completion cannot replace incomplete ownership observations"
                    )
            if foreign is not None:
                assert not foreign.done(), "observer touched a foreign same-name task"
        finally:
            hook.close()
            sys.settrace(previous_trace)
            # Control-owned cleanup only; the observer must never perform this action.
            release.set()
            await asyncio.gather(*captures, return_exceptions=True)
            assert all(task.done() for task in captures)
            if release_task is not None:
                await release_task
            if foreign is not None:
                foreign.cancel()
                await asyncio.gather(foreign, return_exceptions=True)
            await fixture.aclose()

    for case in cases:
        asyncio.run(exercise(case))


def test_after_diagnostic_observes_bound_suppressed_errors_without_changing_acceptance(
    tmp_path, monkeypatch
):
    """Pinned executable code and actual trace/projector, with closed software collaborators."""
    import asyncio
    from contextlib import asynccontextmanager
    from types import ModuleType, SimpleNamespace

    # Exact immutable candidate bytes; hosted shallow checkouts need no historical Git object.
    source = (
        Path(__file__).parent / "fixtures/fact_source_capture_candidate_runtime.txt"
    ).read_bytes()
    assert hashlib.sha256(source).hexdigest() == (
        "f438297121fe03f29d838eabd29ff3392f2d85d6b6b897ebd93c993aacb66bf1"
    )
    assert hashlib.sha1(b"blob " + str(len(source)).encode() + b"\0" + source).hexdigest() == (
        "1f3287e7583f31eb969ffcdc6d3eeb3a5204d271"
    )
    runtime = tmp_path / control.RUNTIME
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(source)
    module = ModuleType("_closed_after_diagnostic_runtime")
    module.__file__ = str(runtime)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(source, str(runtime), "exec", dont_inherit=True), module.__dict__)
    routing = ModuleType("butlers.tools.switchboard.registry.registry")
    route_failure = ValueError
    clone_probe = None
    in_clone = False

    async def unavailable_route(*args):
        nonlocal in_clone
        if clone_probe is not None and not in_clone:
            in_clone = True
            try:
                await clone_probe()
            finally:
                in_clone = False
        raise route_failure("PRIVATE_CAUSE credential=PRIVATE_VALUE")

    routing.resolve_routing_target = unavailable_route
    monkeypatch.setitem(sys.modules, routing.__name__, routing)
    cases = (
        "healthy",
        "suppressed",
        "snapshot-error",
        "lock-read",
        "transaction-exit",
        "private-class",
        "preexisting",
    )
    selected = os.environ.get("FACT_CAPTURE_DIAGNOSTIC_CASE")
    assert selected is None or selected in cases
    if selected:
        cases = (selected,)
    observed = {}

    async def exercise(case):
        nonlocal route_failure, clone_probe
        route_failure = (
            type("PRIVATE_CLASS", (Exception,), {}) if case == "private-class" else ValueError
        )
        inboxes, owned, writes = [uuid.uuid4() for _ in range(3)], [], []
        stored = module.FactWriteContext("owner").to_record()
        context = {"source_channel": "email", "source_sender_identity": "PRIVATE_ID"}
        if case in {"healthy", "lock-read"}:
            context["_fact_source_report"] = stored
        if case == "transaction-exit":
            context["source_sender_identity"] = None
        row = {
            "request_context": context,
            "raw_payload": {},
            "normalized_text": "PRIVATE_TEXT",
            "lifecycle_state": "accepted",
        }

        class Pool:
            @asynccontextmanager
            async def acquire(self, **kwargs):
                yield self

            @asynccontextmanager
            async def transaction(self):
                yield
                if case == "transaction-exit":
                    raise RuntimeError("PRIVATE_COMMIT_FAILURE")

            async def fetchrow(self, query, *args):
                if case == "snapshot-error":
                    raise KeyError("PRIVATE_SQL_KEY")
                if case == "lock-read" and "FOR UPDATE" in query:
                    raise KeyError("PRIVATE_LOCKED_SQL_KEY")
                return dict(row)

            async def execute(self, query, *args):
                writes.append(True)

        issuer = module.FactSourceContextRegistry(Pool())
        binding = {"nonce": "a" * 64}
        monkeypatch.setattr(
            observer, "CONFIG", {"phase": "after", "root": str(tmp_path), "binding": binding}
        )
        observer.prepare_diagnostic(runtime, type(issuer))
        # A structurally equal foreign code object executes under the actual
        # bound capture Task. It must not acquire local operation attribution.
        from types import FunctionType

        original = type(issuer).resolve_identity.__code__
        clone = original.replace(co_filename=str(tmp_path / "PRIVATE_FOREIGN.py"))
        assert clone == original and clone is not original
        assert observer.diagnostic_sites(original)
        assert observer.diagnostic_sites(clone) == ()
        same_filename_clone = original.replace()
        assert same_filename_clone == original and same_filename_clone is not original
        assert observer.diagnostic_sites(same_filename_clone) == ()
        cloned_resolve = FunctionType(clone, module.__dict__)

        foreign_issuer = module.FactSourceContextRegistry(Pool())

        async def foreign_source_probe():
            await cloned_resolve(issuer, None, "email", "PRIVATE_ID", raise_on_error=False)
            await foreign_issuer.resolve_identity(None, "email", "PRIVATE_ID")

        clone_probe = foreign_source_probe if case == "suppressed" else None
        assert observer.DIAGNOSTIC is not None, "actual pinned code must bind"
        namespace = {
            "asyncio": asyncio,
            "owned": owned,
            "resolved_entity": None if case == "transaction-exit" else inboxes[0],
        }
        target = control.NODE.split("::")[1]
        exec(
            compile(
                f"async def {target}(issuer,inboxes):\n"
                " tasks=[asyncio.create_task(issuer.capture_accepted_report(i,resolved_entity)) for i in inboxes]\n"
                " owned.extend(tasks)\n"
                " await asyncio.gather(*tasks)\n",
                str(tmp_path / control.NODE.split("::")[0]),
                "exec",
            ),
            namespace,
        )
        previous = sys.gettrace()
        ambient = (lambda frame, event, arg: None) if case == "preexisting" else None
        sys.settrace(ambient)
        hook = observer.pytest_runtest_call(SimpleNamespace(nodeid=control.NODE))
        failure = None
        try:
            next(hook)
            try:
                await namespace[target](issuer, inboxes)
            except Exception as error:
                failure = error
            if case == "suppressed":
                # A real foreign Task in the same verified code also stays opaque.
                count = len(observer.DIAGNOSTIC["seen"])
                foreign_task = asyncio.create_task(
                    issuer.resolve_identity(None, "email", "PRIVATE")
                )
                await foreign_task
                assert foreign_task.done() and len(observer.DIAGNOSTIC["seen"]) == count
            with pytest.raises(StopIteration):
                next(hook)
            assert sys.gettrace() is ambient
            observer.observe_call_error(failure)
            result = observer.after_diagnostic()
            observed[case] = result
            assert "PRIVATE" not in json.dumps(result)
            assert len(owned) == 3 and all(task.done() for task in owned)
            assert all(task.cancelling() == 0 for task in owned)
            if case == "preexisting":
                assert result == control.unknown_after_diagnostic(binding)
                return
            assert result["available"] is True and result["truncated"] is False
            if case == "healthy":
                assert failure is None
                assert result["call_error"] == {"category": "none", "operation": "none"}
                assert result["resolver_errors"] == []
            elif case == "snapshot-error":
                assert type(failure) is KeyError
                assert observer.exception_facts(failure, tmp_path)[0]["exception"] == "other"
                assert result["call_error"] == {
                    "category": "key-error",
                    "operation": "capture-snapshot",
                }
                assert result["resolver_errors"] == []
            elif case == "lock-read":
                assert type(failure) is KeyError and writes == []
                assert result["call_error"] == {
                    "category": "key-error",
                    "operation": "capture-lock-read",
                }
                assert result["resolver_errors"] == []
            elif case == "transaction-exit":
                assert type(failure) is RuntimeError and len(writes) == 3
                assert result["call_error"] == {
                    "category": "runtime-error",
                    "operation": "capture-lock-context",
                }, "context-exit errors after writes must not claim a locked-row read failure"
                assert result["resolver_errors"] == []
            else:
                assert type(failure) is ValueError
                assert observer.exception_facts(failure, tmp_path)[0]["exception"] == "other"
                assert result["call_error"] == {
                    "category": "value-error",
                    "operation": "capture-source-check",
                }
                assert any(
                    r["operation"] == "resolver-routing"
                    and r["category"] == ("other" if case == "private-class" else "value-error")
                    for r in result["resolver_errors"]
                ), "suppressed owning errors must be observed"
                assert any(r["category"] == "identity-query" for r in result["resolver_errors"])
                if case == "suppressed":
                    assert (
                        sum(
                            r["count"]
                            for r in result["resolver_errors"]
                            if r["operation"] == "resolver-suppress"
                        )
                        == 3
                    ), "foreign cloned code must not gain local operation attribution"
                    saved = observer.CAPTURE_TASKS
                    observer.CAPTURE_TASKS = None
                    assert observer.after_diagnostic() == control.unknown_after_diagnostic(binding)
                    observer.CAPTURE_TASKS = dict(saved)
                    observer.CAPTURE_TASKS.pop(inboxes[0])
                    assert observer.after_diagnostic() == control.unknown_after_diagnostic(binding)
                    observer.CAPTURE_TASKS = saved
        finally:
            hook.close()
            sys.settrace(previous)
            await asyncio.gather(*owned, return_exceptions=True)
            assert all(task.done() for task in owned)

    for case in cases:
        asyncio.run(exercise(case))
    if selected:
        return
    actual = observed["suppressed"]
    binding = actual["binding"]
    assert control.safe_after_diagnostic(actual, binding) == actual
    # Neither a rich annex nor its absence can turn the existing failed AFTER into GREEN.
    failed = phase("after")
    failed["phases"]["call"], failed["exception"], failed["pytest_exit"] = "failed", "other", 1
    with pytest.raises(ValueError):
        control.require_phase(failed, "after", binding)
    for bad in (
        {**actual, "extra": "PRIVATE"},
        {k: v for k, v in actual.items() if k != "call_error"},
        {**actual, "available": 1},
        {**actual, "truncated": 0},
        {**actual, "phase": "before"},
        {**actual, "binding": {}},
        {**actual, "call_error": {"category": "PRIVATE", "operation": "unknown"}},
        {
            **actual,
            "resolver_errors": [{"category": "other", "operation": "resolver-wrap", "count": True}],
        },
        {
            **actual,
            "resolver_errors": [{"category": "other", "operation": "resolver-wrap", "count": 25}],
        },
        {**actual, "resolver_errors": actual["resolver_errors"] * 9},
        {**actual, "available": False},
        {**actual, "call_error": {"category": "none", "operation": "capture-report-write"}},
        {**actual, "call_error": {"category": "value-error", "operation": "none"}},
        {**actual, "call_error": {"category": "unknown", "operation": "unknown"}},
        {
            **actual,
            "call_error": {"category": "value-error", "operation": "unknown"},
            "truncated": False,
        },
        {
            **actual,
            "resolver_errors": [{"category": "other", "operation": "unknown", "count": 1}],
            "truncated": False,
        },
        {
            **actual,
            "resolver_errors": [
                {"category": "other", "operation": "capture-report-write", "count": 1}
            ],
        },
    ):
        assert control.safe_after_diagnostic(bad, binding) == control.unknown_after_diagnostic(
            binding
        )
    sink = tmp_path / "annex.json"
    control.atomic(sink, actual)
    assert control.read_after_diagnostic(sink, binding) == actual
    for text in ("PRIVATE", "{" + '"schema":1,' * 2 + '"binding":{}}', " " * 8193):
        sink.write_text(text)
        assert control.read_after_diagnostic(sink, binding) == control.unknown_after_diagnostic(
            binding
        )
    sink.unlink()
    sink.symlink_to(runtime)
    assert control.read_after_diagnostic(sink, binding) == control.unknown_after_diagnostic(binding)
    assert control.read_after_diagnostic(
        tmp_path / "missing", binding
    ) == control.unknown_after_diagnostic(binding)

    observer.prepare_diagnostic(runtime, module.FactSourceContextRegistry)
    code = module.FactSourceContextRegistry.resolve_identities.__code__
    frame = SimpleNamespace(f_code=code, f_lineno=182)
    # Protocol events and repeated resumption do not grow retained identities.
    for kind in (StopIteration, StopAsyncIteration, GeneratorExit):
        observer.record_resolver_error(frame, kind("PRIVATE"))
    error = ValueError("PRIVATE")
    for _ in range(100):
        observer.record_resolver_error(frame, error)
    assert len(observer.DIAGNOSTIC["seen"]) == 1
    for _ in range(30):
        observer.record_resolver_error(frame, ValueError("PRIVATE"))
    assert len(observer.DIAGNOSTIC["seen"]) == 24
    assert sum(r["count"] for r in observer.DIAGNOSTIC["records"]) == 24
    assert observer.DIAGNOSTIC["truncated"] is True
    observer.prepare_diagnostic(runtime, module.FactSourceContextRegistry)
    for line in (182, 185, 188, 189, 193, 212):
        for kind in (ValueError, KeyError):
            observer.record_resolver_error(
                SimpleNamespace(f_code=code, f_lineno=line), kind("PRIVATE")
            )
    assert len(observer.DIAGNOSTIC["records"]) == 8 and observer.DIAGNOSTIC["truncated"]
    assert len(observer.DIAGNOSTIC["seen"]) <= 24
    observer.prepare_diagnostic(runtime, module.FactSourceContextRegistry)
    observer.record_resolver_error(
        SimpleNamespace(f_code=code, f_lineno=999), ValueError("PRIVATE")
    )
    assert observer.DIAGNOSTIC["records"][0]["operation"] == "unknown"
    assert observer.DIAGNOSTIC["truncated"] is True
    assert "PRIVATE" not in json.dumps(observer.DIAGNOSTIC["records"])
    # A byte-identical source compiled at a foreign path does not match code identity.
    foreign = ModuleType("_foreign_diagnostic_runtime")
    monkeypatch.setitem(sys.modules, foreign.__name__, foreign)
    exec(compile(source, str(tmp_path / "foreign.py"), "exec", dont_inherit=True), foreign.__dict__)
    observer.prepare_diagnostic(runtime, foreign.FactSourceContextRegistry)
    assert observer.DIAGNOSTIC is None
    # Exercise the actual producer failed-AFTER path, including export before refusal.
    # Closed native-dependency stand-ins do no install/exec/PG/network work.
    import signal

    parent = tmp_path / "producer"
    root = parent / "controller"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/fact_source_capture_before_observer.py").write_text("# closed control\n")

    class Sentinel:
        def poll(self):
            return None

        def kill(self):
            pass

        def wait(self, timeout):
            return 0

    def own(command, environment, seconds, **kwargs):
        code = 0
        if command[-1] == "--phase-child":
            cfg = json.loads(Path(environment["FACT_CAPTURE_CONTROL_CONFIG"]).read_text())
            data = phase(cfg["phase"])
            data["binding"] = cfg["binding"]
            if cfg["phase"] == "after":
                data["phases"]["call"], data["exception"], data["pytest_exit"] = (
                    "failed",
                    "other",
                    1,
                )
                control.atomic(
                    Path(cfg["output"]).with_name("after-diagnostic.json"),
                    {
                        **actual,
                        "binding": cfg["binding"],
                    },
                )
            control.atomic(Path(cfg["output"]), data)
            code = data["pytest_exit"]
        return {
            "exit": code,
            "cleanup_complete": True,
            "timed_out": False,
            "cancelled": False,
            "observation": {"completion_basis": control.SETTLEMENT_BASIS},
        }

    signals = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        with monkeypatch.context() as context:
            context.setattr(control, "CANCELLED", [])
            context.setattr(control, "AGGREGATE_DEADLINE", None)
            context.setattr(control, "__file__", str(root / "scripts/control.py"))
            context.setattr(control, "provider_binding", lambda root: dict(binding))
            context.setattr(control, "git", lambda *args, **kwargs: "")
            context.setattr(control, "workload_state", lambda *args: {"runtime_sha256": "b" * 64})
            context.setattr(control, "overlay", lambda *args: None)
            context.setattr(control, "prepare_uv", lambda *args, **kwargs: {})
            context.setattr(control, "run_owned", own)
            context.setattr(
                control.subprocess,
                "run",
                lambda *args, **kwargs: subprocess.CompletedProcess([], 0),
            )
            context.setattr(control.subprocess, "Popen", lambda *args, **kwargs: Sentinel())
            context.setattr(sys, "argv", ["control"])
            context.setenv("RUNNER_TEMP", str(parent))
            context.setenv("FACT_CAPTURE_CONTROL_STARTED", str(control.time.monotonic()))
            assert control.main() == 2
        evidence = parent / "fact-capture-before-evidence"
        annex = json.loads((evidence / "after-diagnostic.json").read_text())
        assert annex["available"] and annex["call_error"] == actual["call_error"]
        assert annex["resolver_errors"] == actual["resolver_errors"]
        assert json.loads((evidence / "after.json").read_text())["phases"]["call"] == "failed"
        assert json.loads((evidence / "control-refusal.json").read_text())["stage"] == "after"
        assert not (evidence / "summary.json").exists()
        assert "PRIVATE" not in json.dumps(annex)
    finally:
        for sig, previous in signals.items():
            signal.signal(sig, previous)
