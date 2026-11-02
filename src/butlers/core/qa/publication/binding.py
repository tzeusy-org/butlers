"""Dispatcher-held publication binding.

The binding names the repository, branch, base, head and PR a publication may
touch.  It is built by the trusted dispatcher from its own records; nothing in
an agent payload can choose the target or the operation.  Only digests of the
binding and payload are persisted, so a receipt cannot leak either.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")

#: The base every QA proposal targets; the reconciliation sweep rebuilds bindings with it.
QA_BASE_REF = "main"


class OperationKind(StrEnum):
    """The closed set of publication operations (REQ-qa-publication-authority-003)."""

    BRANCH_PUSH = "branch_push"
    PR_CREATE = "pr_create"
    PR_UPDATE = "pr_update"
    PR_LABELS = "pr_labels"


class InvalidBinding(ValueError):
    """The binding is malformed; refuse before anything is persisted or sent."""


@dataclass(frozen=True, slots=True)
class PublicationBinding:
    """The exact target of one attempt's publication."""

    repository: str
    branch: str
    base_ref: str
    head_sha: str
    pr_number: int | None = None

    def __post_init__(self) -> None:
        if not _REPOSITORY.fullmatch(self.repository):
            raise InvalidBinding("repository must be owner/name")
        for name, value in (("branch", self.branch), ("base_ref", self.base_ref)):
            if not _REF.fullmatch(value) or ".." in value or value.endswith((".lock", "/", ".")):
                raise InvalidBinding(f"{name} is not a valid ref name")
        if not _SHA.fullmatch(self.head_sha):
            raise InvalidBinding("head_sha must be a 40-character lowercase hex SHA")
        if self.pr_number is not None and (
            not isinstance(self.pr_number, int) or isinstance(self.pr_number, bool)
        ):
            raise InvalidBinding("pr_number must be an integer")
        if self.pr_number is not None and self.pr_number < 1:
            raise InvalidBinding("pr_number must be positive")

    def digest(self) -> str:
        return canonical_digest(
            {
                "repository": self.repository,
                "branch": self.branch,
                "base_ref": self.base_ref,
                "head_sha": self.head_sha,
                "pr_number": self.pr_number,
            }
        )


def pull_request_url(repository: str, number: int) -> str:
    """The web URL of PR ``number`` in ``repository`` (stored on ``healing_attempts``)."""
    return f"https://github.com/{repository}/pull/{number}"


def canonical_digest(value: Mapping[str, Any]) -> str:
    """SHA-256 hex of canonical JSON (sorted keys, no whitespace)."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def request_digest(payload: Mapping[str, Any]) -> str:
    """Digest of the validated publication payload (title, body, labels, ...)."""
    return canonical_digest(payload)


def pr_text_payload(title: str, body: str) -> dict[str, Any]:
    """The ``pr_create``/``pr_update`` payload; reconciliation recomputes its digest."""
    return {"title": title, "body": body}


def labels_payload(labels: Sequence[str]) -> dict[str, Any]:
    """The ``pr_labels`` payload; reconciliation recomputes its digest."""
    return {"labels": list(labels)}


def expected_object_id(kind: OperationKind, binding: PublicationBinding) -> str | None:
    """The observable object an operation binds before egress (REQ-qa-publication-authority-006).

    A push binds the head it moves the branch to; a PR update or label set binds the
    bound PR's number, whose observed state the reconciler compares against the stored
    request digest.  A ``pr_create`` cannot know its number yet and binds nothing.
    """
    if kind is OperationKind.BRANCH_PUSH:
        return binding.head_sha
    if kind in (OperationKind.PR_UPDATE, OperationKind.PR_LABELS):
        if binding.pr_number is None:
            raise InvalidBinding(f"{kind.value} requires a bound PR")
        return str(binding.pr_number)
    return None
