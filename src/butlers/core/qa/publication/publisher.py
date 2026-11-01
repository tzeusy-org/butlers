"""Trusted deterministic QA publisher (REQ-qa-publication-authority-003/-004).

Drives one attempt's publication: verify the sealed export, enforce the
repository whitelist, sanitize the PR text and labels, then run the closed
operation sequence ``branch_push`` -> ``pr_create`` -> ``pr_labels``.  Each
operation is persisted at ``bound`` and moved to ``egress_started`` (under a
lock on an active attempt) before its single provider call, then its outcome
is recorded.  An ambiguous result is recorded and stops the sequence: it is
never retried and nothing is deleted, so the ledger hold stands until an exact
read-only reconciliation.

The publisher is disabled unless a caller injects one (``QaDispatchConfig.publisher``);
production configuration never does until isolation is proven (bu-mc01y5.7).
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from butlers.core.healing.anonymizer import anonymize, sanitize_labels, validate_anonymized
from butlers.core.qa.publication import state
from butlers.core.qa.publication.binding import OperationKind, PublicationBinding, request_digest
from butlers.core.qa.publication.export import ExportRefused, SealedExport
from butlers.core.qa.publication.state import PublicationOperation, PublicationRefused
from butlers.core.qa.publication.transport import (
    AmbiguousOutcome,
    BranchUpdate,
    ClosedTransport,
    ProviderRejected,
    PublicationProvider,
    PullRequestCreate,
    PullRequestLabels,
    StaleExpectedHead,
    TransportRefused,
    TransportRequest,
)
from butlers.core.qa.repo_whitelist import RepoWhitelist

logger = logging.getLogger(__name__)

#: Markers of raw evidence-collection structures that must never reach a PR body.
_RAW_EVIDENCE_MARKERS = ("\n### Evidence\n", "\n## Evidence\n", "evidence_lines:")


class PublicationStatus(StrEnum):
    PUBLISHED = "published"
    ANONYMIZATION_FAILED = "anonymization_failed"
    REFUSED = "refused"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class PublicationResult:
    """Outcome of one publication.  ``category`` is stable and content-free."""

    status: PublicationStatus
    category: str
    pr_number: int | None = None
    pr_url: str | None = None


class PublicationLedger(Protocol):
    """Durable operation state; :class:`PoolLedger` binds it to the real ledger table."""

    async def begin(
        self,
        *,
        attempt_id: uuid.UUID,
        kind: OperationKind,
        binding: PublicationBinding,
        request_digest: str,
        expected_object_id: str | None,
    ) -> PublicationOperation: ...

    async def start_egress(self, operation_id: uuid.UUID) -> bool: ...

    async def record(
        self, operation_id: uuid.UUID, outcome: state.Outcome, *, new_object_id: str | None
    ) -> bool: ...


class PoolLedger:
    """:class:`PublicationLedger` over the real ledger table."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def begin(self, **kwargs: Any) -> PublicationOperation:
        return await state.begin_operation(self._pool, **kwargs)

    async def start_egress(self, operation_id: uuid.UUID) -> bool:
        return await state.mark_egress_started(self._pool, operation_id)

    async def record(
        self, operation_id: uuid.UUID, outcome: state.Outcome, *, new_object_id: str | None
    ) -> bool:
        return await state.record_outcome(
            self._pool, operation_id, outcome, new_object_id=new_object_id
        )


@dataclass(frozen=True, slots=True)
class _Sanitized:
    title: str
    body: str
    labels: tuple[str, ...]


def _sanitize(title: str, body: str, labels: list[str], repo_root: Path) -> _Sanitized | str:
    """Anonymize and validate every externally visible field; a string is the failure reason."""
    clean_title = anonymize(title, repo_root)
    clean_body = anonymize(body, repo_root)
    title_ok, _ = validate_anonymized(clean_title)
    body_ok, _ = validate_anonymized(clean_body)
    if not title_ok or not body_ok:
        return "residual sensitive content in PR text"
    if any(marker in clean_body for marker in _RAW_EVIDENCE_MARKERS):
        return "raw evidence marker in PR body"
    clean_labels, label_violations = sanitize_labels(labels, repo_root)
    if label_violations:
        return "residual sensitive content in PR labels"
    return _Sanitized(clean_title, clean_body, tuple(clean_labels))


def _pr_url(repository: str, number: int) -> str:
    return f"https://github.com/{repository}/pull/{number}"


