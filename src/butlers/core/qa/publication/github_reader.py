"""Read-only GitHub :class:`~butlers.core.qa.publication.reconcile.PublicationReader`.

Reconciliation (REQ-qa-publication-authority-006) needs exactly two observations
of the fixed-origin repository: where a ``qa/`` branch points, and which PRs run
from a head branch onto a base.  This reader makes them with ``GET`` requests
only; it has no method that could send anything else, and it never follows a
redirect, so a renamed or transferred repository is an error, not a new origin.

It proves what it reports or raises :class:`PublicationReadError`, and the
reconciler keeps the hold on any error:

- A ref 404 is reported absent only after the repository itself answers 200
  with its own name; GitHub also answers 404 for a repository the token cannot
  see, and that must never read as "branch missing".
- Every listed PR must run from the bound head branch in this repository onto
  the bound base in this repository.  Anything else means the filter did not
  hold and the listing proves nothing.

The token is supplied by the caller from existing secret plumbing and is only
ever placed in the ``Authorization`` header.  Errors carry a closed, content-free
``code`` and suppress the underlying exception, whose text can echo the URL or
provider content.
"""

from __future__ import annotations

import re
from typing import Any, TypeGuard
from urllib.parse import quote

import httpx

_GITHUB_API_BASE = "https://api.github.com"
_REQUEST_TIMEOUT_SECONDS = 10.0
_PAGE_SIZE = 100
_MAX_PAGES = 10
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


class PublicationReadError(RuntimeError):
    """The observation could not be proven; ``code`` is a stable, content-free category."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _ref_name(value: str) -> str:
    if not _REF.fullmatch(value) or ".." in value or value.endswith((".lock", "/", ".")):
        raise PublicationReadError("invalid_ref")
    return value


def _is_pr_number(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


class GithubPublicationReader:
    """Read-only, fixed-origin view of one GitHub repository."""

    def __init__(
        self,
        repository: str,
        *,
        token: str,
        base_url: str = _GITHUB_API_BASE,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not _REPOSITORY.fullmatch(repository):
            raise ValueError("repository must be owner/name")
        if not isinstance(token, str) or not token:
            raise ValueError("a GitHub token is required")
        self.repository = repository
        self._owner = repository.split("/", 1)[0]
        self._token = token
        self._base_url = base_url.rstrip("/")
        # Injectable for tests (httpx.MockTransport); production uses the default.
        self._transport = transport

    async def _get(self, path: str, params: dict[str, str] | None = None) -> tuple[int, Any]:
        """GET ``path``; return ``(status, json)`` for 200 or 404 and raise for anything else."""
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    path,
                    params=params,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {self._token}",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
        except httpx.HTTPError:
            raise PublicationReadError("transport_error") from None
        if response.status_code == 404:
            return 404, None
        if response.status_code in (403, 429):
            raise PublicationReadError("rate_limited_or_forbidden")
        if response.status_code != 200:
            raise PublicationReadError("unexpected_status")
        try:
            return 200, response.json()
        except ValueError:
            raise PublicationReadError("invalid_response") from None

    async def _confirm_repository(self) -> None:
        status, body = await self._get(f"/repos/{self.repository}")
        name = body.get("full_name") if status == 200 and isinstance(body, dict) else None
        if not isinstance(name, str) or name.lower() != self.repository.lower():
            raise PublicationReadError("repository_unconfirmed")

    async def read_ref(self, *, branch: str) -> str | None:
        """The object id ``refs/heads/<branch>`` points at, or ``None`` if provably absent."""
        branch = _ref_name(branch)
        # ``git/ref`` (singular) matches exactly; ``git/refs`` would prefix-match.
        status, body = await self._get(
            f"/repos/{self.repository}/git/ref/heads/{quote(branch, safe='/')}"
        )
        if status == 404:
            await self._confirm_repository()
            return None
        if not isinstance(body, dict) or body.get("ref") != f"refs/heads/{branch}":
            raise PublicationReadError("invalid_response")
        target = body.get("object")
        if not isinstance(target, dict) or target.get("type") != "commit":
            raise PublicationReadError("invalid_response")
        sha = target.get("sha")
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise PublicationReadError("invalid_response")
        return sha

    async def find_pull_requests(self, *, head: str, base: str) -> tuple[int, ...]:
        """The numbers of every PR (any state) from ``head`` in this repository onto ``base``."""
        head, base = _ref_name(head), _ref_name(base)
        numbers: set[int] = set()
        for page in range(1, _MAX_PAGES + 1):
            status, items = await self._get(
                f"/repos/{self.repository}/pulls",
                {
                    "state": "all",
                    "head": f"{self._owner}:{head}",
                    "base": base,
                    "per_page": str(_PAGE_SIZE),
                    "page": str(page),
                },
            )
            if status != 200 or not isinstance(items, list):
                raise PublicationReadError("invalid_response")
            for item in items:
                numbers.add(self._bound_pr_number(item, head=head, base=base))
            if len(items) < _PAGE_SIZE:
                return tuple(sorted(numbers))
        raise PublicationReadError("too_many_results")

    def _in_this_repository(self, side: object, ref: str) -> bool:
        """``side`` (a PR's ``head`` or ``base``) is ``ref`` in this repository."""
        if not isinstance(side, dict) or side.get("ref") != ref:
            return False
        repo = side.get("repo")
        full_name = repo.get("full_name") if isinstance(repo, dict) else None
        return isinstance(full_name, str) and full_name.lower() == self.repository.lower()

    def _bound_pr_number(self, item: object, *, head: str, base: str) -> int:
        if not isinstance(item, dict):
            raise PublicationReadError("unexpected_pull_request")
        number = item.get("number")
        if (
            not _is_pr_number(number)
            or not self._in_this_repository(item.get("head"), head)
            or not self._in_this_repository(item.get("base"), base)
        ):
            raise PublicationReadError("unexpected_pull_request")
        return number
