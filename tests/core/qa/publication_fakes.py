"""Synthetic provider, ledger and git fixtures for QA publication tests (no live egress)."""

from __future__ import annotations

import os
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from butlers.core.qa.publication.binding import OperationKind, PublicationBinding
from butlers.core.qa.publication.export import GitIdentity, SealedExport
from butlers.core.qa.publication.state import PublicationOperation, PublicationRefused
from butlers.core.qa.publication.transport import AmbiguousOutcome, StaleExpectedHead

REPOSITORY = "acme/butlers"
QA_IDENTITY = GitIdentity("QA Staffer", "qa-staffer@users.noreply.github.com")


@dataclass
class FakeProvider:
    """Records every effect; refs and PRs behave like an atomic remote."""

    repository: str = REPOSITORY
    refs: dict[str, str] = field(default_factory=dict)
    prs: dict[int, dict] = field(default_factory=dict)
    calls: list[tuple[str, dict]] = field(default_factory=list)
    lose_response_on: str | None = None
    raise_on_push: Exception | None = None
    raise_on_create: Exception | None = None

    def _maybe_lose(self, name: str) -> None:
        if self.lose_response_on == name:
            raise AmbiguousOutcome("response lost after the remote effect")

    async def update_ref(self, *, branch, expected_old, new, export) -> None:
        self.calls.append(("update_ref", {"branch": branch, "old": expected_old, "new": new}))
        if self.raise_on_push is not None:
            raise self.raise_on_push
        if self.refs.get(branch) != expected_old:
            raise StaleExpectedHead(branch)
        self.refs[branch] = new
        self._maybe_lose("update_ref")

    async def create_pull_request(self, *, head, base, title, body) -> int:
        self.calls.append(("create_pull_request", {"head": head, "base": base, "title": title}))
        if self.raise_on_create is not None:
            raise self.raise_on_create
        number = 100 + len(self.prs)
        self.prs[number] = {"head": head, "base": base, "title": title, "body": body}
        self._maybe_lose("create_pull_request")
        return number

    async def update_pull_request(self, *, number, title, body) -> None:
        self.calls.append(("update_pull_request", {"number": number}))

    async def set_labels(self, *, number, labels) -> None:
        self.calls.append(("set_labels", {"number": number, "labels": labels}))
        self.prs[number]["labels"] = labels

    # Read-only ``PublicationReader`` view; never appended to ``calls`` (the effect log).

    async def read_ref(self, *, branch) -> str | None:
        return self.refs.get(branch)

    async def find_pull_requests(self, *, head, base) -> tuple[int, ...]:
        return tuple(n for n, pr in self.prs.items() if (pr["head"], pr["base"]) == (head, base))


