"""Closed, fixed-origin publication transport (REQ-qa-publication-authority-003).

The transport is the only caller of a :class:`PublicationProvider`, and the
provider protocol itself names only the four allowed effects.  Every request is
authorized against the dispatcher-held :class:`PublicationBinding` before any
provider call:

- a non-force ref update of the bound ``qa/`` branch to the bound head, with the
  expected old object id (``None`` means the branch must not exist yet);
- creating the bound PR (bound head branch onto the bound base), updating it,
  and applying exactly the fixed sanitized labels.

Anything else (merge, review, approval, queue, a protected or foreign ref, a
foreign repository or PR, force, deletion) raises :class:`TransportRefused` and
never reaches the provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from butlers.core.qa.publication.binding import PublicationBinding
from butlers.core.qa.publication.export import SealedExport

_SHA = re.compile(r"^[0-9a-f]{40}$")
_QA_BRANCH_PREFIX = "qa/"


class TransportRefused(RuntimeError):
    """The request is outside the closed operation set; no provider call was made."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StaleExpectedHead(RuntimeError):
    """The provider's atomic compare failed: the remote ref is not at the expected id."""


class ProviderRejected(RuntimeError):
    """The provider definitely refused the effect (nothing changed); ``code`` is content-free."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AmbiguousOutcome(RuntimeError):
    """The effect may or may not have happened (for example a lost response)."""


class PublicationProvider(Protocol):
    """The only effects a provider exposes.  There is no merge, review or generic call."""

    repository: str

    async def update_ref(
        self, *, branch: str, expected_old: str | None, new: str, export: SealedExport
    ) -> None:
        """Atomically move ``refs/heads/<branch>`` from ``expected_old`` to ``new``; never force."""
        ...

    async def create_pull_request(self, *, head: str, base: str, title: str, body: str) -> int:
        """Open a PR and return its number."""
        ...

    async def update_pull_request(self, *, number: int, title: str, body: str) -> None: ...

    async def set_labels(self, *, number: int, labels: tuple[str, ...]) -> None: ...


@dataclass(frozen=True, slots=True)
class BranchUpdate:
    repository: str
    branch: str
    expected_old: str | None
    new: str
    force: bool = False


@dataclass(frozen=True, slots=True)
class PullRequestCreate:
    repository: str
    head: str
    base: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class PullRequestUpdate:
    repository: str
    number: int
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class PullRequestLabels:
    repository: str
    number: int
    labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GenericOperation:
    """Any other provider operation (merge, review, approve, enqueue, ...): always refused."""

    repository: str
    name: str


TransportRequest = (
    BranchUpdate | PullRequestCreate | PullRequestUpdate | PullRequestLabels | GenericOperation
)


class ClosedTransport:
    """Authorizes each request against one binding, then calls the fixed-origin provider."""

    def __init__(
        self,
        provider: PublicationProvider,
        *,
        binding: PublicationBinding,
        labels: tuple[str, ...],
        export: SealedExport,
    ) -> None:
        if provider.repository != binding.repository:
            raise TransportRefused("foreign_origin")
        self._provider = provider
        self._binding = binding
        self._labels = labels
        self._export = export
        self._bound_pr = binding.pr_number

    @property
    def bound_pr(self) -> int | None:
        return self._bound_pr

    def adopt_created_pr(self, number: int) -> None:
        """Bind the PR a prior run of this attempt recorded as created in the ledger."""
        if self._bound_pr is not None and self._bound_pr != number:
            raise TransportRefused("foreign_pr")
        self._bound_pr = number

    def authorize(self, request: TransportRequest) -> None:
        """Refuse anything outside the closed set.  Pure: no provider call, no state."""
        binding = self._binding
        if request.repository != binding.repository:
            raise TransportRefused("foreign_repository")
        if isinstance(request, BranchUpdate):
            if request.force:
                raise TransportRefused("force_refused")
            if (
                request.branch != binding.branch
                or not binding.branch.startswith(_QA_BRANCH_PREFIX)
                or binding.branch == binding.base_ref
            ):
                raise TransportRefused("foreign_ref")
            if request.new != binding.head_sha or request.new != self._export.head_sha:
                raise TransportRefused("unbound_head")
            if request.expected_old is not None and not _SHA.fullmatch(request.expected_old):
                raise TransportRefused("invalid_expected_head")
            return
        if isinstance(request, PullRequestCreate):
            if self._bound_pr is not None:
                raise TransportRefused("pr_already_bound")
            if request.head != binding.branch or request.base != binding.base_ref:
                raise TransportRefused("foreign_ref")
            return
        if isinstance(request, PullRequestUpdate | PullRequestLabels):
            if self._bound_pr is None or request.number != self._bound_pr:
                raise TransportRefused("foreign_pr")
            if isinstance(request, PullRequestLabels) and request.labels != self._labels:
                raise TransportRefused("unbound_labels")
            return
        raise TransportRefused("unsupported_operation")

    async def execute(self, request: TransportRequest) -> int | None:
        """Authorize, verify the sealed export, then perform exactly one provider effect.

        Returns the PR number for a create, else ``None``.  Provider exceptions
        (:class:`StaleExpectedHead`, :class:`ProviderRejected`,
        :class:`AmbiguousOutcome`) propagate unchanged for the caller to record.
        """
        self.authorize(request)
        self._export.verify()
        provider = self._provider
        if isinstance(request, BranchUpdate):
            await provider.update_ref(
                branch=request.branch,
                expected_old=request.expected_old,
                new=request.new,
                export=self._export,
            )
            return None
        if isinstance(request, PullRequestCreate):
            number = await provider.create_pull_request(
                head=request.head, base=request.base, title=request.title, body=request.body
            )
            if not isinstance(number, int) or isinstance(number, bool) or number < 1:
                raise AmbiguousOutcome("provider returned no PR number")
            self._bound_pr = number
            return number
        if isinstance(request, PullRequestUpdate):
            await provider.update_pull_request(
                number=request.number, title=request.title, body=request.body
            )
            return None
        assert isinstance(request, PullRequestLabels)
        await provider.set_labels(number=request.number, labels=request.labels)
        return None
