"""Invocation-local staging. Never copy ambient HOME, env or credential stores."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .contracts import LaunchRequest, Refused
from .opencode import CONFIG_NAME, OpenCodeSettings


class StagingCleanupFailed(Refused):
    """Retain the partial allocation for its owner without exposing its path."""

    def __init__(self, material: InvocationMaterial) -> None:
        super().__init__("stage_cleanup_failed")
        self.material = material


@dataclass
class InvocationMaterial:
    """Owned private staging, retained if domain settlement cannot be established."""

    directory: Path = field(repr=False)
    _device: int = field(repr=False)
    _inode: int = field(repr=False)
    content_hashes: dict[str, str] = field(default_factory=dict, repr=False)

    _request: LaunchRequest | None = field(default=None, repr=False)

    def prepare_opencode(self, settings: OpenCodeSettings) -> None:
        """Create only the closed native file inside this existing owner.

        Failure leaves any partial file in the directory already owned by
        InvocationOwner; its settlement/discard/quarantine rules still apply.
        """
        if (
            type(settings) is not OpenCodeSettings
            or type(self._request) is not LaunchRequest
            or self._request.runtime != "opencode"
            or self._request.model != settings.model
        ):
            raise Refused("invalid_opencode_material")
        content = settings.render(self._request.broker, self._request.model_credential)
        if len(content) > 65536 or CONFIG_NAME in self.content_hashes:
            raise Refused("invalid_opencode_material")
        directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(directory)
            if (metadata.st_dev, metadata.st_ino) != (self._device, self._inode):
                raise Refused("stage_identity_changed")
            descriptor = os.open(
                CONFIG_NAME,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
            self.content_hashes[CONFIG_NAME] = hashlib.sha256(content).hexdigest()
        finally:
            os.close(directory)

    @classmethod
    def stage(cls, root: Path, request: LaunchRequest) -> InvocationMaterial:
        # root is operator-owned private storage, never a caller path. Reject
        # symlinks and permissive ancestors instead of repairing shared storage.
        root = root.absolute()
        for parent in (root, *root.parents):
            metadata = parent.lstat()
            trusted_sticky_root = metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid not in (0, os.geteuid())
                or (metadata.st_mode & 0o022 and not trusted_sticky_root)
            ):
                raise Refused("unsafe_stage_root")
        metadata = root.lstat()
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise Refused("unsafe_stage_root")
        directory = Path(tempfile.mkdtemp(prefix="invocation-", dir=root))
        metadata = directory.stat()
        material = cls(directory, metadata.st_dev, metadata.st_ino, _request=request)
        try:
            payloads = {
                "model-credential": request.model_credential,
                "prompt": request.prompt.encode(),
                "mcp.json": json.dumps(
                    {
                        "mcpServers": {
                            "broker": {
                                "type": "http",
                                "url": request.broker.endpoint,
                                "headers": {"Authorization": f"Bearer {request.broker.token}"},
                            }
                        }
                    },
                    separators=(",", ":"),
                ).encode(),
            }
            for name, content in payloads.items():
                descriptor = os.open(
                    directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                material.content_hashes[name] = hashlib.sha256(content).hexdigest()
        except BaseException:
            try:
                material.discard()
            except BaseException:
                raise StagingCleanupFailed(material) from None
            raise Refused("staging_failed") from None
        return material

    def discard(self) -> None:
        """Remove only the exact owned directory after the invocation owner settles."""
        try:
            metadata = self.directory.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != (
                self._device,
                self._inode,
            ):
                raise Refused("stage_identity_changed")
            shutil.rmtree(self.directory)
        except OSError:
            raise Refused("stage_cleanup_failed") from None
