"""Internal capture representations and external recovery epoch (RFC0037).

No model tool, HTTP route, classifier or loop is registered here. VerifiedAuthority
is supplied only by the later source-owning ingress adapter after its server-held
owner/session/source verification; constructing it is not itself authentication.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class CaptureUnavailable(RuntimeError):
    """Content-blind refusal; never echo an input or underlying exception."""

    def __init__(self, category: str = "unavailable") -> None:
        self.category = category
        super().__init__("Capture service unavailable")


@dataclass(frozen=True)
class ServiceEpoch:
    generation: uuid.UUID
    not_before: datetime


def load_epoch(path: Path) -> ServiceEpoch:
    """Read a regular nonsecret host manifest; missing/malformed never initializes it."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError
            data = json.loads(stream.read(4097))
        if set(data) != {"generation", "not_before"}:
            raise ValueError
        boundary = datetime.fromisoformat(data["not_before"])
        if boundary.tzinfo is None:
            raise ValueError
        return ServiceEpoch(uuid.UUID(data["generation"]), boundary.astimezone(UTC))
    except (OSError, ValueError, TypeError, KeyError):
        raise CaptureUnavailable("epoch_unavailable") from None


def rotate_epoch(path: Path) -> ServiceEpoch:
    """Explicit restore/operator primitive, never called by admission/startup.

    The host path is outside the database and repository. Atomic replace and fsync
    make the new rotation cutoff durable before any restored authority is issued.
    The manifest is nonsecret; it is not a credential or an owner capability.
    """
    epoch = ServiceEpoch(uuid.uuid4(), datetime.now(UTC))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".capture-epoch-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(
                {"generation": str(epoch.generation), "not_before": epoch.not_before.isoformat()},
                stream,
                sort_keys=True,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return epoch


@dataclass(frozen=True)
class VerifiedAuthority:
    """Trusted adapter result, not a caller/model-authored actor assertion.

    Source verification/expiry/revocation remains the ingress owner's contract.
    payload_digest binds the exact canonical input this authority admitted.
    source_occurred_at is the original occurrence time, never a reminted request time.
    """

    principal_id: uuid.UUID
    source_occurrence: uuid.UUID
    source_occurred_at: datetime
    service_epoch: uuid.UUID
    payload_digest: str


def canonical_intake(text: str, references: list[dict[str, str]] | None = None) -> tuple[str, str]:
    """Refuse full oversized/malformed input, never truncate or fetch references.

    References are normalized owning-source UUID handles, not URLs or provider
    credentials. Their ordinary-source admission is verified by the ingress owner.
    """
    try:
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 32768:
            raise ValueError
        refs = [] if references is None else references
        if not isinstance(refs, list) or len(refs) > 8:
            raise ValueError
        normalized = []
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != {"source_id", "attachment_id"}:
                raise ValueError
            normalized.append({key: str(uuid.UUID(ref[key])) for key in sorted(ref)})
        value = json.dumps(
            {"text": text, "references": normalized},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        encoded = value.encode("utf-8")
        if len(encoded) > 65536:
            raise ValueError
        return value, hashlib.sha256(encoded).hexdigest()
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise CaptureUnavailable("invalid_input") from None


@dataclass(frozen=True)
class CaptureReceipt:
    capture_id: uuid.UUID
    disposition: str
    category: str
    operation_id: uuid.UUID | None = None
    target_receipt: dict[str, Any] | None = None


def project_receipt(row: Any) -> CaptureReceipt:
    """Content-free admission/status projection; no retained source text."""
    return CaptureReceipt(
        row["id"], row["disposition"], row["category"], row["operation_id"], row["receipt"]
    )
