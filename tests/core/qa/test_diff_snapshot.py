"""The diff snapshot derived from a sealed export matches git's, with no git over pod state."""

from __future__ import annotations

import hashlib
import shutil
import zlib
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

    assert any("Binary file not shown: blob.bin" in line["text"] for line in snapshot)
    assert ("+", "now text") not in _derived_lines(snapshot)


async def test_an_oversize_file_and_a_file_flood_are_bounded(packed_base, monkeypatch) -> None:
    pod, trusted, base = packed_base
    commit_file(pod, "src/pkg/mod.py", "x" * 500 + "\n", "big line")
    for n in range(5):
        commit_file(pod, f"many/f{n}.txt", f"{n}\n", "many")
    export = await _export(pod, base)
    monkeypatch.setattr(diff_snapshot, "MAX_DISPLAY_BYTES", 100)
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

    export = await dispatch._seal_isolated_export(pod, "qa/finance/abc-1", base, QA_IDENTITY)
    assert export is not None
    snapshot = await dispatch._isolated_diff_snapshot(trusted, export)
    assert ("+", "c = 31") in _derived_lines(snapshot)

    # No bound identity or base, or an unknown branch: no export, so the old empty snapshot.
    assert await dispatch._seal_isolated_export(pod, "qa/finance/abc-1", base, None) is None
    assert await dispatch._seal_isolated_export(pod, "qa/finance/abc-1", None, QA_IDENTITY) is None
    assert await dispatch._seal_isolated_export(pod, "qa/none/x-1", base, QA_IDENTITY) is None
    assert await dispatch._isolated_diff_snapshot(trusted, None) == []


def _loose(repo: Path, kind: str, body: bytes) -> str:
    """Write one loose object directly (fast, and independent of git's own checks)."""
    raw = f"{kind} {len(body)}\0".encode() + body
    oid = hashlib.sha1(raw, usedforsecurity=False).hexdigest()
    target = repo / ".git" / "objects" / oid[:2] / oid[2:]
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(zlib.compress(raw))
    return oid


def _nested_head(repo: Path, base: str, depth: int) -> str:
    """A head commit whose tree nests ``depth`` trees deep around one new file."""
    leaf = _loose(repo, "blob", b"deep = True\n")
    tree = _loose(repo, "tree", b"100644 leaf.py\0" + bytes.fromhex(leaf))
    for _ in range(depth):
        tree = _loose(repo, "tree", b"40000 d\0" + bytes.fromhex(tree))
    who = f"{QA_IDENTITY.name} <{QA_IDENTITY.email}> 1700000000 +0000"
    body = f"tree {tree}\nparent {base}\nauthor {who}\ncommitter {who}\n\ndeep\n"
    return _loose(repo, "commit", body.encode())


async def test_a_deeply_nested_export_is_refused_not_a_recursion_error(packed_base) -> None:
    from butlers.core.qa import dispatch
    from butlers.core.qa.publication.export import ExportRefused

    pod, trusted, base = packed_base
    head = _nested_head(pod, base, 5_000)
    git(pod, "checkout", "-q", "-b", "qa/finance/deep-1")
    (pod / ".git" / "refs" / "heads" / "qa" / "finance" / "deep-1").write_text(head + "\n")

    # The sealer itself refuses, instead of exhausting the stack on the publish path too.
    with pytest.raises(ExportRefused, match="tree_too_deep"):
        await build_sealed_export(
            pod / ".git" / "objects", base_sha=base, head_sha=head, identity=QA_IDENTITY
        )
    # So dispatch has no export: an empty snapshot, and the attempt carries on.
    assert await dispatch._seal_isolated_export(pod, "qa/finance/deep-1", base, QA_IDENTITY) is None
    assert await dispatch._isolated_diff_snapshot(trusted, None) == []


