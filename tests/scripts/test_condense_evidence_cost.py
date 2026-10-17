"""REQ-testing-053: traced proof workers keep exact path semantics at lower observed cost."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from coverage import CoverageData

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_condensation_ledger as consumer  # noqa: E402
import condense_evidence as evidence  # noqa: E402

pytestmark = pytest.mark.unit

SPELLINGS = [
    "",
    ".",
    "plans",
    "plans/",
    "plans/.",
    "plans/..",
    "plans/current.json",
    "plans//current.json",
    "plans/../plans/current.json",
    "plans/absent.json",
    "plans/x.py",
    "plans/link-to-absent",
    "current.json",
    "link-file",
    "chain",
    "dangling",
    "link-dir/current.json",
    "link-dir/absent.json",
    "link-dir/x.py",
    "link-dir/../plans/current.json",
    "missing/../plans/current.json",
    "missing/../plans/absent.json",
    "missing/../plans/link-to-absent",
    "missing/x.py",
    "other.txt",
    "other.txt/x.py",
    "other.txt/../plans/current.json",
    "loop-a",
    "loop-a/x.py",
    "loop-a/current.json",
    "odd-link/current.json",
    "odd-link/absent.json",
    "odd-link/link-to-absent",
    "odd-link/x.py",
]


def _tree(tmp_path):
    root = tmp_path.resolve() / "root"
    (root / "plans").mkdir(parents=True)
    (root / "plans/current.json").write_text("{}")
    (root / "plans/link-to-absent").symlink_to("absent.json")
    (root / "other.txt").write_text("other")
    (root / "link-file").symlink_to("plans/current.json")
    (root / "chain").symlink_to("link-file")
    (root / "link-dir").symlink_to("plans", target_is_directory=True)
    (root / "dangling").symlink_to("plans/absent.json")
    (root / "loop-a").symlink_to("loop-b")
    (root / "loop-b").symlink_to("loop-a")
    # The kernel cannot traverse a missing component; realpath pops it lexically.
    (root / "odd-link").symlink_to("missing/../plans")
    return root


def _outcome(operation):
    try:
        return operation()
    except (OSError, RuntimeError) as exc:
        return type(exc)


def test_control_input_resolution_equals_realpath_membership(tmp_path, monkeypatch):
    """REQ-testing-053: the worker's cheap guard decides exactly as realpath does."""
    root = _tree(tmp_path)
    monkeypatch.chdir(root)
    present, absent = str(root / "plans/current.json"), str(root / "plans/absent.json")
    for targets in (set(), {present}, {absent}, {present, absent}):
        for spelling in SPELLINGS:
            for path in (spelling, str(root / spelling) if spelling else str(root)):
                expected = os.path.realpath(path) in targets
                assert evidence.resolves_to(path, targets) is expected, (path, targets)


def test_setup_realpath_sharing_directories_equals_realpath(tmp_path, monkeypatch):
    """REQ-testing-053: coverage start's shared directory walks resolve every path alike."""
    root = _tree(tmp_path)
    monkeypatch.chdir(root)
    shared = evidence.directory_sharing_realpath(os.path.realpath)
    for _ in range(2):
        for spelling in SPELLINGS:
            for path in (spelling, str(root / spelling), Path(root / spelling)):
                assert shared(path) == os.path.realpath(path), path
                for strict in (False, True):
                    assert _outcome(lambda p=path, s=strict: shared(p, strict=s)) == _outcome(
                        lambda p=path, s=strict: os.path.realpath(p, strict=s)
                    ), (path, strict)


def test_worker_shares_only_coverage_walks_never_the_subject_realpath(tmp_path):
    """REQ-testing-053: coverage's own os.path copy shares walks; the subject's is original."""
    import coverage.files

    assert coverage.files.os.path is not os.path
    root = tmp_path.resolve() / "subject"
    (root / "real").mkdir(parents=True)
    (root / "tests").mkdir()
    # The subject imports through a linked directory, so coverage's include
    # matches only if each canonical name resolves that link exactly.
    (root / "src").symlink_to("real", target_is_directory=True)
    (root / "real/product.py").write_text("def value():\n    return 1\n")
    (root / "tests/test_subject.py").write_text(
        "import os\nimport posixpath\n\nimport coverage.files\nfrom product import value\n\n\n"
        "def test_subject():\n"
        "    assert os.path.realpath is posixpath.realpath\n"
        "    assert coverage.files.os.path.realpath is not posixpath.realpath\n"
        "    assert value() == 1\n"
    )
    output = tmp_path / "run.json"
    request = output.with_suffix(".request.json")
    request.write_text(
        json.dumps(
            {
                "root": str(root),
                "selection": ["tests/test_subject.py::test_subject"],
                "scope": ["real/product.py"],
                "nonce": "0" * 64,
                "output": str(output),
                "id": "run",
            }
        )
    )
    script = Path(evidence.__file__).resolve()
    env = {
        **os.environ,
        "PYTHONPATH": str(script.parent),
        "PYTHONDONTWRITEBYTECODE": "1",
        "COVERAGE_CORE": "ctrace",
    }
    subprocess.run(
        [sys.executable, str(script), "_worker", str(request)],
        cwd=root,
        env=env,
        check=True,
        timeout=120,
    )
    result = json.loads(output.read_text())
    [case] = result["cases"]
    assert case["phases"] == {"setup": "passed", "call": "passed", "teardown": "passed"}
    assert result["complete"] is True
    assert {row[0] for row in result["arcs"][case["key"]]} == {"real/product.py"}

    assert _retargeted_worker_arcs(tmp_path, root) == [
        [path, start, end]
        for path, arcs in [
            ("other/second.py", [(-1, 1), (-1, 2), (1, -1), (2, -1)]),
            ("real/product.py", [(-1, 2), (2, -1)]),
        ]
        for start, end in arcs
    ]


