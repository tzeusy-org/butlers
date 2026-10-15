"""REQ-testing-053: proof workers bind the same installed tool files with fewer observed calls."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_condensation_ledger as consumer  # noqa: E402

pytestmark = pytest.mark.unit


def _reference_file(path, prefixes):
    """The binding before the cut: is_file, a read loop to EOF, then a second stat."""
    if not path.is_file() or not any(prefix in path.parents for prefix in prefixes):
        raise consumer.EvidenceError("installed-tool-source-unknown")
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY)
    try:
        while chunk := os.read(descriptor, 1 << 20):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest(), path.stat().st_mode & 0o777


def _reference_record():
    names = {"coverage", "pytest"}
    names.update(
        entry.dist.metadata["Name"]
        for entry in importlib.metadata.entry_points(group="pytest11")
        if entry.dist is not None
    )
    rows = []
    prefixes = {Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()}
    for name in sorted(names):
        dist = importlib.metadata.distribution(name)
        files = dist.files
        assert files
        for item in sorted(files, key=str):
            if item.suffix not in {".py", ".so"} and item.name not in {
                "METADATA",
                "entry_points.txt",
            }:
                continue
            sha256, mode = _reference_file(Path(dist.locate_file(item)).resolve(), prefixes)
            rows.append({"distribution": name, "path": str(item), "sha256": sha256, "mode": mode})
    observer = shutil.which("strace")
    if observer is not None:
        path = Path(observer).resolve(strict=True)
        rows.append(
            {
                "distribution": "owned-request-access-observer",
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "mode": path.stat().st_mode & 0o777,
            }
        )
    return {
        "python": sys.version,
        "pytest": importlib.metadata.version("pytest"),
        "coverage": importlib.metadata.version("coverage"),
        "closure": consumer.canonical(rows),
    }


def _outcome(operation):
    try:
        return operation()
    except consumer.EvidenceError as exc:
        return ("refused", str(exc))
    except OSError as exc:
        return (type(exc), exc.errno)


def test_tool_record_equals_the_full_read_binding():
    """REQ-testing-053: the installed tool closure is unchanged by one stat and one read."""
    assert consumer.tool_record() == _reference_record()


def test_tool_file_equals_the_full_read_binding_for_every_entry_kind(tmp_path):
    """REQ-testing-053: each spelling binds, or refuses, exactly as before."""
    root = tmp_path.resolve()
    (root / "empty.py").write_bytes(b"")
    (root / "small.py").write_bytes(b"value = 1\n")
    # Several of the old 1 MiB chunks, plus a ragged tail.
    (root / "large.so").write_bytes(os.urandom((3 << 20) + 17))
    (root / "exact.so").write_bytes(os.urandom(1 << 20))
    (root / "private.py").write_bytes(b"x = 2\n")
    (root / "private.py").chmod(0o600)
    (root / "package").mkdir()
    os.mkfifo(root / "pipe.py")
    (root / "loop-a.py").symlink_to("loop-b.py")
    (root / "loop-b.py").symlink_to("loop-a.py")
    (root / "linked.py").symlink_to("small.py")
    spellings = [
        "empty.py",
        "small.py",
        "large.so",
        "exact.so",
        "private.py",
        "linked.py",
        "package",
        "pipe.py",
        "absent.py",
        "small.py/child.py",
        "loop-a.py",
    ]
    for prefixes in ({root}, {root / "elsewhere"}, set()):
        for spelling in spellings:
            path = root / spelling
            assert _outcome(lambda p=path, s=prefixes: consumer.tool_file(p, s)) == _outcome(
                lambda p=path, s=prefixes: _reference_file(p, s)
            ), (spelling, prefixes)
    assert consumer.tool_file(root / "private.py", {root})[1] == 0o600


@pytest.mark.parametrize("change", [b"grown tail", None])
def test_tool_file_reads_to_the_end_of_a_file_changed_after_its_stat(tmp_path, monkeypatch, change):
    """REQ-testing-053: a size that went stale still hashes the bytes actually read to EOF."""
    path = tmp_path.resolve() / "tool.py"
    path.write_bytes(b"0123456789" * 50)
    original = Path.stat

    def stat_then_change(self, *args, **kwargs):
        info = original(self, *args, **kwargs)
        if self == path:
            if change is None:
                with open(path, "r+b") as handle:
                    handle.truncate(123)
            else:
                with open(path, "ab") as handle:
                    handle.write(change)
        return info

    monkeypatch.setattr(Path, "stat", stat_then_change)
    sha256, _ = consumer.tool_file(path, {tmp_path.resolve()})
    monkeypatch.setattr(Path, "stat", original)
    assert sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_tool_file_makes_one_stat_and_one_read(tmp_path, monkeypatch):
    """REQ-testing-053: a file read whole at its stat size pays no second stat or EOF read."""
    path = tmp_path.resolve() / "tool.py"
    path.write_bytes(b"value = 1\n")
    calls = []
    stat_call, read_call = os.stat, os.read

    def counted_stat(target, *args, **kwargs):
        calls.append(("stat", os.fspath(target)))
        return stat_call(target, *args, **kwargs)

    def counted_read(descriptor, size):
        calls.append(("read", size))
        return read_call(descriptor, size)

    prefixes = {tmp_path.resolve()}
    monkeypatch.setattr(os, "stat", counted_stat)
    monkeypatch.setattr(os, "read", counted_read)
    result = consumer.tool_file(path, prefixes)
    monkeypatch.undo()
    assert calls == [("stat", str(path)), ("read", 11)]
    assert result == _reference_file(path, prefixes)