async def test_the_walk_is_iterative_and_refuses_past_its_depth_cap(
    packed_base, monkeypatch
) -> None:
    from butlers.core.qa.publication import export as export_module

    pod, trusted, base = packed_base
    head = _nested_head(pod, base, 5_000)
    # Raise the sealer's cap so a 5,000-deep export exists, then prove the derivation neither
    # recurses (the default recursion limit is ~1,000) nor follows an uncapped depth.
    monkeypatch.setattr(export_module, "MAX_TREE_DEPTH", 10_000)
    export = await build_sealed_export(
        pod / ".git" / "objects", base_sha=base, head_sha=head, identity=QA_IDENTITY
    )
    objects_dir = trusted / ".git" / "objects"

    assert await derive_diff_snapshot(export, objects_dir) == []  # past the cap: refused

    monkeypatch.setattr(diff_snapshot, "MAX_TREE_DEPTH", 10_000)
    snapshot = await derive_diff_snapshot(export, objects_dir)
    assert ("+", "deep = True") in _derived_lines(snapshot)


async def test_a_tampered_base_object_is_refused(tmp_path: Path, caplog) -> None:
    pod = tmp_path / "pod"
    base = init_repo(pod)
    commit_file(pod, "src/pkg/mod.py", "a = 1\n", "mod")
    base = git(pod, "rev-parse", "HEAD").strip()
    trusted = tmp_path / "trusted"
    shutil.copytree(pod, trusted)
    blob = git(pod, "rev-parse", f"{base}:src/pkg/mod.py").strip()
    loose = trusted / ".git" / "objects" / blob[:2] / blob[2:]
    loose.chmod(0o644)
    forged = b"blob 8\0a = 666\n"
    loose.write_bytes(zlib.compress(forged))  # valid zlib, wrong content for this id
    commit_file(pod, "src/pkg/mod.py", "a = 2\n", "edit")

    snapshot = await derive_diff_snapshot(await _export(pod, base), trusted / ".git" / "objects")

    assert snapshot == []
    assert "object_hash_mismatch" in caplog.text
    assert "666" not in caplog.text


async def test_a_missing_base_blob_is_labelled_unavailable(tmp_path: Path) -> None:
    pod = tmp_path / "pod"
    init_repo(pod)
    commit_file(pod, "src/pkg/mod.py", "a = 1\n", "mod")
    base = git(pod, "rev-parse", "HEAD").strip()
    trusted = tmp_path / "trusted"
    shutil.copytree(pod, trusted)  # loose objects, so one blob can be removed on its own
    blob = git(pod, "rev-parse", f"{base}:src/pkg/mod.py").strip()
    gone = trusted / ".git" / "objects" / blob[:2] / blob[2:]
    gone.chmod(0o644)
    gone.unlink()
    commit_file(pod, "src/pkg/mod.py", "a = 2\n", "edit")

    snapshot = await derive_diff_snapshot(await _export(pod, base), trusted / ".git" / "objects")

    texts = [line["text"] for line in snapshot]
    assert "Base version unavailable: src/pkg/mod.py" in texts
    assert not any("Binary" in t or "too large" in t for t in texts)


async def test_rendering_stops_at_the_line_cap(packed_base, monkeypatch) -> None:
    pod, trusted, base = packed_base
    for n in range(6):
        commit_file(pod, f"many/f{n}.txt", "".join(f"row {i}\n" for i in range(50)), "many")
    export = await _export(pod, base)
    monkeypatch.setattr(diff_snapshot, "MAX_DIFF_LINES", 120)

    snapshot = await derive_diff_snapshot(export, trusted / ".git" / "objects")

    assert snapshot[-1]["text"].startswith("...")
    assert sum(line["text"].startswith("diff --git") for line in snapshot) < 6


