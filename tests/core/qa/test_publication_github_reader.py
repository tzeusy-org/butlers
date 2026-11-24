"""Read-only GitHub reader for QA publication reconciliation (bu-mc01y5.30).

REQ-qa-publication-authority-006: reconciliation observes the exact bound
resource read-only.  The reader issues only GETs against its fixed origin, and
anything it cannot prove (a 404 it cannot attribute to the ref, a foreign PR in
the listing, an unexpected payload) raises, which keeps the ledger hold.  No live
GitHub is contacted: every response comes from an ``httpx.MockTransport``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from butlers.core.qa.publication.github_reader import (
    GithubPublicationReader,
    PublicationReadError,
)
from butlers.core.qa.publication.reconcile import PullRequestView

pytestmark = pytest.mark.unit

_REPO = "acme/butlers"
_BRANCH = "qa/finance/abcdef123456-1"
_SHA = "c" * 40
_TOKEN = "synthetic-token-value"


def _pr(number: int, *, head: str = _BRANCH, base: str = "main", repo: str = _REPO) -> dict:
    return {
        "number": number,
        "head": {"ref": head, "repo": {"full_name": repo}},
        "base": {"ref": base, "repo": {"full_name": _REPO}},
    }


class _GitHub:
    """Routes requests to canned answers and records every request it saw."""

    def __init__(self, routes: dict[str, httpx.Response | list[httpx.Response]]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get(request.url.path, httpx.Response(500))
        if isinstance(answer, list):
            return answer.pop(0)
        return answer

    def reader(self, repository: str = _REPO) -> GithubPublicationReader:
        return GithubPublicationReader(
            repository, token=_TOKEN, transport=httpx.MockTransport(self)
        )


_REF_PATH = f"/repos/{_REPO}/git/ref/heads/{_BRANCH}"
_PULLS_PATH = f"/repos/{_REPO}/pulls"
_REPO_PATH = f"/repos/{_REPO}"


def _ref(sha: str = _SHA, *, ref: str = f"refs/heads/{_BRANCH}", kind: str = "commit") -> dict:
    return {"ref": ref, "object": {"type": kind, "sha": sha}}


async def test_read_ref_returns_the_exact_head_with_a_get_only() -> None:
    github = _GitHub({_REF_PATH: httpx.Response(200, json=_ref())})

    assert await github.reader().read_ref(branch=_BRANCH) == _SHA

    (request,) = github.requests
    assert request.method == "GET"
    assert request.headers["authorization"] == f"Bearer {_TOKEN}"


async def test_missing_ref_is_absent_only_once_the_repository_itself_is_confirmed() -> None:
    confirmed = _GitHub(
        {
            _REF_PATH: httpx.Response(404, json={"message": "Not Found"}),
            _REPO_PATH: httpx.Response(200, json={"full_name": "Acme/Butlers"}),
        }
    )
    assert await confirmed.reader().read_ref(branch=_BRANCH) is None
    assert [r.method for r in confirmed.requests] == ["GET", "GET"]

    # A 404 for the repository too (no access, renamed, wrong token) proves nothing.
    hidden = _GitHub(
        {
            _REF_PATH: httpx.Response(404, json={"message": "Not Found"}),
            _REPO_PATH: httpx.Response(404, json={"message": "Not Found"}),
        }
    )
    with pytest.raises(PublicationReadError) as exc:
        await hidden.reader().read_ref(branch=_BRANCH)
    assert exc.value.code == "repository_unconfirmed"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json=_ref(ref="refs/heads/qa/other")),
        httpx.Response(200, json=_ref(kind="tag")),
        httpx.Response(200, json=_ref(sha="C" * 40)),
        httpx.Response(200, json=[_ref()]),
        httpx.Response(200, text="not json"),
        httpx.Response(301, headers={"location": "https://api.github.com/repositories/1"}),
        httpx.Response(403, json={"message": "API rate limit exceeded"}),
        httpx.Response(502),
    ],
    ids=["other-ref", "not-commit", "bad-sha", "prefix-list", "not-json", "redirect", "403", "502"],
)
async def test_unprovable_ref_answer_raises(response: httpx.Response) -> None:
    github = _GitHub({_REF_PATH: response, _REPO_PATH: httpx.Response(200, json={})})

    with pytest.raises(PublicationReadError) as exc:
        await github.reader().read_ref(branch=_BRANCH)

    assert _TOKEN not in str(exc.value)


async def test_find_pull_requests_lists_every_state_for_the_bound_head_and_base() -> None:
    github = _GitHub({_PULLS_PATH: httpx.Response(200, json=[_pr(12), _pr(7)])})

    assert await github.reader().find_pull_requests(head=_BRANCH, base="main") == (7, 12)

    (request,) = github.requests
    assert request.method == "GET"
    params = request.url.params
    assert (params["state"], params["head"], params["base"]) == ("all", f"acme:{_BRANCH}", "main")


async def test_find_pull_requests_follows_full_pages_and_bounds_them() -> None:
    page = [_pr(n) for n in range(1, 101)]
    github = _GitHub(
        {_PULLS_PATH: [httpx.Response(200, json=page), httpx.Response(200, json=[_pr(101)])]}
    )
    assert len(await github.reader().find_pull_requests(head=_BRANCH, base="main")) == 101
    assert [r.url.params["page"] for r in github.requests] == ["1", "2"]

    endless = _GitHub({_PULLS_PATH: [httpx.Response(200, json=page) for _ in range(20)]})
    with pytest.raises(PublicationReadError) as exc:
        await endless.reader().find_pull_requests(head=_BRANCH, base="main")
    assert exc.value.code == "too_many_results"


@pytest.mark.parametrize(
    "item",
    [
        _pr(3, head="qa/other"),
        _pr(3, base="release"),
        _pr(3, repo="evil/fork"),
        {**_pr(3), "head": {"ref": _BRANCH, "repo": None}},
        {**_pr(3), "number": True},
        {**_pr(3), "number": 0},
    ],
    ids=["other-head", "other-base", "fork-head", "deleted-fork", "bool-number", "zero-number"],
)
async def test_any_pull_request_outside_the_bound_pair_raises(item: dict) -> None:
    github = _GitHub({_PULLS_PATH: httpx.Response(200, json=[_pr(1), item])})

    with pytest.raises(PublicationReadError) as exc:
        await github.reader().find_pull_requests(head=_BRANCH, base="main")

    assert exc.value.code == "unexpected_pull_request"


_PR_PATH = f"{_PULLS_PATH}/7"


def _pr_detail(**overrides) -> dict:
    labels = [{"name": "self-healing"}, {"name": "automated"}]
    return {**_pr(7), "title": "fix(qa): t", "body": None, "labels": labels, **overrides}


async def test_read_pull_request_reports_the_bound_pair_text_and_labels() -> None:
    github = _GitHub({_PR_PATH: httpx.Response(200, json=_pr_detail())})

    pr = await github.reader().read_pull_request(number=7)

    assert pr == PullRequestView(
        7, _BRANCH, "main", "fix(qa): t", "", ("self-healing", "automated")
    )
    assert [r.method for r in github.requests] == ["GET"]


async def test_missing_pull_request_is_none_only_once_the_repository_is_confirmed() -> None:
    github = _GitHub(
        {
            _PR_PATH: httpx.Response(404, json={"message": "Not Found"}),
            _REPO_PATH: httpx.Response(200, json={"full_name": _REPO}),
        }
    )
    assert await github.reader().read_pull_request(number=7) is None

    hidden = _GitHub({_PR_PATH: httpx.Response(404), _REPO_PATH: httpx.Response(404)})
    with pytest.raises(PublicationReadError) as exc:
        await hidden.reader().read_pull_request(number=7)
    assert exc.value.code == "repository_unconfirmed"


@pytest.mark.parametrize(
    "item",
    [
        _pr_detail(number=8),
        {**_pr_detail(), "head": {"ref": _BRANCH, "repo": {"full_name": "evil/fork"}}},
        _pr_detail(title=None),
        _pr_detail(labels=[{"name": 3}]),
        _pr_detail(labels="self-healing"),
    ],
    ids=["other-number", "fork-head", "no-title", "bad-label", "labels-not-list"],
)
async def test_unprovable_pull_request_answer_raises(item: dict) -> None:
    github = _GitHub({_PR_PATH: httpx.Response(200, json=item)})

    with pytest.raises(PublicationReadError) as exc:
        await github.reader().read_pull_request(number=7)

    assert exc.value.code == "unexpected_pull_request"


async def test_transport_failure_raises_without_its_message() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url} with {_TOKEN}")

    reader = GithubPublicationReader(_REPO, token=_TOKEN, transport=httpx.MockTransport(explode))
    with pytest.raises(PublicationReadError) as exc:
        await reader.find_pull_requests(head=_BRANCH, base="main")

    assert exc.value.code == "transport_error"
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
    assert _TOKEN not in str(exc.value)


@pytest.mark.parametrize(
    ("repository", "token"),
    [("acme", _TOKEN), ("acme/butlers/extra", _TOKEN), (_REPO, "")],
    ids=["no-owner", "extra-segment", "no-token"],
)
def test_reader_refuses_an_unbound_origin_or_missing_token(repository: str, token: str) -> None:
    with pytest.raises(ValueError):
        GithubPublicationReader(repository, token=token)


class _Chunks(httpx.AsyncByteStream):
    """A body with no ``Content-Length`` that records how much of it was pulled."""

    def __init__(self, chunk: bytes, count: int) -> None:
        self.chunk, self.count, self.sent = chunk, count, 0

    async def __aiter__(self):
        for _ in range(self.count):
            self.sent += 1
            yield self.chunk


_MIB = 1024 * 1024


async def test_declared_oversized_body_raises_before_it_is_read() -> None:
    body = _Chunks(b"x" * _MIB, 3)
    github = _GitHub(
        {_REF_PATH: httpx.Response(200, headers={"Content-Length": str(3 * _MIB)}, stream=body)}
    )

    with pytest.raises(PublicationReadError) as exc:
        await github.reader().read_ref(branch=_BRANCH)

    assert exc.value.code == "response_too_large"
    assert body.sent == 0


async def test_undeclared_oversized_body_raises_once_the_cap_is_passed() -> None:
    body = _Chunks(b"[" + b" " * (_MIB - 1), 100)
    github = _GitHub({_PULLS_PATH: httpx.Response(200, stream=body)})

    with pytest.raises(PublicationReadError) as exc:
        await github.reader().find_pull_requests(head=_BRANCH, base="main")

    assert exc.value.code == "response_too_large"
    assert body.sent == 3  # stopped just past 2 MiB, never buffered the remaining 97 MiB


async def test_body_within_the_cap_is_still_parsed_from_the_stream() -> None:
    payload = json.dumps(_ref()).encode()
    github = _GitHub({_REF_PATH: httpx.Response(200, stream=_Chunks(payload, 1))})

    assert await github.reader().read_ref(branch=_BRANCH) == _SHA


def test_environment_proxy_and_trust_settings_are_ignored(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")
    # No injected transport: the production client, which would mount env proxies if trusted.
    client = GithubPublicationReader(_REPO, token=_TOKEN)._client()

    assert client.trust_env is False
    assert not client._mounts  # no proxy route for api.github.com
