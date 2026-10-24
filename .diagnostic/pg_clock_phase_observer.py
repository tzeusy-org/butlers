"""UNEXECUTED diagnostic draft. Closed phase/SQLSTATE observer; never admission proof."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest

# Numeric enums are exhaustively documented in protocol.json. No values, SQL,
# exception strings, identities, filenames or native addresses enter the trace.
CFG = json.loads((Path(__file__).parent / "source-bindings.json").read_text())
ROOT = Path.cwd().resolve()
OWNER = CFG["owner_path"]
NODES = {OWNER + "::" + name: index for index, name in enumerate(CFG["names"])}
FD = None
COUNT = SIZE = 0
FAULT = False
START = time.monotonic()
ACTIVE = None
PROBE = False
SEEN = set()
COLLECTED = False
TRACED = False
PROCESS = None
DIRECTORY = None


def emit(event, **fields):
    global COUNT, SIZE, FAULT
    if FD is None or FAULT:
        return
    try:
        record = {"e": event, "ms": max(0, int((time.monotonic() - START) * 1000)), **fields}
        encoded = (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode()
        if COUNT >= 159 or SIZE + len(encoded) > 24000:
            # Last reserved record means observation UNKNOWN, never success.
            encoded = b'{"e":9}\n'
            FAULT = True
        written = os.write(FD, encoded)
        if written != len(encoded):
            FAULT = True
        COUNT += 1
        SIZE += written
    except Exception:
        # Observer failure must not change a subject result. Missing footer/
        # record coverage is UNKNOWN to the external validator.
        FAULT = True


def once(point):
    key = (ACTIVE, point)
    if key not in SEEN:
        SEEN.add(key)
        emit(4, n=ACTIVE, p=point)


# Code identity is additionally qualified by complete source hashes in configure.
POINTS = {
    (OWNER, "test_record_and_flush_round_trips_full_payload_as_object"): {
        165: 1, 177: 2, 180: 3, 186: 4, 195: 5,
        478: 6, 480: 7, 482: 8, 485: 9, 486: 10, 492: 11, 493: 12,
        499: 13, 503: 14, 507: 15, 568: 16,
    },
    (OWNER, "migrated_db_url"): {71: 20},
    (OWNER, "pool"): {81: 21, 87: 22, 88: 23, 89: 24},
    ("src/butlers/testing/migration.py", "create_migrated_test_db"): {406: 25, 412: 26, 416: 27},
    ("src/butlers/migrations.py", "run_migrations"): {368: 28, 373: 29, 380: 30, 382: 31},
    ("scripts/scrub_filtered_event_previews.py", "scrub_existing_previews"): {
        82: 40, 83: 41, 84: 42, 95: 43, 127: 44, 131: 45,
    },
}
ABS_POINTS = {str(ROOT / path): {} for path, _ in POINTS}
for (path, name), points in POINTS.items():
    ABS_POINTS[str(ROOT / path)][name] = points


def trace(frame, event, arg):
    global PROBE, TRACED, FAULT
    try:
        functions = ABS_POINTS.get(frame.f_code.co_filename)
        if not functions or frame.f_code.co_name not in functions:
            return None
        is_scrub = frame.f_code.co_filename == str(ROOT / "scripts/scrub_filtered_event_previews.py")
        if event == "line":
            if frame.f_code.co_filename == str(ROOT / OWNER):
                if frame.f_code.co_name == CFG["names"][0] and frame.f_lineno == 485:
                    PROBE = True
                elif frame.f_code.co_name == CFG["names"][0] and frame.f_lineno == 486:
                    PROBE = False
            point = functions[frame.f_code.co_name].get(frame.f_lineno)
            if point is not None and (not is_scrub or PROBE):
                TRACED = True
                once(point)
        elif event == "return" and is_scrub and PROBE:
            module = sys.modules.get("scripts.scrub_filtered_event_previews")
            cls = vars(module).get("ScrubReceipt") if module is not None else None
            if cls is not None and type(arg) is cls:
                # Plain dataclass fields already computed by the subject. No
                # exception text, SQL arguments or additional database reads.
                state = object.__getattribute__(arg, "sqlstate")
                verdict = object.__getattribute__(arg, "verdict")
                emit(5, n=ACTIVE, s={None: 0, "55P03": 1, "57014": 2, "42501": 3}.get(state, 4),
                     v={"COMPLETE": 0, "INCOMPLETE": 1, "UNKNOWN": 2, "DRY-RUN": 3}.get(verdict, 4))
        return trace
    except Exception:
        FAULT = True
        return None


def pytest_configure(config):
    global FD, PROCESS, DIRECTORY
    # Qualification failures stop BEFORE subject execution, not silent skips.
    if sys.gettrace() is not None:
        raise pytest.UsageError("diagnostic observer requires no existing trace function")
    if os.environ.get("FAKETIME") != "+45d":
        raise pytest.UsageError("diagnostic fixed offset mismatch")
    library_names = [Path(x).name for x in os.environ.get("LD_PRELOAD", "").split(":")]
    if "libfaketime.so.1" not in library_names:
        raise pytest.UsageError("diagnostic fixed preload mismatch")
    for path, binding in CFG["source_bindings"].items():
        data = (ROOT / path).read_bytes()
        if hashlib.sha256(data).hexdigest() != binding["sha256"]:
            raise pytest.UsageError("diagnostic subject source mismatch")
    worker = os.environ.get("PYTEST_XDIST_WORKER", "controller")
    if worker == "controller":
        name = "controller"
    elif worker.startswith("gw") and worker[2:].isdigit() and 0 <= int(worker[2:]) <= 14:
        name = "w" + worker[2:]
    else:
        raise pytest.UsageError("diagnostic worker range exceeded")
    directory = Path(os.environ["PG_CLOCK_DIAG_OUTPUT"])
    if not directory.is_absolute() or not directory.is_dir() or directory.is_relative_to(ROOT):
        raise pytest.UsageError("diagnostic output must preexist outside checkout")
    PROCESS, DIRECTORY = name, directory
    FD = os.open(directory / (name + ".jsonl"), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    emit(0)


def pytest_collection_finish(session):
    global COLLECTED
    ids = [item.nodeid for item in session.items]
    # Under xdist this runs in workers. It OBSERVES, never alters selection.
    if ids and (len(ids) != 6 or set(ids) != set(NODES)):
        raise pytest.UsageError("diagnostic six-owner identity mismatch")
    if ids:
        COLLECTED = True
        emit(1, c=6)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    global ACTIVE, PROBE
    ACTIVE = NODES.get(item.nodeid)
    PROBE = False
    emit(2, n=ACTIVE)
    previous = sys.gettrace()
    try:
        if ACTIVE == 0:
            if previous is not None:
                raise pytest.UsageError("diagnostic trace ownership changed")
            sys.settrace(trace)
        yield
    finally:
        if ACTIVE == 0:
            sys.settrace(previous)
            emit(6, n=0, r=int(sys.gettrace() is previous))
        ACTIVE = None
        PROBE = False


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    slot = {"postgres_container": 0, "migrated_db_url": 1, "pool": 2}.get(fixturedef.argname)
    if slot is not None:
        emit(7, n=ACTIVE, f=slot, p=0)
    outcome = yield
    if slot is not None:
        emit(7, n=ACTIVE, f=slot, p=1, x=int(outcome.excinfo is not None))
    if slot == 0 and outcome.excinfo is None:
        # Cache-only reads of the already-created synthetic fixture object.
        # No Docker request or data/credentials read here. ID is never emitted
        # in traces/artifacts; only a post-exit exact-owned-resource audit uses it.
        try:
            value = outcome.get_result()
            wrapped = vars(value).get("_container")
            attrs = vars(wrapped).get("attrs")
            identifier = attrs.get("Id") if type(attrs) is dict else None
            if type(identifier) is not str or len(identifier) != 64 or any(c not in "0123456789abcdef" for c in identifier):
                raise ValueError("closed ownership shape mismatch")
            fd = os.open(DIRECTORY / ("owned-" + PROCESS + ".id"),
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            try:
                if os.write(fd, identifier.encode("ascii")) != 64:
                    raise ValueError("closed ownership write incomplete")
            finally:
                os.close(fd)
        except Exception:
            global FAULT
            FAULT = True


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    # Read result only; no forcing, skipping, xfail changes or error replacement.
    emit(3, n=NODES.get(item.nodeid), p={"setup": 0, "call": 1, "teardown": 2}[report.when],
         o={"passed": 0, "failed": 1, "skipped": 2}[report.outcome],
         x=0 if call.excinfo is None else 1 if call.excinfo.type is AssertionError else 2 if issubclass(call.excinfo.type, TimeoutError) else 3)


def pytest_sessionfinish(session, exitstatus):
    emit(8, x=int(exitstatus) if 0 <= int(exitstatus) <= 5 else 6,
         c=int(COLLECTED), a=int(TRACED), f=int(FAULT))
    if FD is not None:
        os.close(FD)
