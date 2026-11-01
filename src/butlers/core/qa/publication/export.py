"""Sealed, sanitized export of an investigation branch (REQ-qa-publication-authority-004).

The publisher never runs git inside the agent repository.  It reads raw objects
through ``git cat-file --batch`` against a fresh, publisher-owned empty
repository whose object directory points at the agent's object store, so no
agent hook, config (including ``core.hooksPath``, ``core.fsmonitor``, filters,
drivers or includes) or credential helper is ever consulted.  Every object read
is re-hashed and must match its object id, so a tampered object store cannot
substitute bytes for a known id.

The export walks every commit newly reachable from the bound head down to the
trusted base and validates, before anything is pushed:

- commit headers (only ``tree``/``parent``/``author``/``committer``; no merges,
  signatures or encodings);
- author and committer identity, which must equal the dispatcher-bound QA
  identity exactly;
- the commit message and every path and added line of every newly reachable
  blob, through ``validate_anonymized()``.  Git bytes are content-addressed, so
  agent content is validated rather than rewritten; any residue refuses.

A leak in a prior commit that a later commit removes is still reachable, so it
still refuses.  The result is a frozen :class:`SealedExport` carrying the exact
validated bytes and their digest; :meth:`SealedExport.verify` re-derives both
before any transport use, so validated bytes cannot be swapped afterwards.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from butlers.core.git_env import isolated_git_env
from butlers.core.healing.anonymizer import validate_anonymized

_SHA = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_COMMIT_HEADERS = frozenset({b"tree", b"parent", b"author", b"committer"})
_SUBMODULE_MODE = b"160000"
_TREE_MODE = b"40000"

#: Bounds that keep a single export small enough to validate and retain.
MAX_COMMITS = 50
MAX_BLOB_BYTES = 1 << 20
MAX_EXPORT_BYTES = 20 << 20


class ExportRefused(RuntimeError):
    """The branch cannot be exported; ``code`` is a stable, content-free category."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class GitIdentity:
    """The dispatcher-bound author/committer identity every exported commit must carry."""

    name: str
    email: str

    def render(self) -> bytes:
        return f"{self.name} <{self.email}>".encode()


@dataclass(frozen=True, slots=True)
class ExportedObject:
    oid: str
    kind: str
    data: bytes


@dataclass(frozen=True, slots=True)
class SealedExport:
    """Immutable, validated objects for ``base_sha..head_sha``."""

    base_sha: str
    head_sha: str
    commits: tuple[str, ...]
    objects: tuple[ExportedObject, ...]
    digest: str

    def verify(self) -> None:
        """Refuse unless every object still hashes to its id and the digest still matches."""
        for obj in self.objects:
            if _object_id(obj.kind, obj.data) != obj.oid:
                raise ExportRefused("export_tampered")
        if _export_digest(self.base_sha, self.head_sha, self.commits, self.objects) != self.digest:
            raise ExportRefused("export_tampered")
        if not self.commits or self.commits[-1] != self.head_sha:
            raise ExportRefused("export_tampered")


def _object_id(kind: str, data: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(data)}\0".encode() + data, usedforsecurity=False).hexdigest()


def _export_digest(
    base_sha: str, head_sha: str, commits: tuple[str, ...], objects: tuple[ExportedObject, ...]
) -> str:
    digest = hashlib.sha256()
    digest.update(f"base {base_sha}\nhead {head_sha}\n".encode())
    for commit in commits:
        digest.update(f"commit {commit}\n".encode())
    for obj in objects:
        digest.update(f"{obj.oid} {obj.kind} {len(obj.data)}\n".encode())
        digest.update(obj.data)
    return digest.hexdigest()