class QaPublisher:
    """Publishes one attempt's sealed export through a closed transport."""

    def __init__(
        self,
        *,
        provider: PublicationProvider,
        ledger: PublicationLedger,
        repo_root: Path,
    ) -> None:
        self._provider = provider
        self._ledger = ledger
        self._repo_root = repo_root

    @property
    def repository(self) -> str:
        """The provider's fixed origin; the only repository a binding may name."""
        return self._provider.repository

    async def publish(
        self,
        *,
        attempt_id: uuid.UUID,
        binding: PublicationBinding,
        export: SealedExport,
        title: str,
        body: str,
        labels: list[str],
        whitelist: RepoWhitelist | None,
    ) -> PublicationResult:
        """Publish once; a ``None`` whitelist refuses (fail closed)."""
        refused = await self._preflight(binding, export, whitelist)
        if refused is not None:
            return refused
        sanitized = _sanitize(title, body, labels, self._repo_root)
        if isinstance(sanitized, str):
            return PublicationResult(PublicationStatus.ANONYMIZATION_FAILED, sanitized)

        transport = ClosedTransport(
            self._provider, binding=binding, labels=sanitized.labels, export=export
        )
        repository = binding.repository
        steps: list[tuple[OperationKind, Any]] = [
            (
                OperationKind.BRANCH_PUSH,
                lambda: BranchUpdate(repository, binding.branch, None, binding.head_sha),
            ),
            (
                OperationKind.PR_CREATE,
                lambda: PullRequestCreate(
                    repository, binding.branch, binding.base_ref, sanitized.title, sanitized.body
                ),
            ),
            (
                OperationKind.PR_LABELS,
                lambda: PullRequestLabels(repository, transport.bound_pr or 0, sanitized.labels),
            ),
        ]
        payloads = {
            OperationKind.BRANCH_PUSH: {"export": export.digest, "head": binding.head_sha},
            OperationKind.PR_CREATE: {"title": sanitized.title, "body": sanitized.body},
            OperationKind.PR_LABELS: {"labels": list(sanitized.labels)},
        }
        for kind, make_request in steps:
            op_binding = (
                dataclasses.replace(binding, pr_number=transport.bound_pr)
                if kind is OperationKind.PR_LABELS
                else binding
            )
            failure = await self._run_operation(
                attempt_id=attempt_id,
                kind=kind,
                binding=op_binding,
                payload_digest=request_digest(payloads[kind]),
                transport=transport,
                make_request=make_request,
            )
            if failure is not None:
                return dataclasses.replace(
                    failure,
                    pr_number=transport.bound_pr,
                    pr_url=_pr_url(repository, transport.bound_pr) if transport.bound_pr else None,
                )
        assert transport.bound_pr is not None
        return PublicationResult(
            PublicationStatus.PUBLISHED,
            "published",
            pr_number=transport.bound_pr,
            pr_url=_pr_url(repository, transport.bound_pr),
        )

    async def _preflight(
        self, binding: PublicationBinding, export: SealedExport, whitelist: RepoWhitelist | None
    ) -> PublicationResult | None:
        try:
            export.verify()
        except ExportRefused as exc:
            return PublicationResult(PublicationStatus.REFUSED, exc.code)
        if binding.head_sha != export.head_sha or binding.pr_number is not None:
            return PublicationResult(PublicationStatus.REFUSED, "unbound_head")
        if whitelist is None:
            return PublicationResult(PublicationStatus.REFUSED, "repo_not_whitelisted")
        await whitelist.ensure_loaded()
        allowed, _reason = whitelist.is_allowed(binding.repository)
        if not allowed:
            return PublicationResult(PublicationStatus.REFUSED, "repo_not_whitelisted")
        return None

    async def _run_operation(
        self,
        *,
        attempt_id: uuid.UUID,
        kind: OperationKind,
        binding: PublicationBinding,
        payload_digest: str,
        transport: ClosedTransport,
        make_request: Any,
    ) -> PublicationResult | None:
        """Run one operation; ``None`` means it succeeded (now or in an earlier run)."""
        try:
            request: TransportRequest = make_request()
            transport.authorize(request)
            operation = await self._ledger.begin(
                attempt_id=attempt_id,
                kind=kind,
                binding=binding,
                request_digest=payload_digest,
                expected_object_id=binding.head_sha if kind is OperationKind.BRANCH_PUSH else None,
            )
            if operation.stage != "bound":
                return self._resume(operation, kind, transport)
            if not await self._ledger.start_egress(operation.id):
                # Another publisher holds this operation; never egress twice.
                return PublicationResult(PublicationStatus.AMBIGUOUS, "concurrent_egress")
        except TransportRefused as exc:
            return PublicationResult(PublicationStatus.REFUSED, exc.code)
        except PublicationRefused as exc:
            return PublicationResult(PublicationStatus.REFUSED, exc.code)

        outcome: state.Outcome
        new_object_id: str | None = None
        result: PublicationResult | None = None
        try:
            number = await transport.execute(request)
            outcome = "succeeded"
            if kind is OperationKind.BRANCH_PUSH:
                new_object_id = binding.head_sha
            elif kind is OperationKind.PR_CREATE:
                new_object_id = str(number)
        except StaleExpectedHead:
            outcome = "failed"
            result = PublicationResult(PublicationStatus.FAILED, "stale_expected_head")
        except ProviderRejected as exc:
            outcome = "failed"
            result = PublicationResult(PublicationStatus.FAILED, f"provider_rejected_{exc.code}")
        except (TransportRefused, ExportRefused) as exc:
            # Refused after egress_started was persisted: nothing was sent.
            outcome = "failed"
            result = PublicationResult(PublicationStatus.REFUSED, exc.code)
        except AmbiguousOutcome:
            outcome = "ambiguous"
            result = PublicationResult(PublicationStatus.AMBIGUOUS, f"{kind.value}_ambiguous")
        except Exception:  # noqa: BLE001 — an unknown error after egress is ambiguous
            logger.warning("QA publication %s raised after egress started", kind, exc_info=True)
            outcome = "ambiguous"
            result = PublicationResult(PublicationStatus.AMBIGUOUS, f"{kind.value}_ambiguous")

        try:
            recorded = await self._ledger.record(operation.id, outcome, new_object_id=new_object_id)
        except PublicationRefused:
            recorded = False
        if not recorded:
            # The operation stays egress_started, which holds the lineage.
            return PublicationResult(PublicationStatus.AMBIGUOUS, "outcome_unrecorded")
        return result

    @staticmethod
    def _resume(
        operation: PublicationOperation, kind: OperationKind, transport: ClosedTransport
    ) -> PublicationResult | None:
        """A prior run already reached this operation: reuse success, never re-egress."""
        if operation.stage == "succeeded":
            if kind is OperationKind.PR_CREATE and operation.new_object_id:
                transport.adopt_created_pr(int(operation.new_object_id))
            return None
        if operation.holds_lineage:
            return PublicationResult(PublicationStatus.AMBIGUOUS, f"{kind.value}_held")
        return PublicationResult(PublicationStatus.FAILED, f"{kind.value}_{operation.stage}")
