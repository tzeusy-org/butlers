"""The sealed export reads only the agent repository's own object files (bu-mc01y5.26).

``.git/objects`` is agent-writable.  Re-hashing stops byte substitution, but the
reader must also never be steered into opening anything outside that directory:
planted ``info/alternates``/``info/http-alternates``, symlinked objects, fan-out
directories, packs or the object directory itself, and FIFOs (which would hang an
``open``) must all refuse, and no git process may run against the agent store.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import zlib
from pathlib import Path

import pytest

from butlers.core.qa.publication import export as export_module
from butlers.core.qa.publication.export import ExportRefused, SealedExport, build_sealed_export
from tests.core.qa.publication_fakes import QA_IDENTITY, commit_file, git, init_repo

# A planted FIFO or a hung reader must fail the test, not the whole run.
_DEADLINE_S = 10


async def _export(repo: Path, base: str, head: str) -> SealedExport:
    return await asyncio.wait_for(
        build_sealed_export(
            repo / ".git" / "objects", base_sha=base, head_sha=head, identity=QA_IDENTITY
        ),
        _DEADLINE_S,
    )


def _loose(repo: Path, oid: str) -> Path:
    return repo / ".git" / "objects" / oid[:2] / oid[2:]


def _blob(repo: Path, head: str, path: str) -> str:
    return git(repo, "rev-parse", f"{head}:{path}")


def _outside_blob(tmp_path: Path, content: str) -> tuple[Path, str]:
    """A repository the agent does not own, holding one blob the agent never wrote."""
    outside = tmp_path / "outside"
    outside.mkdir()
    git(outside, "init", "-q")
    return outside, git(outside, "hash-object", "-w", "--stdin", input=content)


def _commit_referencing(repo: Path, oid: str, path: str) -> str:
    git(repo, "update-index", "--add", "--cacheinfo", f"100644,{oid},{path}")
    git(repo, "commit", "-q", "--no-verify", "-m", "fix: add notes")
    return git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("indirection", ["alternates", "http-alternates"])
async def test_planted_alternates_refuse_instead_of_reading_another_store(
    tmp_path: Path, indirection: str
) -> None:
    repo = tmp_path / "repo"
    base = init_repo(repo)
    outside, foreign = _outside_blob(tmp_path, "value = 'outside the agent store'\n")
    info = repo / ".git" / "objects" / "info"
    info.mkdir(exist_ok=True)
    target = str(outside / ".git" / "objects")
    if indirection == "http-alternates":
        target = f"file://{target}"
    (info / indirection).write_text(target + "\n")
    # Commit while the alternate is live so git accepts the foreign object id.
    (info / "alternates").write_text(str(outside / ".git" / "objects") + "\n")
    head = _commit_referencing(repo, foreign, "notes.txt")
    if indirection == "http-alternates":
        (info / "alternates").unlink()
    assert not _loose(repo, foreign).exists()

    with pytest.raises(ExportRefused) as refused:
        await _export(repo, base, head)

    assert refused.value.code == "object_store_unsafe"


async def test_symlinked_loose_object_refuses(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = init_repo(repo)
    head = commit_file(repo, "src/app.py", "value = 2\n", "fix")
    loose = _loose(repo, _blob(repo, head, "src/app.py"))
    elsewhere = tmp_path / "elsewhere"
    shutil.move(loose, elsewhere)
    loose.symlink_to(elsewhere)

    with pytest.raises(ExportRefused, match="object_store_unsafe"):
        await _export(repo, base, head)


async def test_symlinked_fanout_directory_refuses(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = init_repo(repo)
    head = commit_file(repo, "src/app.py", "value = 2\n", "fix")
    fanout = _loose(repo, _blob(repo, head, "src/app.py")).parent
    elsewhere = tmp_path / "fanout-elsewhere"
    shutil.move(fanout, elsewhere)
    fanout.symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(ExportRefused, match="object_store_unsafe"):
        await _export(repo, base, head)


async def test_symlinked_object_directory_refuses(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = init_repo(repo)
    head = commit_file(repo, "src/app.py", "value = 2\n", "fix")
    objects = repo / ".git" / "objects"
    elsewhere = tmp_path / "objects-elsewhere"
    shutil.move(objects, elsewhere)
    objects.symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(ExportRefused, match="object_store_unsafe"):
        await _export(repo, base, head)


async def test_fifo_in_place_of_a_loose_object_refuses_without_hanging(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = init_repo(repo)
    head = commit_file(repo, "src/app.py", "value = 2\n", "fix")
    loose = _loose(repo, _blob(repo, head, "src/app.py"))
    loose.unlink()
    os.mkfifo(loose)

    with pytest.raises(ExportRefused, match="object_store_unsafe"):
        await _export(repo, base, head)


def _repack(repo: Path, *, ref_delta: bool = False) -> None:
    config = ["-c", f"repack.useDeltaBaseOffset={'false' if ref_delta else 'true'}"]
    git(repo, *config, "repack", "-a", "-d", "-f", "-q", "--depth=50", "--window=50")
    git(repo, "prune-packed")


def _packs(repo: Path) -> list[Path]:
    return sorted((repo / ".git" / "objects" / "pack").glob("pack-*.*"))


@pytest.mark.parametrize("ref_delta", [False, True])
async def test_packed_and_deltified_objects_export_from_the_store(
    tmp_path: Path, ref_delta: bool
) -> None:
    repo = tmp_path / "repo"
    base = init_repo(repo)
    lines = [f"value_{index} = {index}\n" for index in range(400)]
    commit_file(repo, "src/big.py", "".join(lines), "feat: add table")
    lines[200] = "value_200 = 'changed'\n"
    head = commit_file(repo, "src/big.py", "".join(lines), "fix: change one row")
    loose_export = await _export(repo, base, head)

    _repack(repo, ref_delta=ref_delta)
    assert not any(p.is_dir() and len(p.name) == 2 for p in (repo / ".git" / "objects").iterdir())
    index = next(p for p in _packs(repo) if p.suffix == ".idx")
    # verify-pack prints "<oid> <type> <size> <packed> <offset> <depth> <base>" for deltas.
    entries = [line.split() for line in git(repo, "verify-pack", "-v", str(index)).splitlines()]
    assert any(len(fields) == 7 for fields in entries)

    packed_export = await _export(repo, base, head)

    packed_export.verify()
    assert packed_export == loose_export


@pytest.mark.parametrize("suffix", [".idx", ".pack"])
async def test_symlinked_pack_file_refuses(tmp_path: Path, suffix: str) -> None:
    repo = tmp_path / "repo"
    base = init_repo(repo)
    head = commit_file(repo, "src/app.py", "value = 2\n", "fix")
    _repack(repo)
    pack_file = next(p for p in _packs(repo) if p.suffix == suffix)
    elsewhere = tmp_path / f"elsewhere{suffix}"
    shutil.move(pack_file, elsewhere)
    pack_file.symlink_to(elsewhere)

    with pytest.raises(ExportRefused, match="object_store_unsafe"):
        await _export(repo, base, head)


async def test_fifo_in_place_of_a_pack_index_refuses_without_hanging(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = init_repo(repo)
    head = commit_file(repo, "src/app.py", "value = 2\n", "fix")
    _repack(repo)
    index = next(p for p in _packs(repo) if p.suffix == ".idx")
    index.unlink()
    os.mkfifo(index)

    with pytest.raises(ExportRefused, match="object_store_unsafe"):
        await _export(repo, base, head)


async def test_export_runs_no_git_process_against_the_agent_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    base = init_repo(repo)
    head = commit_file(repo, "src/app.py", "value = 2\n", "fix")

    async def _no_subprocess(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the export must not spawn a process")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _no_subprocess)
    monkeypatch.setattr(export_module.asyncio, "create_subprocess_exec", _no_subprocess)

    export = await _export(repo, base, head)

    export.verify()


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"not zlib at all", id="not-zlib"),
        pytest.param(zlib.compress(b"blob 99\0value = 2\n"), id="size-lie"),
        pytest.param(zlib.compress(b"blob 9\0value = 2\n"), id="trailing-bytes"),
        pytest.param(zlib.compress(b"blob 99999999999\0x"), id="oversized"),
        pytest.param(zlib.compress(b"blob 10\0value = 2\n")[:-6], id="truncated"),
    ],
)
async def test_malformed_loose_object_refuses(tmp_path: Path, raw: bytes) -> None:
    repo = tmp_path / "repo"
    base = init_repo(repo)
    head = commit_file(repo, "src/app.py", "value = 2\n", "fix")
    loose = _loose(repo, _blob(repo, head, "src/app.py"))
    loose.chmod(0o644)
    loose.write_bytes(raw)

    with pytest.raises(ExportRefused, match="malformed_object"):
        await _export(repo, base, head)
