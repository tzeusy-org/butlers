"""Immutable image handoff controls, independent of native/provider eligibility."""

import errno
import hashlib
import os

import pytest

from butlers.session_host.bubblewrap import ImageFile
from butlers.session_host.contracts import Refused


def image(path):
    path.write_bytes(b"approved-image-bytes")
    path.chmod(0o444)
    return ImageFile(path, "/runtime/program", hashlib.sha256(path.read_bytes()).hexdigest())


def test_snapshot_is_kernel_immutable_and_pins_verified_bytes(tmp_path):
    entry = image(tmp_path / "program")
    descriptor = entry.open()
    try:
        entry.source.chmod(0o644)
        entry.source.write_bytes(b"changed-after-verification")
        with pytest.raises(OSError) as write:
            os.pwrite(descriptor, b"changed", 0)
        assert write.value.errno == errno.EPERM
        with pytest.raises(OSError) as truncate:
            os.ftruncate(descriptor, 0)
        assert truncate.value.errno == errno.EPERM
        assert os.pread(descriptor, 65536, 0) == b"approved-image-bytes"
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("kind", ["writable", "hardlink", "symlink", "ancestor", "special", "fifo"])
def test_unsafe_image_sources_refuse_before_handoff(tmp_path, kind):
    entry = image(tmp_path / "program")
    if kind == "writable":
        entry.source.chmod(0o644)
    elif kind == "hardlink":
        os.link(entry.source, tmp_path / "alias")
    elif kind == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(entry.source)
        entry = ImageFile(alias, entry.destination, entry.sha256)
    elif kind == "ancestor":
        alias = tmp_path / "linked-directory"
        alias.symlink_to(tmp_path, target_is_directory=True)
        entry = ImageFile(alias / "program", entry.destination, entry.sha256)
    elif kind == "special":
        entry.source.chmod(0o4444)
    elif kind == "fifo":
        entry.source.unlink()
        os.mkfifo(entry.source, 0o444)
    with pytest.raises(Refused):
        entry.open()


def test_in_place_mutation_during_snapshot_refuses(tmp_path, monkeypatch):
    entry = image(tmp_path / "program")
    original_read = os.read
    mutated = False

    def read_then_mutate(descriptor, size):
        nonlocal mutated
        data = original_read(descriptor, size)
        if data and not mutated:
            mutated = True
            entry.source.chmod(0o644)
            entry.source.write_bytes(b"changed-source-bytes")
            entry.source.chmod(0o444)
        return data

    monkeypatch.setattr("butlers.session_host.bubblewrap.os.read", read_then_mutate)
    with pytest.raises(Refused, match="input_changed_during_snapshot"):
        entry.open()
    assert mutated
