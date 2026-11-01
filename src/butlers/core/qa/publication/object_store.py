"""Read raw git objects from an agent-writable object directory without leaving it.

The agent can write every byte of ``.git/objects``, including the indirections git
itself honours: ``info/alternates`` (any other object store on the host),
``info/http-alternates``, symlinked loose objects, fan-out directories, packs or the
object directory itself, and FIFOs that block an ``open`` forever.  Any git process
pointed at that directory would follow them.  So no git process runs here: loose
objects and pack files are parsed in-process, and every file is opened relative to a
directory descriptor with ``O_NOFOLLOW | O_NONBLOCK``, must be a regular file and
must fit the size bounds.  A planted alternate or any non-regular entry refuses the
read with ``object_store_unsafe`` instead of being followed or ignored.

Only what the sealed export needs is supported: SHA-1 loose objects and version-2
pack indexes over version-2/3 packs with ``OFS_DELTA``/``REF_DELTA`` entries.
Callers re-hash every returned object against its id.
"""

from __future__ import annotations

import os
import re
import stat
import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
# O_NONBLOCK keeps a planted FIFO from blocking the open; the S_ISREG check then refuses it.
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
_FANOUT = re.compile(r"[0-9a-f]{2}")
_PACK_INDEX = re.compile(r"pack-[0-9a-f]{40}\.idx")
_INDIRECTIONS = ("alternates", "http-alternates")
_PACK_KINDS = {1: "commit", 2: "tree", 3: "blob", 4: "tag"}
_OFS_DELTA, _REF_DELTA = 6, 7
_IDX_MAGIC = b"\377tOc"
_CHUNK = 64 * 1024

#: Longest delta chain followed; git itself never writes one deeper than 4095.
MAX_DELTA_DEPTH = 4096
#: Largest object (or intermediate delta base) materialised in memory.
MAX_OBJECT_BYTES = 64 << 20
#: Largest single object-store file opened; bounds a planted oversized loose object.
MAX_FILE_BYTES = 4 << 30