class _ObjectReader:
    """Hash-verified raw object reads from an agent object store, with no agent config."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self.total_bytes = 0

    async def read(self, oid: str, expected_kind: str) -> bytes:
        stdin, stdout = self._process.stdin, self._process.stdout
        assert stdin is not None and stdout is not None
        stdin.write(f"{oid}\n".encode())
        await stdin.drain()
        header = (await stdout.readline()).rstrip(b"\n").split(b" ")
        if len(header) != 3 or header[0].decode() != oid:
            raise ExportRefused("object_missing")
        kind, size = header[1].decode(), int(header[2])
        if kind != expected_kind:
            raise ExportRefused("unexpected_object_type")
        if kind == "blob" and size > MAX_BLOB_BYTES:
            raise ExportRefused("export_too_large")
        self.total_bytes += size
        if self.total_bytes > MAX_EXPORT_BYTES:
            raise ExportRefused("export_too_large")
        data = await stdout.readexactly(size + 1)
        data = data[:-1]
        if _object_id(kind, data) != oid:
            raise ExportRefused("object_hash_mismatch")
        return data


@dataclass(frozen=True, slots=True)
class _TreeEntry:
    mode: bytes
    oid: str


def _parse_tree(data: bytes) -> dict[bytes, _TreeEntry]:
    entries: dict[bytes, _TreeEntry] = {}
    position = 0
    while position < len(data):
        space = data.index(b" ", position)
        nul = data.index(b"\0", space)
        mode, name = data[position:space], data[space + 1 : nul]
        oid = data[nul + 1 : nul + 21]
        if len(oid) != 20:
            raise ExportRefused("malformed_tree")
        entries[name] = _TreeEntry(mode, oid.hex())
        position = nul + 21
    return entries


@dataclass(frozen=True, slots=True)
class _Commit:
    tree: str
    parents: tuple[str, ...]
    author: bytes
    committer: bytes
    message: bytes


def _parse_commit(data: bytes) -> _Commit:
    head, separator, message = data.partition(b"\n\n")
    if not separator:
        raise ExportRefused("malformed_commit")
    fields: dict[bytes, list[bytes]] = {}
    for line in head.split(b"\n"):
        if line.startswith(b" "):
            raise ExportRefused("unsupported_commit_header")
        key, _, value = line.partition(b" ")
        if key not in _ALLOWED_COMMIT_HEADERS:
            raise ExportRefused("unsupported_commit_header")
        fields.setdefault(key, []).append(value)
    if [len(fields.get(k, [])) for k in (b"tree", b"author", b"committer")] != [1, 1, 1]:
        raise ExportRefused("malformed_commit")
    return _Commit(
        tree=fields[b"tree"][0].decode(),
        parents=tuple(p.decode() for p in fields.get(b"parent", [])),
        author=fields[b"author"][0],
        committer=fields[b"committer"][0],
        message=message,
    )


def _identity(signature: bytes) -> bytes:
    # "<name> <<email>> <epoch> <tz>": drop the trailing timestamp and zone.
    parts = signature.rsplit(b" ", 2)
    if len(parts) != 3:
        raise ExportRefused("author_metadata")
    return parts[0]


def _decode(data: bytes, code: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExportRefused(code) from exc


def _require_clean(text: str, code: str) -> None:
    clean, _ = validate_anonymized(text)
    if not clean:
        raise ExportRefused(code)


class _Walker:
    def __init__(self, reader: _ObjectReader) -> None:
        self._reader = reader
        self.objects: dict[str, ExportedObject] = {}

    async def _load(self, oid: str, kind: str) -> bytes:
        data = await self._reader.read(oid, kind)
        self.objects.setdefault(oid, ExportedObject(oid, kind, data))
        return data

    async def diff_trees(self, old_oid: str | None, new_oid: str, prefix: str) -> None:
        """Validate and collect everything in ``new_oid`` that ``old_oid`` does not hold."""
        if old_oid == new_oid:
            return
        new_entries = _parse_tree(await self._load(new_oid, "tree"))
        old_entries = (
            _parse_tree(await self._reader.read(old_oid, "tree")) if old_oid is not None else {}
        )
        for name, entry in new_entries.items():
            old = old_entries.get(name)
            if old is not None and old.oid == entry.oid and old.mode == entry.mode:
                continue
            path = prefix + _decode(name, "binary_path")
            _require_clean(path, "content_leak")
            if entry.mode == _SUBMODULE_MODE:
                raise ExportRefused("submodule")
            if entry.mode == _TREE_MODE:
                old_tree = old.oid if old is not None and old.mode == _TREE_MODE else None
                await self.diff_trees(old_tree, entry.oid, path + "/")
                continue
            old_blob = old.oid if old is not None and old.mode != _TREE_MODE else None
            await self._diff_blob(old_blob, entry.oid)

    async def _diff_blob(self, old_oid: str | None, new_oid: str) -> None:
        if old_oid == new_oid:
            return
        new_text = _decode(await self._load(new_oid, "blob"), "binary_content")
        old_lines: list[str] = []
        if old_oid is not None:
            # Content already reachable from the parent was validated (or is trusted
            # base content); only lines this commit adds are new egress.
            old_raw = await self._reader.read(old_oid, "blob")
            old_lines = old_raw.decode("utf-8", errors="replace").splitlines()
        added = [
            line[1:]
            for line in difflib.unified_diff(old_lines, new_text.splitlines(), lineterm="", n=0)
            if line.startswith("+") and not line.startswith("+++")
        ]
        _require_clean("\n".join(added), "content_leak")

    async def commit(self, oid: str) -> _Commit:
        return _parse_commit(await self._load(oid, "commit"))


async def _open_reader(objects_dir: Path, scratch: Path) -> asyncio.subprocess.Process:
    env = isolated_git_env()
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    init = await asyncio.create_subprocess_exec(
        "git",
        "init",
        "--quiet",
        "--bare",
        "--template=",
        str(scratch),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
    )
    if await init.wait() != 0:
        raise ExportRefused("reader_unavailable")
    env["GIT_DIR"] = str(scratch)
    env["GIT_OBJECT_DIRECTORY"] = str(objects_dir)
    return await asyncio.create_subprocess_exec(
        "git",
        "cat-file",
        "--batch",
        cwd=str(scratch),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
    )


async def build_sealed_export(
    objects_dir: Path,
    *,
    base_sha: str,
    head_sha: str,
    identity: GitIdentity,
) -> SealedExport:
    """Validate and freeze every object newly reachable from ``head_sha`` over ``base_sha``.

    ``objects_dir`` is the agent repository's object directory; ``base_sha`` is
    the dispatcher's trusted main commit and ``head_sha`` the bound head.  Any
    finding refuses with :class:`ExportRefused`; nothing is pushed by this call.
    """
    if not (_SHA.fullmatch(base_sha) and _SHA.fullmatch(head_sha)) or base_sha == head_sha:
        raise ExportRefused("invalid_range")
    with tempfile.TemporaryDirectory(prefix="qa-export-") as scratch:
        process = await _open_reader(objects_dir, Path(scratch))
        try:
            walker = _Walker(_ObjectReader(process))
            chain: list[tuple[str, _Commit]] = []
            cursor = head_sha
            while cursor != base_sha:
                if len(chain) >= MAX_COMMITS:
                    raise ExportRefused("not_descendant_of_base")
                commit = await walker.commit(cursor)
                if len(commit.parents) != 1:
                    raise ExportRefused("merge_or_root_commit")
                chain.append((cursor, commit))
                cursor = commit.parents[0]
            chain.reverse()

            expected_identity = identity.render()
            parent_tree = _parse_commit(await walker._reader.read(base_sha, "commit")).tree
            for _oid, commit in chain:
                if (
                    _identity(commit.author) != expected_identity
                    or _identity(commit.committer) != expected_identity
                ):
                    raise ExportRefused("author_metadata")
                _require_clean(_decode(commit.message, "binary_message"), "message_leak")
                await walker.diff_trees(parent_tree, commit.tree, "")
                parent_tree = commit.tree
        except (ValueError, asyncio.IncompleteReadError) as exc:
            raise ExportRefused("malformed_object") from exc
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()

    commits = tuple(oid for oid, _ in chain)
    objects = tuple(sorted(walker.objects.values(), key=lambda o: o.oid))
    return SealedExport(
        base_sha=base_sha,
        head_sha=head_sha,
        commits=commits,
        objects=objects,
        digest=_export_digest(base_sha, head_sha, commits, objects),
    )