def test_tool_resolution_equals_path_resolve(tmp_path):
    """REQ-testing-053: shared directory resolution never changes a bound tool path."""
    root = _tree(tmp_path)
    directories = {}
    for _ in range(2):
        for spelling in SPELLINGS:
            path = root / spelling
            assert _outcome(lambda p=path: consumer.resolve_in(p, directories)) == _outcome(
                path.resolve
            ), path


def test_request_access_stream_reads_each_actor_identity_once(tmp_path, monkeypatch):
    """REQ-testing-053: a reaped actor's PID is never re-read and possibly reused."""
    request = tmp_path / "run.request.json"
    output = tmp_path / "run.json"
    calls = []

    def identity(pid):
        calls.append(pid)
        return {"pid": pid, "group": 1, "start": "1", "state": "S"} if pid == 41 else None

    monkeypatch.setattr(evidence, "process_identity", identity)
    stream = evidence.RequestAccessStream(
        tmp_path, {"plans/current.json"}, request, output, "0" * 64
    )
    for line in (
        b"41 getpid() = 41",
        b"42 getpid() = 42",
        b"41 getppid() = 1",
        b"42 getppid() = 41",
        b"42 +++ exited with 0 +++",
        b"41 +++ exited with 0 +++",
    ):
        stream.consume(line)
    assert calls == [41, 42]
    assert set(stream.live) == {41}
    assert not stream.failed and stream.exits == {41, 42}


def test_encoded_record_paths_decode_every_byte_alike():
    """REQ-testing-053: strace -xx tokens decode to the same bytes as a hex parse."""

    def encoded(value):
        return "".join(f"\\x{byte:02x}" for byte in value)

    every = bytes(range(256))
    values = [b"/", b"plans/current.json", every, every[::-1], "café/☃".encode()]
    record = ", ".join(
        f'{index}<{encoded(value)}>, "{encoded(value)}"' for index, value in enumerate(values)
    )
    for value in (record, record + ' "" ""..., 0x10, "\\x"', "no tokens", ""):
        expected = [
            bytes.fromhex(token.replace("\\x", ""))
            for token in re.findall(r"(?:\\x[0-9a-f]{2})+", value)
        ]
        assert evidence.RequestAccessStream.paths(value) == expected
    assert evidence.RequestAccessStream.paths(record) == [v for v in values for _ in (0, 1)]


def _public_query_arcs(data, root, keys):
    arcs = {}
    for key in keys:
        rows = set()
        for phase in ("setup", "call", "teardown"):
            data.set_query_context(key + "|" + phase)
            for filename in data.measured_files():
                path = Path(filename).resolve()
                if root in path.parents:
                    rows.update(
                        (str(path.relative_to(root)), a, b) for a, b in (data.arcs(filename) or [])
                    )
        arcs[key] = sorted(rows)
    return arcs


