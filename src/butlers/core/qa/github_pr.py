"""Live GitHub PR metadata fetch for the QA dossier PR panel.

The ``healing_attempts`` row persists only ``branch_name``/``pr_url``/``pr_number``
— it carries no CI status or diff stats. Rather than add a schema column and a
background sync (higher-risk), this module fetches CI status + diff-stat
(additions/deletions) live from the GitHub REST API on demand, behind a short
in-process TTL cache keyed by ``(owner, repo, pr_number)``.

Auth: a conventional GitHub token is resolved by the caller (CredentialStore
``BUTLERS_QA_GH_TOKEN`` first, then ``GITHUB_TOKEN`` / ``GH_TOKEN`` from the
environment) and passed in. When no token is available — or GitHub is
unreachable, rate-limited, or returns 401/403/404 — every accessor degrades
gracefully to ``None`` fields. The dossier never breaks because GitHub is
down: it falls back to the honest "unavailable" state.

NO persistence, NO migration: live-fetch + TTL cache only.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

# Compact CI status surfaced to the dossier PR panel. Mirrors the
# ``QaPrSummary.ci_status`` literal: passing / failing / pending / unknown.
CiStatus = Literal["passing", "failing", "pending", "unknown"]

# Cache entries live this long. Short enough to reflect a freshly-green PR
# soon after CI completes, long enough that repeated dossier loads of the same
# case do not hammer the GitHub API.
_CACHE_TTL_SECONDS = 60.0

_GITHUB_API_BASE = "https://api.github.com"
_REQUEST_TIMEOUT_SECONDS = 6.0

# https://github.com/<owner>/<repo>/pull/<n>  (tolerates trailing slash / query)
_PR_URL_RE = re.compile(
    r"github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/pull/(?P<number>\d+)",
)


@dataclass(frozen=True)
class PrMetadata:
    """Live GitHub metadata for a single PR.

    All fields are ``None`` when unavailable (no token, GitHub error, etc.) so
    the dossier can fall back to its honest "unavailable" presentation.
    """

    ci_status: CiStatus | None
    additions: int | None
    deletions: int | None


_UNAVAILABLE = PrMetadata(ci_status=None, additions=None, deletions=None)


def parse_pr_url(url: str | None) -> tuple[str, str, int] | None:
    """Parse ``(owner, repo, number)`` from a GitHub PR URL.

    Returns ``None`` when *url* is missing or not a recognizable PR URL.
    """
    if not url:
        return None
    m = _PR_URL_RE.search(url)
    if not m:
        return None
    repo = m.group("repo")
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    try:
        return m.group("owner"), repo, int(m.group("number"))
    except (TypeError, ValueError):
        return None


def _map_check_runs_to_ci_status(check_runs: list[dict]) -> CiStatus:
    """Collapse a list of GitHub check-runs into one compact CI status.

    Precedence: any incomplete run -> ``pending``; else any failed run ->
    ``failing``; else if at least one run succeeded -> ``passing``; otherwise
    ``unknown`` (no check runs at all).
    """
    if not check_runs:
        return "unknown"

    saw_success = False
    for run in check_runs:
        status = (run.get("status") or "").lower()
        if status != "completed":
            # queued / in_progress / waiting / pending / requested
            return "pending"
        conclusion = (run.get("conclusion") or "").lower()
        if conclusion in {"failure", "timed_out", "cancelled", "action_required", "stale"}:
            return "failing"
        if conclusion == "success":
            saw_success = True
        # neutral / skipped do not flip the verdict on their own

    return "passing" if saw_success else "unknown"


class GithubPrClient:
    """Fetches live PR metadata with an in-process TTL cache.

    A single instance is shared per process (see :func:`get_pr_client`). The
    cache is keyed by ``(owner, repo, number)`` and survives until the TTL
    expires. Errors are never raised to the caller — they resolve to the
    unavailable :data:`_UNAVAILABLE` sentinel.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = _CACHE_TTL_SECONDS,
        timeout_seconds: float = _REQUEST_TIMEOUT_SECONDS,
        monotonic=time.monotonic,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._timeout = timeout_seconds
        self._monotonic = monotonic
        # Injectable transport for tests (e.g. httpx.MockTransport). Production
        # leaves this None so httpx uses its default network transport.
        self._transport = transport
        self._cache: dict[tuple[str, str, int], tuple[float, PrMetadata]] = {}

    def _cache_get(self, key: tuple[str, str, int]) -> PrMetadata | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._monotonic() >= expires_at:
            self._cache.pop(key, None)
            return None
        return value

    def _cache_put(self, key: tuple[str, str, int], value: PrMetadata) -> None:
        self._cache[key] = (self._monotonic() + self._ttl, value)

    async def fetch(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        token: str | None,
    ) -> PrMetadata:
        """Return live PR metadata, honoring the TTL cache.

        Never raises: any missing token or GitHub failure resolves to the
        unavailable sentinel. Successful and unavailable results are both
        cached so a transient outage does not trigger a fetch storm.
        """
        key = (owner, repo, number)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        if not token:
            # No auth path: cache the unavailable result to avoid re-checking
            # on every dossier load within the TTL window.
            self._cache_put(key, _UNAVAILABLE)
            return _UNAVAILABLE

        result = await self._fetch_uncached(owner, repo, number, token)
        self._cache_put(key, result)
        return result

    async def _fetch_uncached(
        self,
        owner: str,
        repo: str,
        number: int,
        token: str,
    ) -> PrMetadata:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            async with httpx.AsyncClient(
                base_url=_GITHUB_API_BASE,
                headers=headers,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                pr_resp = await client.get(f"/repos/{owner}/{repo}/pulls/{number}")
                if pr_resp.status_code != 200:
                    logger.debug(
                        "GitHub PR fetch %s/%s#%s returned %s — degrading to unavailable",
                        owner,
                        repo,
                        number,
                        pr_resp.status_code,
                    )
                    return _UNAVAILABLE
                pr = pr_resp.json()
                additions = pr.get("additions")
                deletions = pr.get("deletions")
                head_sha = (pr.get("head") or {}).get("sha")

                ci_status: CiStatus | None = None
                if head_sha:
                    checks_resp = await client.get(
                        f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs"
                    )
                    if checks_resp.status_code == 200:
                        body = checks_resp.json()
                        runs = body.get("check_runs") or []
                        ci_status = _map_check_runs_to_ci_status(runs)
                    else:
                        logger.debug(
                            "GitHub check-runs fetch %s/%s@%s returned %s",
                            owner,
                            repo,
                            head_sha,
                            checks_resp.status_code,
                        )

                return PrMetadata(
                    ci_status=ci_status,
                    additions=additions if isinstance(additions, int) else None,
                    deletions=deletions if isinstance(deletions, int) else None,
                )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            # ValueError covers malformed JSON; never let GitHub break the dossier.
            logger.debug(
                "GitHub PR fetch %s/%s#%s failed (%s) — degrading to unavailable",
                owner,
                repo,
                number,
                exc.__class__.__name__,
            )
            return _UNAVAILABLE


# Process-wide singleton client (shared TTL cache across requests).
_CLIENT: GithubPrClient | None = None


def get_pr_client() -> GithubPrClient:
    """Return the process-wide :class:`GithubPrClient` singleton."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = GithubPrClient()
    return _CLIENT