@dataclass
class FakeLedger:
    """In-memory ``PublicationLedger`` with the real ledger's stage and uniqueness rules."""

    attempt_status: str = "investigating"
    rows: dict[tuple[uuid.UUID, str], PublicationOperation] = field(default_factory=dict)
    exports: dict[uuid.UUID, SealedExport] = field(default_factory=dict)
    retain_refusal: str | None = None

    async def retain(self, attempt_id, export) -> None:
        if self.retain_refusal is not None:
            raise PublicationRefused(self.retain_refusal)
        stored = self.exports.setdefault(attempt_id, export)
        if stored.digest != export.digest:
            raise PublicationRefused("export_changed")

    async def begin(self, *, attempt_id, kind, binding, request_digest, expected_object_id):
        key = (attempt_id, kind.value)
        existing = self.rows.get(key)
        if existing is None:
            existing = PublicationOperation(
                id=uuid.uuid4(),
                attempt_id=attempt_id,
                kind=kind.value,
                stage="bound",
                binding_digest=binding.digest(),
                request_digest=request_digest,
                expected_object_id=expected_object_id,
                new_object_id=None,
                result=None,
                reconciliation=None,
                started_at=datetime.now(UTC),
                ended_at=None,
                reconciled_at=None,
            )
            self.rows[key] = existing
        if existing.binding_digest != binding.digest():
            raise PublicationRefused("binding_changed")
        if existing.request_digest != request_digest:
            raise PublicationRefused("payload_changed")
        return existing

    def _replace(self, operation_id, **changes) -> bool:
        for key, row in self.rows.items():
            if row.id == operation_id:
                self.rows[key] = PublicationOperation(**{**_fields(row), **changes})
                return True
        return False

    def _get(self, operation_id) -> PublicationOperation:
        return next(row for row in self.rows.values() if row.id == operation_id)

    async def start_egress(self, operation_id) -> bool:
        if self.attempt_status not in ("investigating", "pr_open"):
            raise PublicationRefused("attempt_not_active")
        if self._get(operation_id).stage != "bound":
            return False
        return self._replace(operation_id, stage="egress_started")

    async def record(self, operation_id, outcome, *, new_object_id) -> bool:
        if self._get(operation_id).stage != "egress_started":
            return False
        return self._replace(operation_id, stage=outcome, new_object_id=new_object_id)

    async def get(self, attempt_id, kind) -> PublicationOperation | None:
        return self.rows.get((attempt_id, kind.value))

    async def reconcile(self, operation_id, observation, *, observed_object_id) -> bool:
        """Mirror ``state.record_reconciliation`` and the core_298 adoption CHECK."""
        row = self._get(operation_id)
        if not row.holds_lineage:
            return False
        exact = observed_object_id is not None and observed_object_id == row.expected_object_id
        adopted = (
            row.kind == "pr_create"
            and row.expected_object_id is None
            and row.new_object_id is None
            and str(observed_object_id or "").isdigit()
        )
        if observation == "resource_found" and not (exact or adopted):
            return False
        if observation == "resource_absent" and observed_object_id is not None:
            return False
        return self._replace(
            operation_id,
            stage="reconciled",
            reconciliation=observation,
            new_object_id=row.new_object_id or observed_object_id,
        )

    def row(self, attempt_id: uuid.UUID, kind: OperationKind) -> PublicationOperation:
        return self.rows[(attempt_id, kind.value)]


def _fields(row: PublicationOperation) -> dict:
    return {name: getattr(row, name) for name in PublicationOperation.__dataclass_fields__}


def binding_for(export: SealedExport, **overrides) -> PublicationBinding:
    fields = {
        "repository": REPOSITORY,
        "branch": "qa/finance/abcdef123456-1",
        "base_ref": "main",
        "head_sha": export.head_sha,
    }
    return PublicationBinding(**{**fields, **overrides})


def synthetic_export(head_sha: str = "c" * 40) -> SealedExport:
    """An empty but self-consistent export for transport/publisher tests."""
    from butlers.core.qa.publication.export import _export_digest

    commits = (head_sha,)
    return SealedExport(
        "b" * 40, head_sha, commits, (), _export_digest("b" * 40, head_sha, commits, ())
    )


# --- real git fixtures ----------------------------------------------------


def _git_env(identity: GitIdentity = QA_IDENTITY) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR")}
    env |= {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": identity.name,
        "GIT_AUTHOR_EMAIL": identity.email,
        "GIT_COMMITTER_NAME": identity.name,
        "GIT_COMMITTER_EMAIL": identity.email,
    }
    return env


def git(
    repo: Path, *args: str, identity: GitIdentity = QA_IDENTITY, input: str | None = None
) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=_git_env(identity),
        input=input,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def write_raw_tree(repo: Path, entries: list[tuple[bytes, bytes, str]]) -> str:
    """Write ``(mode, name, oid)`` entries verbatim as a tree object, bypassing git's checks."""
    raw = b"".join(mode + b" " + name + b"\0" + bytes.fromhex(oid) for mode, name, oid in entries)
    return (
        subprocess.run(
            ["git", "hash-object", "-t", "tree", "-w", "--literally", "--stdin"],
            cwd=repo,
            env=_git_env(QA_IDENTITY),
            input=raw,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .strip()
    )


def commit_file(
    repo: Path, path: str, content: str, message: str, identity: GitIdentity = QA_IDENTITY
) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git(repo, "add", path, identity=identity)
    git(repo, "commit", "-q", "--no-verify", "-m", message, identity=identity)
    return git(repo, "rev-parse", "HEAD")


def init_repo(repo: Path) -> str:
    """A repo with one trusted base commit; returns the base SHA."""
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    return commit_file(repo, "src/app.py", "value = 1\nowner = 'ops@example.com'\n", "base")