def test_case_arcs_equal_the_public_query_api(tmp_path):
    """REQ-testing-053: one read-only connection returns every case's exact phase arcs."""
    root = tmp_path.resolve() / "root"
    (root / "pkg").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (root / "linked").symlink_to(root / "pkg", target_is_directory=True)
    (tmp_path / "escape").symlink_to(tmp_path / "outside", target_is_directory=True)
    (tmp_path / "into").symlink_to(root / "pkg", target_is_directory=True)
    inside, linked = str(root / "pkg/a.py"), str(root / "linked/b.py")
    outside, escaped = str(tmp_path / "outside/c.py"), str(tmp_path / "escape/d.py")
    entered = str(tmp_path / "into/e.py")
    data = CoverageData(basename=str(tmp_path / "run.coverage"))
    for context, files in {
        "k1|setup": {inside: {(-1, 1), (1, 2)}, outside: {(1, 2)}},
        "k1|call": {inside: {(2, 3)}, linked: {(5, -4)}, escaped: {(7, 8)}},
        "k1|teardown": {linked: {(5, -4)}, entered: {(1, -1)}},
        "k1|calls": {inside: {(9, 10)}},
        "k2|call": {inside: {(2, 4)}, outside: {(3, 4)}},
        "k.|call": {linked: {(6, 7)}},
        "": {inside: {(11, 12)}},
    }.items():
        data.set_context(context)
        data.add_arcs(files)
    data.write()
    keys = ["k1", "k2", "k.", "k3"]
    expected = _public_query_arcs(data, root, keys)
    assert expected["k1"] == [
        ("pkg/a.py", -1, 1),
        ("pkg/a.py", 1, 2),
        ("pkg/a.py", 2, 3),
        ("pkg/b.py", 5, -4),
        ("pkg/e.py", 1, -1),
    ]
    assert expected["k."] == [("pkg/b.py", 6, 7)]
    assert expected["k3"] == []
    assert evidence.case_arcs(data, root, keys) == expected
    assert evidence.case_arcs(data, root, []) == {}


def test_owned_launcher_execs_only_after_the_go_byte():
    """REQ-testing-053: the import-free launcher keeps the go-byte admission."""
    command = [sys.executable, "-I", "-c", evidence.LAUNCHER, sys.executable, "-c", "exit(7)"]
    for signal, code in ((b"1", 7), (b"0", 2), (b"", 2)):
        assert subprocess.run(command, input=signal, timeout=30).returncode == code


def _retargeted_worker_arcs(tmp_path, root):
    # A new filename under the same parent must use that parent's current
    # target. Restore the link before source readback, as a real subject may do.
    (root / "other").mkdir()
    (root / "other/second.py").write_text("def value():\n    return 2\n")
    subject = root / "tests/test_subject.py"
    subject.write_text(
        subject.read_text()
        + "    import importlib.util\n"
        + "    from pathlib import Path\n"
        + "    root = Path(__file__).resolve().parents[1]\n"
        + "    (root / 'src').unlink()\n"
        + "    (root / 'src').symlink_to('other', target_is_directory=True)\n"
        + "    try:\n"
        + "        spec = importlib.util.spec_from_file_location('second', root / 'src/second.py')\n"
        + "        second = importlib.util.module_from_spec(spec)\n"
        + "        spec.loader.exec_module(second)\n"
        + "        assert second.value() == 2\n"
        + "    finally:\n"
        + "        (root / 'src').unlink()\n"
        + "        (root / 'src').symlink_to('real', target_is_directory=True)\n"
    )
    node = "tests/test_subject.py::test_subject"
    scope = ["real/product.py", "other/second.py"]
    plan = {
        "base": "event-base",
        "scope": scope,
        "removed": [node],
        "survivors": [node],
        "cluster": "fresh-covered-parent",
        "bead": "bu-1kw4n9",
        "contract": {"class": "wire", "cites": ["REQ-testing-053"]},
        "mapping": {node: {"survivors": [node], "reason": "Retain both executed files"}},
    }
    plans = root / "tests/condensation-plans"
    plans.mkdir()
    (plans / "current.json").write_text(json.dumps(plan))
    consumer.validate_execution_request(plan)
    names = [*scope, "src", "tests/test_subject.py", "tests/condensation-plans/current.json"]
    before = consumer.source_records(root, names)
    controls = consumer.execution_request_inputs(root, before)
    assert controls == {"tests/condensation-plans/current.json"}
    observed_output = tmp_path / "observed.json"
    observed = evidence.suite(
        root, [node], scope, "1" * 64, observed_output, "observed", 60, controls
    )
    assert observed["complete"] and observed["cleanup"] and observed["exit"] == 0
    assert observed["tracer"] == "CTracer"
    assert observed["selected"] == 1 and observed["deselected"] == 0
    [observed_case] = observed["cases"]
    assert observed_case["phases"] == {"setup": "passed", "call": "passed", "teardown": "passed"}
    access = observed["request_access"]
    assert access["complete"] and access["accesses"] == access["reads"] == 0
    assert access["probe_accesses"] == access["subject_probes"] == 1
    assert access["actors"] and all(actor["exited"] for actor in access["actors"])
    assert access["finalization_failures"] == []
    assert observed["request_boundary"]["runtime_read"] is False
    assert consumer.source_records(root, names) == before
    assert os.readlink(root / "src") == "real"
    assert (
        consumer.coverage_arcs(observed_output.with_suffix(".coverage"), observed)
        == observed["arcs"]
    )
    return observed["arcs"][observed_case["key"]]