async def test_the_publisher_reuses_the_dispatchers_sealed_export(tmp_path: Path, monkeypatch):
    """One seal per attempt: a supplied export is neither rebuilt nor re-resolved."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from butlers.core.qa.publication import proposal
    from tests.core.qa.publication_fakes import synthetic_export

    export = synthetic_export()
    publisher = MagicMock()
    publisher.repository = "owner/repo"
    publisher.publish = AsyncMock(return_value="published")
    monkeypatch.setattr(proposal, "resolve_branch_head", MagicMock(side_effect=AssertionError))
    monkeypatch.setattr(proposal, "build_sealed_export", AsyncMock(side_effect=AssertionError))
    monkeypatch.setattr(proposal, "compose_pr_text", MagicMock(return_value=("t", "b")))
    monkeypatch.setattr(proposal, "load_pr_notes", MagicMock(return_value={}))

    result = await proposal.publish_investigation(
        publisher,
        repo_root=tmp_path,
        worktree_path=tmp_path,
        attempt_id=uuid.uuid4(),
        finding=MagicMock(),
        branch_name="qa/finance/abc-1",
        base_sha="b" * 40,
        identity=QA_IDENTITY,
        labels=[],
        whitelist=None,
        dashboard_base_url=None,
        sealed_export=export,
    )

    assert result == "published"
    assert publisher.publish.await_args.kwargs["export"] is export
    assert publisher.publish.await_args.kwargs["binding"].head_sha == export.head_sha


async def test_one_huge_file_cannot_overshoot_the_line_cap(packed_base, monkeypatch) -> None:
    pod, trusted, base = packed_base
    commit_file(pod, "big/one.txt", "".join(f"row {i}\n" for i in range(500)), "one big file")
    export = await _export(pod, base)
    monkeypatch.setattr(diff_snapshot, "MAX_DIFF_LINES", 60)

    snapshot = await derive_diff_snapshot(export, trusted / ".git" / "objects")

    assert len(snapshot) <= 61 and snapshot[-1]["text"].startswith("...")


async def test_a_seal_time_refusal_is_info_and_not_a_counted_failure(caplog) -> None:
    import logging
    from unittest.mock import MagicMock, patch

    from butlers.core.qa.publication.export import ExportRefused

    counter = MagicMock()
    with patch.object(diff_snapshot, "_failures", counter), caplog.at_level(logging.INFO):
        diff_snapshot.record_snapshot_failure("seal", ExportRefused("content_leak"))
        diff_snapshot.record_snapshot_failure("seal", RuntimeError("boom"))

    counter.add.assert_called_once()  # only the genuine error is counted
    levels = {r.levelname for r in caplog.records if "content_leak" in r.getMessage()}
    assert levels == {"INFO"}


async def test_a_supplied_export_for_another_base_is_refused(tmp_path: Path, monkeypatch) -> None:
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from butlers.core.qa.publication import proposal
    from tests.core.qa.publication_fakes import synthetic_export

    export = synthetic_export()  # base "b" * 40
    publisher = MagicMock()
    publisher.record_refusal = AsyncMock(side_effect=lambda _attempt, result: result)
    publisher.repository = "owner/repo"

    result = await proposal.publish_investigation(
        publisher,
        repo_root=tmp_path,
        worktree_path=tmp_path,
        attempt_id=uuid.uuid4(),
        finding=MagicMock(),
        branch_name="qa/finance/abc-1",
        base_sha="a" * 40,
        identity=QA_IDENTITY,
        labels=[],
        whitelist=None,
        dashboard_base_url=None,
        sealed_export=export,
    )

    assert result.category == "unbound_export"
    publisher.publish.assert_not_called()


async def test_retain_unpublished_proposal_reuses_the_sealed_export(tmp_path: Path, monkeypatch):
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from butlers.core.qa.publication import proposal
    from tests.core.qa.publication_fakes import synthetic_export

    export = synthetic_export()
    retain = AsyncMock()
    monkeypatch.setattr(proposal.retention, "retain_export", retain)
    monkeypatch.setattr(proposal, "resolve_branch_head", MagicMock(side_effect=AssertionError))
    monkeypatch.setattr(proposal, "build_sealed_export", AsyncMock(side_effect=AssertionError))

    result = await proposal.retain_unpublished_proposal(
        MagicMock(),
        repo_root=tmp_path,
        attempt_id=uuid.uuid4(),
        branch_name="qa/finance/abc-1",
        base_sha="b" * 40,
        identity=QA_IDENTITY,
        sealed_export=export,
    )

    assert result is None
    assert retain.await_args.args[2] is export