class ObjectStoreError(RuntimeError):
    """The read refused; ``code`` is a stable, content-free category."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _unsafe(exc: BaseException | None = None) -> ObjectStoreError:
    error = ObjectStoreError("object_store_unsafe")
    error.__cause__ = exc
    return error


def _malformed() -> ObjectStoreError:
    return ObjectStoreError("malformed_object")


def _open_dir(name: str | Path, dir_fd: int | None = None) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    except OSError as exc:  # ELOOP (symlink), ENOTDIR, EACCES, ...
        raise _unsafe(exc) from exc


def _open_file(name: str, dir_fd: int) -> int:
    """Open a regular file below ``dir_fd``; ``FileNotFoundError`` passes through."""
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _unsafe(exc) from exc
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
        os.close(fd)
        raise _unsafe()
    return fd


def _pread_exact(fd: int, size: int, offset: int) -> bytes:
    data = os.pread(fd, size, offset)
    if len(data) != size:
        raise _malformed()
    return data


def _inflate(read: Callable[[int], bytes], expected: int) -> bytes:
    """Inflate one zlib stream fed by ``read(n)``; it must yield exactly ``expected`` bytes."""
    decompressor = zlib.decompressobj()
    out = bytearray()
    try:
        while not decompressor.eof:
            chunk = decompressor.unconsumed_tail or read(_CHUNK)
            if not chunk:
                raise _malformed()
            out += decompressor.decompress(chunk, expected + 1 - len(out))
            if len(out) > expected:
                raise _malformed()
    except zlib.error as exc:
        raise _malformed() from exc
    if len(out) != expected:
        raise _malformed()
    return bytes(out)


def _varint(data: bytes, position: int) -> tuple[int, int]:
    """Decode a delta-header size (little-endian base-128)."""
    value = shift = 0
    while True:
        if position >= len(data):
            raise _malformed()
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, position


def _apply_delta(base: bytes, delta: bytes) -> bytes:
    source_size, position = _varint(delta, 0)
    target_size, position = _varint(delta, position)
    if source_size != len(base) or target_size > MAX_OBJECT_BYTES:
        raise _malformed()
    out = bytearray()
    while position < len(delta):
        op = delta[position]
        position += 1
        if op & 0x80:
            offset = size = 0
            for bit in range(4):
                if op & (1 << bit):
                    offset |= delta[position] << (8 * bit)
                    position += 1
            for bit in range(3):
                if op & (0x10 << bit):
                    size |= delta[position] << (8 * bit)
                    position += 1
            size = size or 0x10000
            if offset + size > len(base):
                raise _malformed()
            out += base[offset : offset + size]
        elif op:
            out += delta[position : position + op]
            position += op
        else:
            raise _malformed()
        if len(out) > target_size:
            raise _malformed()
    if len(out) != target_size:
        raise _malformed()
    return bytes(out)


@dataclass(slots=True)
class _Pack:
    index_fd: int
    pack_fd: int
    count: int
    fanout: tuple[int, ...]

    def offset_of(self, oid: bytes) -> int | None:
        names_at = 8 + 1024
        low = self.fanout[oid[0] - 1] if oid[0] else 0
        high = self.fanout[oid[0]]
        if not low <= high <= self.count:
            raise _malformed()
        while low < high:  # binary search of the sorted name table, read in place
            middle = (low + high) // 2
            name = _pread_exact(self.index_fd, 20, names_at + 20 * middle)
            if name == oid:
                break
            if name < oid:
                low = middle + 1
            else:
                high = middle
        else:
            return None
        position = middle
        offsets_at = names_at + 24 * self.count  # names, then CRC32s
        (offset,) = struct.unpack(">I", _pread_exact(self.index_fd, 4, offsets_at + 4 * position))
        if offset & 0x80000000:
            large_at = offsets_at + 4 * self.count + 8 * (offset & 0x7FFFFFFF)
            (offset,) = struct.unpack(">Q", _pread_exact(self.index_fd, 8, large_at))
        return offset


class ObjectStore:
    """Confined reads of one object directory; use as a context manager."""

    def __init__(self, objects_dir: Path) -> None:
        self._fds: list[int] = []
        self._packs: list[_Pack] = []
        try:
            parent = self._keep(_open_dir(objects_dir.parent))
            self._objects = self._keep(_open_dir(objects_dir.name, parent))
            self._refuse_indirections()
            self._load_packs()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> ObjectStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        while self._fds:
            os.close(self._fds.pop())

    def _keep(self, fd: int) -> int:
        self._fds.append(fd)
        return fd

    def _refuse_indirections(self) -> None:
        try:
            info = self._keep(os.open("info", _DIR_FLAGS, dir_fd=self._objects))
        except FileNotFoundError:
            return
        except OSError as exc:
            raise _unsafe(exc) from exc
        for name in _INDIRECTIONS:
            try:
                os.stat(name, dir_fd=info, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise _unsafe()

    def _load_packs(self) -> None:
        try:
            pack_dir = self._keep(os.open("pack", _DIR_FLAGS, dir_fd=self._objects))
        except FileNotFoundError:
            return
        except OSError as exc:
            raise _unsafe(exc) from exc
        for name in sorted(os.listdir(pack_dir)):
            if not _PACK_INDEX.fullmatch(name):
                continue
            try:
                index_fd = self._keep(_open_file(name, pack_dir))
                pack_fd = self._keep(_open_file(name[: -len(".idx")] + ".pack", pack_dir))
            except FileNotFoundError as exc:
                raise _unsafe(exc) from exc
            header = _pread_exact(index_fd, 8 + 1024, 0)
            if header[:8] != _IDX_MAGIC + b"\0\0\0\2":
                raise _malformed()
            fanout = struct.unpack(">256I", header[8:])
            pack_header = _pread_exact(pack_fd, 12, 0)
            if pack_header[:4] != b"PACK" or pack_header[4:8] not in (b"\0\0\0\2", b"\0\0\0\3"):
                raise _malformed()
            self._packs.append(_Pack(index_fd, pack_fd, fanout[255], fanout))

    def read(self, oid: str) -> tuple[str, bytes]:
        """Return ``(kind, data)`` for ``oid``; ``object_missing`` when the store lacks it."""
        loose = self._read_loose(oid)
        if loose is not None:
            return loose
        return self._read_packed(bytes.fromhex(oid), depth=0)

    def _read_loose(self, oid: str) -> tuple[str, bytes] | None:
        if not _FANOUT.fullmatch(oid[:2]):
            raise ObjectStoreError("object_missing")
        try:
            fanout = os.open(oid[:2], _DIR_FLAGS, dir_fd=self._objects)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise _unsafe(exc) from exc
        try:
            fd = _open_file(oid[2:], fanout)
        except FileNotFoundError:
            return None
        finally:
            os.close(fanout)
        try:
            with os.fdopen(fd, "rb") as handle:
                return self._inflate_loose(handle.read(MAX_FILE_BYTES + 1))
        except OSError as exc:
            raise _unsafe(exc) from exc

    @staticmethod
    def _inflate_loose(raw: bytes) -> tuple[str, bytes]:
        decompressor = zlib.decompressobj()
        try:
            head = decompressor.decompress(raw, 64)
        except zlib.error as exc:
            raise _malformed() from exc
        header, nul, rest = head.partition(b"\0")
        kind, _, size_text = header.partition(b" ")
        if not nul or kind.decode(errors="replace") not in _PACK_KINDS.values():
            raise _malformed()
        if not size_text.isdigit() or int(size_text) > MAX_OBJECT_BYTES:
            raise _malformed()
        size = int(size_text)
        if len(rest) > size:
            raise _malformed()
        remainder = b""
        if not decompressor.eof:
            try:
                remainder = decompressor.decompress(
                    decompressor.unconsumed_tail, size + 1 - len(rest)
                )
            except zlib.error as exc:
                raise _malformed() from exc
            if not decompressor.eof:
                raise _malformed()
        data = rest + remainder
        if len(data) != size:
            raise _malformed()
        return kind.decode(), data

    def _locate(self, oid: bytes) -> tuple[_Pack, int]:
        for pack in self._packs:
            offset = pack.offset_of(oid)
            if offset is not None:
                return pack, offset
        raise ObjectStoreError("object_missing")

    def _read_packed(self, oid: bytes, depth: int) -> tuple[str, bytes]:
        pack, offset = self._locate(oid)
        return self._read_entry(pack, offset, depth)

    def _read_entry(self, pack: _Pack, offset: int, depth: int) -> tuple[str, bytes]:
        """Resolve the entry at ``offset``, following its delta chain iteratively."""
        deltas: list[bytes] = []
        while True:
            if depth + len(deltas) > MAX_DELTA_DEPTH:
                raise _malformed()
            kind_code, size, position = self._entry_header(pack, offset)
            if kind_code in _PACK_KINDS:
                base = self._inflate_at(pack, position, size)
                kind = _PACK_KINDS[kind_code]
                break
            if kind_code == _OFS_DELTA:
                distance, position = self._ofs_distance(pack, position)
                if distance <= 0 or distance > offset:
                    raise _malformed()
                deltas.append(self._inflate_at(pack, position, size))
                offset -= distance
                continue
            if kind_code == _REF_DELTA:
                base_oid = _pread_exact(pack.pack_fd, 20, position)
                deltas.append(self._inflate_at(pack, position + 20, size))
                kind, base = self._read_packed(base_oid, depth + len(deltas))
                break
            raise _malformed()
        for delta in reversed(deltas):
            base = _apply_delta(base, delta)
        return kind, base

    @staticmethod
    def _entry_header(pack: _Pack, offset: int) -> tuple[int, int, int]:
        header = os.pread(pack.pack_fd, 16, offset)
        if not header:
            raise _malformed()
        byte = header[0]
        kind_code, size, shift, index = (byte >> 4) & 7, byte & 0x0F, 4, 1
        while byte & 0x80:
            if index >= len(header):
                raise _malformed()
            byte = header[index]
            size |= (byte & 0x7F) << shift
            shift += 7
            index += 1
        if size > MAX_OBJECT_BYTES:
            raise _malformed()
        return kind_code, size, offset + index

    @staticmethod
    def _ofs_distance(pack: _Pack, position: int) -> tuple[int, int]:
        data = os.pread(pack.pack_fd, 16, position)
        if not data:
            raise _malformed()
        byte, index = data[0], 1
        distance = byte & 0x7F
        while byte & 0x80:
            if index >= len(data):
                raise _malformed()
            byte = data[index]
            index += 1
            distance = ((distance + 1) << 7) | (byte & 0x7F)
        return distance, position + index

    @staticmethod
    def _inflate_at(pack: _Pack, position: int, size: int) -> bytes:
        cursor = [position]

        def _read(count: int) -> bytes:
            chunk = os.pread(pack.pack_fd, count, cursor[0])
            cursor[0] += len(chunk)
            return chunk

        return _inflate(_read, size)
