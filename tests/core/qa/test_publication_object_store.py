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
from butlers.core.qa.publication import object_store
from butlers.core.qa.publication.export import ExportRefused, SealedExport, build_sealed_export
from butlers.core.qa.publication.object_store import ObjectStore, ObjectStoreError
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


# --- Hostile pack bytes, crafted directly (bu-dqu408) -------------------------------

_A, _B = b"\x11" * 20, b"\x22" * 20


def _entry_header(kind: int, size: int) -> bytes:
    byte, size = (kind << 4) | (size & 0x0F), size >> 4
    out = bytearray()
    while size:
        out.append(byte | 0x80)
        byte, size = size & 0x7F, size >> 7
    return bytes(out + bytes([byte]))


def _delta_size(value: int) -> bytes:
    out = bytearray()
    while True:
        byte, value = value & 0x7F, value >> 7
        if not value:
            return bytes(out + bytes([byte]))
        out.append(byte | 0x80)


def _write_pack(objects: Path, entries: list[tuple[bytes, bytes]], *, offsets=None) -> None:
    """Write one v2 pack + idx holding ``(oid, raw entry)``; ``offsets`` overrides idx offsets."""
    (objects / "pack").mkdir(parents=True)
    body = bytearray(b"PACK\0\0\0\2" + len(entries).to_bytes(4, "big"))
    at: dict[bytes, int] = {}
    for oid, raw in entries:
        at[oid] = len(body)
        body += raw
    body += b"\0" * 20
    at.update(offsets or {})
    oids = sorted(at)
    fanout = [sum(1 for oid in oids if oid[0] <= first) for first in range(256)]
    index = bytearray(b"\377tOc\0\0\0\2" + b"".join(n.to_bytes(4, "big") for n in fanout))
    index += b"".join(oids) + b"\0" * 4 * len(oids)
    large = bytearray()
    for oid in oids:
        if at[oid] >= 1 << 31:
            index += (0x80000000 | len(large) // 8).to_bytes(4, "big")
            large += at[oid].to_bytes(8, "big")
        else:
            index += at[oid].to_bytes(4, "big")
    index += large
    (objects / "pack" / f"pack-{'a' * 40}.pack").write_bytes(bytes(body))
    (objects / "pack" / f"pack-{'a' * 40}.idx").write_bytes(bytes(index))


def _read(objects: Path, oid: bytes) -> tuple[str, bytes]:
    with ObjectStore(objects) as store:
        return store.read(oid.hex())


def _ref_delta(base: bytes, delta: bytes) -> bytes:
    return _entry_header(7, len(delta)) + base + zlib.compress(delta)


def test_crafted_ref_delta_resolves_like_git(tmp_path: Path) -> None:
    # copy(offset 0, size 5) + insert " world"
    delta = _delta_size(5) + _delta_size(11) + b"\x90\x05" + b"\x06 world"
    entries = [(_A, _entry_header(3, 5) + zlib.compress(b"hello")), (_B, _ref_delta(_A, delta))]
    _write_pack(tmp_path / "objects", entries)

    assert _read(tmp_path / "objects", _B) == ("blob", b"hello world")


@pytest.mark.parametrize("cycle", ["self", "pair"])
def test_ref_delta_cycle_refuses_without_recursing(tmp_path: Path, cycle: str) -> None:
    delta = _delta_size(3) + _delta_size(3) + b"\x03abc"
    if cycle == "self":
        entries = [(_A, _ref_delta(_A, delta))]
    else:
        entries = [(_A, _ref_delta(_B, delta)), (_B, _ref_delta(_A, delta))]
    _write_pack(tmp_path / "objects", entries)

    with pytest.raises(ObjectStoreError, match="malformed_object"):
        _read(tmp_path / "objects", _A)


def test_chain_of_large_deltas_is_charged_to_one_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two 8 KiB insert-only deltas pointing at each other: each fits the per-object cap,
    # but the chain must stop once its total inflated bytes pass the per-read budget,
    # long before the depth cap (which alone would allow depth x object-size bytes).
    monkeypatch.setattr(object_store, "MAX_CHAIN_BYTES", 64 * 1024)
    ops = (b"\x7f" + b"x" * 127) * 64
    delta = _delta_size(1) + _delta_size(127 * 64) + ops
    entries = [(_A, _ref_delta(_B, delta)), (_B, _ref_delta(_A, delta))]
    _write_pack(tmp_path / "objects", entries)
    inflated: list[int] = []
    original = object_store._inflate
    monkeypatch.setattr(
        object_store, "_inflate", lambda read, size: inflated.append(size) or original(read, size)
    )

    with pytest.raises(ObjectStoreError, match="malformed_object"):
        _read(tmp_path / "objects", _A)
    assert inflated == []  # refused while walking headers, before inflating any delta


@pytest.mark.parametrize(
    "delta",
    [
        pytest.param(_delta_size(5) + _delta_size(5) + b"\x91", id="truncated-copy"),
        pytest.param(_delta_size(5) + _delta_size(5) + b"\x09abc", id="truncated-insert"),
    ],
)
def test_truncated_delta_opcode_refuses_as_malformed(tmp_path: Path, delta: bytes) -> None:
    entries = [(_A, _entry_header(3, 5) + zlib.compress(b"hello")), (_B, _ref_delta(_A, delta))]
    _write_pack(tmp_path / "objects", entries)

    with pytest.raises(ObjectStoreError, match="malformed_object"):
        _read(tmp_path / "objects", _B)


@pytest.mark.parametrize(
    "offset", [(1 << 64) - 1, 1 << 40, 4], ids=["u64-max", "past-end", "header"]
)
def test_index_offset_outside_the_pack_refuses(tmp_path: Path, offset: int) -> None:
    entries = [(_A, _entry_header(3, 5) + zlib.compress(b"hello"))]
    _write_pack(tmp_path / "objects", entries, offsets={_A: offset})

    with pytest.raises(ObjectStoreError, match="malformed_object"):
        _read(tmp_path / "objects", _A)


def test_oversized_loose_file_refuses_before_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(object_store, "MAX_LOOSE_FILE_BYTES", 1024)
    oid = "ab" + "c" * 38
    (tmp_path / "objects" / "ab").mkdir(parents=True)
    (tmp_path / "objects" / "ab" / oid[2:]).write_bytes(zlib.compress(os.urandom(4096), 0))

    with pytest.raises(ObjectStoreError, match="malformed_object"):
        with ObjectStore(tmp_path / "objects") as store:
            store.read(oid)
