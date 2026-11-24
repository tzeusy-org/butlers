"""The diff snapshot derived from a sealed export matches git's, with no git over pod state."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from butlers.core.qa.diff import parse_unified_diff
from butlers.core.qa.publication import diff_snapshot
from butlers.core.qa.publication.diff_snapshot import derive_diff_snapshot
from butlers.core.qa.publication.export import SealedExport, build_sealed_export
from tests.core.qa.publication_fakes import QA_IDENTITY, commit_file, git, init_repo

pytestmark = pytest.mark.unit

_BIG = "".join(f"line {n} of a longer file\n" for n in range(400))


def _git_lines(repo: Path, base: str, head: str) -> list[tuple[str, str]]:
    raw = git(repo, "diff", "--no-color", "--no-ext-diff", "--unified=3", f"{base}..{head}")
    return [(line.kind, line.text) for line in parse_unified_diff(raw) if line.kind != "meta"]


def _derived_lines(snapshot: list[dict[str, str]]) -> list[tuple[str, str]]:
    return [(line["kind"], line["text"]) for line in snapshot if line["kind"] != "meta"]


async def _export(repo: Path, base: str) -> SealedExport:
    head = git(repo, "rev-parse", "HEAD").strip()
    return await build_sealed_export(
        repo / ".git" / "objects", base_sha=base, head_sha=head, identity=QA_IDENTITY
    )


@pytest.fixture
def packed_base(tmp_path: Path) -> tuple[Path, Path, str]:
    """A pod clone and a trusted copy of the base after ``git gc`` (so base blobs are packed)."""
    pod = tmp_path / "pod"
    base = init_repo(pod)
    commit_file(pod, "docs/guide.md", _BIG, "guide")
    commit_file(pod, "src/pkg/mod.py", "a = 1\nb = 2\nc = 3\n", "mod")
    commit_file(pod, "src/old.py", "legacy = True\n", "old")
    commit_file(pod, "gone/one.txt", "one\n", "gone one")
    commit_file(pod, "gone/two.txt", "two\n", "gone two")
    base = git(pod, "rev-parse", "HEAD").strip()
    git(pod, "gc", "-q", "--aggressive")
    trusted = tmp_path / "trusted"
    shutil.copytree(pod, trusted)
    assert not list((trusted / ".git" / "objects").glob("??/*")), "base blobs must be packed"
    return pod, trusted, base


async def test_the_derived_snapshot_matches_git_over_a_packed_base(packed_base) -> None:
    pod, trusted, base = packed_base
    commit_file(pod, "src/pkg/mod.py", "a = 1\nb = 20\nc = 3\nd = 4\n", "edit")
    commit_file(pod, "docs/guide.md", _BIG.replace("line 200 ", "LINE 200 "), "guide edit")
    commit_file(pod, "src/new/fresh.py", "def fresh():\n    return 1\n", "add")
    git(pod, "rm", "-q", "src/old.py")
    git(pod, "rm", "-q", "-r", "gone")
    git(pod, "commit", "-q", "-m", "delete")
    head = git(pod, "rev-parse", "HEAD").strip()

    snapshot = await derive_diff_snapshot(await _export(pod, base), trusted / ".git" / "objects")

    assert _derived_lines(snapshot) == _git_lines(pod, base, head)
    headers = {line["text"] for line in snapshot if line["text"].startswith("diff --git")}
    assert headers == {
        "diff --git a/docs/guide.md b/docs/guide.md",
        "diff --git a/src/pkg/mod.py b/src/pkg/mod.py",
        "diff --git a/src/new/fresh.py b/src/new/fresh.py",
        "diff --git a/src/old.py b/src/old.py",
        "diff --git a/gone/one.txt b/gone/one.txt",
        "diff --git a/gone/two.txt b/gone/two.txt",
    }


async def test_the_pod_clone_is_never_read_for_base_content(packed_base) -> None:
    pod, trusted, base = packed_base
    commit_file(pod, "src/pkg/mod.py", "a = 1\nb = 2\nc = 30\n", "edit")
    export = await _export(pod, base)
    shutil.rmtree(pod)  # nothing the pod wrote is needed once the export is sealed

    snapshot = await derive_diff_snapshot(export, trusted / ".git" / "objects")

    assert ("+", "c = 30") in _derived_lines(snapshot)


async def test_a_binary_base_side_is_a_placeholder_not_content(packed_base) -> None:
    pod, trusted, base = packed_base
    binary = pod / "blob.bin"
    binary.write_bytes(b"\x00\x01\x02binary")
    git(pod, "add", "blob.bin")
    git(pod, "commit", "-q", "-m", "binary")
    base = git(pod, "rev-parse", "HEAD").strip()
    git(pod, "gc", "-q")
    shutil.rmtree(trusted)
    shutil.copytree(pod, trusted)
    commit_file(pod, "blob.bin", "now text\n", "to text")

    snapshot = await derive_diff_snapshot(await _export(pod, base), trusted / ".git" / "objects")

    assert any("Binary or oversize file not shown: blob.bin" in line["text"] for line in snapshot)
    assert ("+", "now text") not in _derived_lines(snapshot)


async def test_an_oversize_file_and_a_file_flood_are_bounded(packed_base, monkeypatch) -> None:
    pod, trusted, base = packed_base
    commit_file(pod, "src/pkg/mod.py", "x" * 500 + "\n", "big line")
    for n in range(5):
        commit_file(pod, f"many/f{n}.txt", f"{n}\n", "many")
    export = await _export(pod, base)
    monkeypatch.setattr(diff_snapshot, "MAX_BLOB_BYTES", 100)
    monkeypatch.setattr(diff_snapshot, "MAX_DIFF_FILES", 2)

    snapshot = await derive_diff_snapshot(export, trusted / ".git" / "objects")

    texts = [line["text"] for line in snapshot]
    assert sum(t.startswith("diff --git") for t in texts) == 2
    assert any("more changed files not shown" in t for t in texts)


async def test_any_refusal_gives_an_empty_snapshot(packed_base, tmp_path, caplog) -> None:
    pod, _trusted, base = packed_base
    commit_file(pod, "src/pkg/mod.py", "a = 2\n", "edit")
    export = await _export(pod, base)

    # A base store that lacks the base commit cannot produce a diff; display never gates.
    empty = tmp_path / "empty" / ".git" / "objects"
    empty.mkdir(parents=True)
    assert await derive_diff_snapshot(export, empty) == []
    assert "Could not derive the QA diff snapshot" in caplog.text


async def test_an_isolated_attempt_records_its_diff_without_running_any_git(
    packed_base, monkeypatch
) -> None:
    from butlers.core.qa import dispatch

    pod, trusted, base = packed_base
    git(pod, "checkout", "-q", "-b", "qa/finance/abc-1")
    commit_file(pod, "src/pkg/mod.py", "a = 1\nb = 2\nc = 31\n", "edit")

    async def no_subprocess(*_args, **_kwargs):
        raise AssertionError("an isolated attempt must not spawn git over the pod clone")

    monkeypatch.setattr(dispatch.asyncio, "create_subprocess_exec", no_subprocess)

    snapshot = await dispatch._isolated_diff_snapshot(
        trusted, pod, "qa/finance/abc-1", base, QA_IDENTITY
    )
    assert ("+", "c = 31") in _derived_lines(snapshot)

    # No bound identity or base means the export cannot be proven: the old empty snapshot.
    assert (
        await dispatch._isolated_diff_snapshot(trusted, pod, "qa/finance/abc-1", base, None) == []
    )
    assert (
        await dispatch._isolated_diff_snapshot(trusted, pod, "qa/finance/abc-1", None, QA_IDENTITY)
        == []
    )
    # A refused export (unknown branch) is also empty, never an exception.
    assert (
        await dispatch._isolated_diff_snapshot(trusted, pod, "qa/none/x-1", base, QA_IDENTITY) == []
    )
