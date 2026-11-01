"""Internal capture input/host epoch guards (REQ-general-capture-001)."""

import uuid

import pytest

from butlers.core.capture import CaptureUnavailable, canonical_intake, load_epoch, rotate_epoch

pytestmark = pytest.mark.unit


def test_input_bounds_and_host_epoch_are_not_reconstructed_from_database(tmp_path):
    path = tmp_path / "epoch.json"
    with pytest.raises(CaptureUnavailable):
        load_epoch(path)
    first = rotate_epoch(path)
    assert load_epoch(path) == first
    second = rotate_epoch(path)
    assert second.generation != first.generation and second.not_before >= first.not_before
    canonical, digest = canonical_intake("x" * 32768)
    assert canonical_intake("x" * 32768) == (canonical, digest)
    for text, refs in [
        ("x" * 32769, None),
        ("é" * 16385, None),
        ("", None),
        ("ok", [{"source_id": str(uuid.uuid4()), "attachment_id": str(uuid.uuid4())}] * 9),
        ("ok", [{"source_id": "https://untrusted.invalid", "attachment_id": "token"}]),
    ]:
        with pytest.raises(CaptureUnavailable) as exc:
            canonical_intake(text, refs)
        assert "untrusted" not in str(exc.value)
    path.unlink()
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(CaptureUnavailable):
        load_epoch(path)
