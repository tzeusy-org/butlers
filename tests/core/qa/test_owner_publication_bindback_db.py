"""Synthetic GitHub observations against the real patrol and PostgreSQL seams."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.core.qa.dispatch import _PUBLICATION_UNAVAILABLE_DETAIL, check_open_pr_statuses
from butlers.core.qa.publication import owner_bindback
from butlers.core.qa.publication.github_reader import GithubPublicationReader
from butlers.core.qa.publication.proposal import retain_unpublished_proposal
from butlers.core.qa.publication.retention import retain_export
from butlers.modules.qa import QaModule
from tests.core.qa.publication_fakes import QA_IDENTITY, synthetic_export

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

REPO = "acme/butlers"
BRANCH = "qa/general/abc-1"


async def _candidate(pool, *, target=REPO, retain=True):
    patrol = await pool.fetchval(
        "INSERT INTO public.qa_patrols (status) VALUES ('clean') RETURNING id"
    )
    attempt = await pool.fetchval(
        """
        INSERT INTO public.healing_attempts
            (fingerprint, butler_name, status, severity, exception_type, call_site,
             qa_patrol_id, error_detail, branch_name)
        VALUES (gen_random_uuid()::text, 'general', 'failed', 2, 'Error', 'a:b', $1, $2, $3)
        RETURNING id
    """,
        patrol,
        _PUBLICATION_UNAVAILABLE_DETAIL,
        BRANCH,
    )
    if retain:
        async with pool.acquire() as conn:
            await conn.execute("SET ROLE butler_qa_rw")
            try:
                result = await retain_unpublished_proposal(
                    conn,
                    repo_root=Path("/tmp"),
                    attempt_id=attempt,
                    branch_name=BRANCH,
                    base_sha="b" * 40,
                    identity=QA_IDENTITY,
                    sealed_export=synthetic_export(),
                    repository=target,
                )
                assert result is None
            finally:
                await conn.execute("RESET ROLE")
    await pool.execute("""
        INSERT INTO public.qa_allowed_repositories (owner, repo) VALUES ('acme', 'butlers')
        ON CONFLICT (owner, repo) DO UPDATE SET enabled = true
    """)
    return attempt


def _reader(
    monkeypatch, *, head="c" * 40, repo=REPO, base="main", branch=BRANCH, numbers=(7,), fail=False
):
    def answer(request):
        if fail:
            return httpx.Response(503)
        side = {"ref": branch, "repo": {"full_name": repo}, "sha": head}
        base_side = {"ref": base, "repo": {"full_name": REPO}}
        item = {
            "number": 7,
            "head": side,
            "base": base_side,
            "title": "fix",
            "body": "",
            "labels": [],
        }
        if request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[dict(item, number=n) for n in numbers])
        return httpx.Response(200, json=item)

    monkeypatch.setattr(
        owner_bindback,
        "GithubPublicationReader",
        lambda repository, token: GithubPublicationReader(
            repository, token=token, transport=httpx.MockTransport(answer)
        ),
    )


async def _patrol(pool, monkeypatch):
    monkeypatch.setattr("butlers.modules.qa.check_open_pr_statuses", AsyncMock())
    monkeypatch.setattr("butlers.modules.qa.get_spawner", lambda: None)
    await QaModule()._check_pr_statuses(pool, "synthetic-token")


async def test_competing_patrols_bind_once_and_repeat_is_idempotent(db_pool, monkeypatch):
    attempt = await _candidate(db_pool)
    _reader(monkeypatch)
    original = owner_bindback.bind_owner_publications
    wins = []
    factory = owner_bindback.GithubPublicationReader
    ready = asyncio.Event()
    observations = 0

    def competing_reader(*args, **kwargs):
        reader = factory(*args, **kwargs)
        find = reader.find_pull_requests

        async def synchronized_find(**query):
            nonlocal observations
            result = await find(**query)
            observations += 1
            if observations == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=5)
            return result

        reader.find_pull_requests = synchronized_find
        return reader

    monkeypatch.setattr(owner_bindback, "GithubPublicationReader", competing_reader)

    async def recorded(*args, **kwargs):
        result = await original(*args, **kwargs)
        wins.append(result)
        return result

    monkeypatch.setattr(owner_bindback, "bind_owner_publications", recorded)
    await asyncio.gather(_patrol(db_pool, monkeypatch), _patrol(db_pool, monkeypatch))
    await _patrol(db_pool, monkeypatch)
    assert observations == 2
    assert sorted(wins) == [0, 0, 1]
    row = await db_pool.fetchrow(
        "SELECT status, pr_number, pr_url, publication_provenance "
        "FROM public.healing_attempts WHERE id=$1",
        attempt,
    )
    assert tuple(row.values()) == (
        "pr_open",
        7,
        "https://github.com/acme/butlers/pull/7",
        "owner_published",
    )


async def test_repo_configuration_change_does_not_retarget_export(db_pool, monkeypatch):
    attempt = await _candidate(db_pool)
    await db_pool.execute(
        "UPDATE public.qa_repo_config SET repo_url='https://github.com/other/repo'"
    )
    _reader(monkeypatch, repo="other/repo")
    await _patrol(db_pool, monkeypatch)
    assert (
        await db_pool.fetchval("SELECT status FROM public.healing_attempts WHERE id=$1", attempt)
        == "failed"
    )
    _reader(monkeypatch)
    await _patrol(db_pool, monkeypatch)
    assert (
        await db_pool.fetchval("SELECT status FROM public.healing_attempts WHERE id=$1", attempt)
        == "pr_open"
    )


async def test_bound_pr_continues_through_merge_tracking(db_pool, monkeypatch):
    attempt = await _candidate(db_pool)
    _reader(monkeypatch)
    await _patrol(db_pool, monkeypatch)
    process = MagicMock(returncode=0)
    process.communicate = AsyncMock(return_value=(json.dumps({"state": "MERGED"}).encode(), b""))
    monkeypatch.setattr(
        "butlers.core.qa.dispatch.asyncio.create_subprocess_exec", AsyncMock(return_value=process)
    )
    result = await check_open_pr_statuses(db_pool, Path("/tmp"), "synthetic-token")
    assert result["merged"] == 1
    assert (
        await db_pool.fetchval("SELECT status FROM public.healing_attempts WHERE id=$1", attempt)
        == "pr_merged"
    )


async def test_retained_target_cannot_be_rebound(db_pool):
    from butlers.core.qa.publication.state import PublicationRefused

    attempt = await _candidate(db_pool)
    async with db_pool.acquire() as conn:
        await conn.execute("SET ROLE butler_qa_rw")
        try:
            await retain_export(
                conn,
                attempt,
                synthetic_export(),
                target_repository=REPO,
                target_base_ref="main",
                target_branch=BRANCH,
            )
            with pytest.raises(PublicationRefused, match="export_target_changed"):
                await retain_export(
                    conn,
                    attempt,
                    synthetic_export(),
                    target_repository="other/repo",
                    target_base_ref="main",
                    target_branch=BRANCH,
                )
        finally:
            await conn.execute("RESET ROLE")


async def test_expiry_during_observation_is_rechecked_in_write(db_pool, monkeypatch):
    attempt = await _candidate(db_pool)
    _reader(monkeypatch)
    factory = owner_bindback.GithubPublicationReader

    def expiring_reader(*args, **kwargs):
        reader = factory(*args, **kwargs)
        read = reader.read_pull_request

        async def read_after_expiry(**query):
            result = await read(**query)
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "ALTER TABLE public.healing_attempts DISABLE TRIGGER healing_attempts_closed_at_stamp"
                )
                try:
                    await conn.execute(
                        "UPDATE public.healing_attempts SET closed_at=now()-interval '31 days' WHERE id=$1",
                        attempt,
                    )
                finally:
                    await conn.execute(
                        "ALTER TABLE public.healing_attempts ENABLE TRIGGER healing_attempts_closed_at_stamp"
                    )
            return result

        reader.read_pull_request = read_after_expiry
        return reader

    monkeypatch.setattr(owner_bindback, "GithubPublicationReader", expiring_reader)
    await _patrol(db_pool, monkeypatch)
    assert (
        await db_pool.fetchval("SELECT status FROM public.healing_attempts WHERE id=$1", attempt)
        == "failed"
    )


@pytest.mark.parametrize(
    "case",
    [
        "foreign_repo",
        "head",
        "branch",
        "attempt_branch",
        "base",
        "absent",
        "ambiguous",
        "unavailable",
        "missing_export",
        "unbound",
        "expired",
        "repo_changed",
    ],
)
async def test_unprovable_candidates_remain_unchanged(db_pool, monkeypatch, case):
    attempt = await _candidate(
        db_pool, target=None if case == "unbound" else REPO, retain=case != "missing_export"
    )
    if case == "attempt_branch":
        await db_pool.execute(
            "UPDATE public.healing_attempts SET branch_name='qa/other' WHERE id=$1", attempt
        )
    if case == "expired":
        # The real closed_at trigger stamps the terminal transition; advance DB time by
        # setting its timestamp as migration owner while disabling only that trigger.
        await db_pool.execute(
            "ALTER TABLE public.healing_attempts DISABLE TRIGGER healing_attempts_closed_at_stamp"
        )
        await db_pool.execute(
            "UPDATE public.healing_attempts SET closed_at=now()-interval '31 days' WHERE id=$1",
            attempt,
        )
        await db_pool.execute(
            "ALTER TABLE public.healing_attempts ENABLE TRIGGER healing_attempts_closed_at_stamp"
        )
    if case == "repo_changed":
        await db_pool.execute(
            "UPDATE public.qa_allowed_repositories SET enabled=false WHERE owner='acme' AND repo='butlers'"
        )
    _reader(
        monkeypatch,
        repo="fork/butlers" if case == "foreign_repo" else REPO,
        head="d" * 40 if case == "head" else "c" * 40,
        base="other" if case == "base" else "main",
        branch="qa/other" if case == "branch" else BRANCH,
        numbers=(7, 8) if case == "ambiguous" else (() if case == "absent" else (7,)),
        fail=case == "unavailable",
    )
    await _patrol(db_pool, monkeypatch)
    row = await db_pool.fetchrow(
        "SELECT status, pr_number, publication_provenance FROM public.healing_attempts WHERE id=$1",
        attempt,
    )
    assert tuple(row.values()) == ("failed", None, None)
