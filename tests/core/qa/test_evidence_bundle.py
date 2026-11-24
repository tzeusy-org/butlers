"""The dispatcher-assembled evidence bundle (bu-ejow5p.13)."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.qa import evidence_bundle as eb
from butlers.core.qa.evidence_bundle import (
    EvidenceWithheld,
    build_evidence_bundle,
    write_bundle_file,
)
from butlers.core.qa.models import QaFinding
from butlers.core.qa.prompts import build_investigation_prompt

pytestmark = pytest.mark.unit

SESSION = uuid.UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 11, 24, 6, 0, tzinfo=UTC)


def _finding(**overrides) -> QaFinding:
    fields = dict(
        fingerprint="f" * 64,
        source_type="session_records",
        source_butler="finance",
        severity=2,
        exception_type="ValueError",
        event_summary="boom",
        call_site="src/butlers/example.py:12",
        occurrence_count=3,
        first_seen=NOW - timedelta(minutes=5),
        last_seen=NOW,
        timestamp=NOW,
        structured_evidence={"session_ids": [str(SESSION)]},
    )
    fields.update(overrides)
    return QaFinding(**fields)


def _session_row(error: str = "tool call failed") -> dict:
    return {
        "source_butler": "finance",
        "session_id": SESSION,
        "error": error,
        "status": "error",
        "trigger_source": "schedule",
        "started_at": NOW - timedelta(seconds=30),
        "completed_at": NOW,
    }


def _pool(*, sessions=None, tools=None, outcomes=None, fail: set[str] | None = None):
    fail = fail or set()

    async def fetch(sql: str, *_args):
        if "v_qa_recent_failures" in sql:
            key, rows = "sessions", sessions or []
        elif "v_qa_tool_call_failures" in sql:
            key, rows = "tools", tools or []
        else:
            key, rows = "outcomes", outcomes or []
        if key in fail:
            raise RuntimeError("raw driver text must never surface")
        return rows

    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=fetch)
    return pool


async def _no_history(_root, _finding) -> list[str]:
    return []


async def _build(pool, finding=None, **kwargs):
    return await build_evidence_bundle(
        pool,
        finding or _finding(),
        attempt_id=uuid.uuid4(),
        repo_root=Path("/repo"),
        git_history=_no_history,
        **kwargs,
    )


async def test_the_same_inputs_give_the_same_ids_and_digest() -> None:
    pool = _pool(sessions=[_session_row()])
    first, second = await _build(pool), await _build(pool)

    assert first.digest == second.digest
    assert [item.id for item in first.items] == ["E1"]
    assert first.items[0].kind == "session"
    assert first.items[0].digest == second.items[0].digest
    changed = await _build(_pool(sessions=[_session_row(error="a different failure")]))
    assert changed.digest != first.digest


async def test_every_body_is_anonymized_and_ids_run_across_sections() -> None:
    pool = _pool(
        sessions=[_session_row(error="contact owner@example.com about it")],
        tools=[
            {
                "source_butler": "finance",
                "session_id": SESSION,
                "tool_name": "ledger_append",
                "module_name": "finance",
                "error": "boom",
            }
        ],
        outcomes=[{"status": "failed", "error_detail": "timeout", "closed_at": NOW}],
    )
    bundle = await _build(pool)

    assert [item.id for item in bundle.items] == ["E1", "E2", "E3"]
    assert [item.kind for item in bundle.items] == ["session", "tool_failure", "dispatch_outcome"]
    assert "owner@example.com" not in json.dumps(bundle.to_dict())


async def test_a_body_that_cannot_be_made_clean_withholds_the_bundle(monkeypatch) -> None:
    monkeypatch.setattr(eb, "anonymize", lambda text, _root: text)  # a scrubber that missed it
    leaky = _pool(sessions=[_session_row(error="key sk-ant-api03-" + "a" * 40)])

    with pytest.raises(EvidenceWithheld):
        await _build(leaky)


async def test_an_unreadable_source_is_marked_unavailable_and_never_invented(caplog) -> None:
    pool = _pool(sessions=[_session_row()], fail={"tools"})

    bundle = await _build(pool)

    status = {section.name: section.status for section in bundle.sections}
    assert status["tool_failures"] == "unavailable"
    assert status["sessions"] == "available"
    assert next(s for s in bundle.sections if s.name == "tool_failures").items == ()
    assert "RuntimeError" in caplog.text
    assert "raw driver text" not in caplog.text  # class name only


async def test_the_bundle_is_capped_with_an_explicit_status(monkeypatch) -> None:
    monkeypatch.setattr(eb, "MAX_BUNDLE_BYTES", 5 * 1200)
    rows = [_session_row(error="x" * 300) for _ in range(5)]

    bundle = await _build(_pool(sessions=rows))

    section = next(s for s in bundle.sections if s.name == "sessions")
    assert section.status == "truncated"
    assert 0 < len(section.items) < 5
    serialized = len(json.dumps(bundle.to_dict()).encode())
    assert serialized <= 5 * 1200 + 2000  # the cap counts serialized items, not bare text


async def test_unused_section_budget_flows_to_later_sections_in_fixed_order(monkeypatch) -> None:
    monkeypatch.setattr(eb, "MAX_BUNDLE_BYTES", 5 * 700)
    sessions = [_session_row(error="x" * 300)]
    outcomes = [{"status": "failed", "error_detail": "y" * 300, "closed_at": NOW} for _ in range(5)]

    bundle = await _build(_pool(sessions=sessions, outcomes=outcomes))
    again = await _build(_pool(sessions=sessions, outcomes=outcomes))

    kept = next(s for s in bundle.sections if s.name == "recent_outcomes")
    assert len(kept.items) > 1  # the empty sections' share reached the last one
    assert bundle.digest == again.digest


async def test_the_manifest_carries_ids_and_digests_without_bodies() -> None:
    bundle = await _build(_pool(sessions=[_session_row()]))

    manifest = bundle.manifest()

    assert manifest["digest"] == bundle.digest
    assert manifest["sections"][0]["items"] == [
        {"id": "E1", "kind": "session", "digest": bundle.items[0].digest}
    ]
    assert "text" not in json.dumps(manifest)


# --- git history -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call_site",
    ["../etc/passwd:1", "-rf.py:3", "/abs/path.py:1", "no extension:1", "a/../b.py:2", "<unknown>"],
)
async def test_a_call_site_that_is_not_a_repo_relative_file_gets_no_history(
    tmp_path: Path, call_site: str
) -> None:
    assert await eb._git_history(tmp_path, _finding(call_site=call_site)) == []


async def test_git_history_runs_a_pinned_argv_with_no_config_or_drivers(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("x = 1\n")
    seen: dict = {}

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"abc1234 2026-11-20 fix the thing\n", b""

    async def fake_exec(*argv, **kwargs):
        seen["argv"], seen["env"] = argv, kwargs["env"]
        return _Proc()

    monkeypatch.setattr(eb.asyncio, "create_subprocess_exec", fake_exec)

    lines = await eb._git_history(tmp_path, _finding(call_site="src/x.py:9"))

    assert lines == ["src/x.py: abc1234 2026-11-20 fix the thing"]
    argv = seen["argv"]
    assert argv[:4] == ("git", "-C", str(tmp_path), "log")
    assert {"--no-ext-diff", "--no-textconv", "-n10"} <= set(argv)
    assert argv[-2:] == ("--", "src/x.py")  # the path cannot be read as an option
    env = seen["env"]
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["HOME"] != os.environ.get("HOME") and "qa-evidence-home-" in env["HOME"]
    assert env["GIT_CONFIG_KEY_0"] == "core.hooksPath"


async def test_git_history_reads_a_real_repository(tmp_path: Path) -> None:
    from tests.core.qa.publication_fakes import commit_file, init_repo

    init_repo(tmp_path)
    commit_file(tmp_path, "src/butlers/example.py", "a = 1\n", "add example")
    commit_file(tmp_path, "src/butlers/example.py", "a = 2\n", "change example")

    lines = await eb._git_history(tmp_path, _finding(call_site="src/butlers/example.py:1"))

    assert len(lines) == 2 and lines[0].endswith("change example")


# --- the file the agent reads ---------------------------------------------------------------


async def test_the_bundle_file_is_read_only_and_matches_the_record(tmp_path: Path) -> None:
    bundle = await _build(_pool(sessions=[_session_row()]))
    directory = tmp_path / "evidence"

    path = await asyncio.to_thread(write_bundle_file, bundle, directory)

    assert json.loads(path.read_text()) == bundle.to_dict()
    assert stat.S_IMODE(path.stat().st_mode) == 0o444
    assert directory.stat().st_mode & stat.S_IWUSR  # cleanup can still delete the directory


# --- the prompt ---------------------------------------------------------------------------


def test_the_prompt_points_to_the_bundle_and_no_longer_claims_it_is_all_there_is() -> None:
    finding = _finding()
    with_bundle = build_investigation_prompt(finding, uuid.uuid4(), None, "evidence/bundle.json")
    without = build_investigation_prompt(finding, uuid.uuid4())

    assert "evidence/bundle.json" in with_bundle and "## Evidence Bundle" in with_bundle
    assert "## Evidence Bundle" not in without
    for prompt in (with_bundle, without):
        assert "all the information available" not in prompt
        assert "no database, log or tracing access" in prompt


def _sign_head_with_a_fake_signature(repo: Path) -> None:
    """Rewrite HEAD with a gpgsig header so ``log.showSignature`` would invoke gpg.program."""
    import subprocess

    def git(*args: str, stdin: str | None = None) -> str:
        done = subprocess.run(
            ["git", "-C", str(repo), *args], input=stdin, capture_output=True, text=True, check=True
        )
        return done.stdout

    raw = git("cat-file", "commit", "HEAD")
    header, _, message = raw.partition("\n\n")
    signature = "gpgsig -----BEGIN PGP SIGNATURE-----\n \n AAAA\n -----END PGP SIGNATURE-----"
    git(
        "update-ref",
        "HEAD",
        git(
            "hash-object",
            "-t",
            "commit",
            "-w",
            "--stdin",
            stdin=f"{header}\n{signature}\n\n{message}",
        ).strip(),
    )


async def test_a_planted_repo_config_cannot_run_code_through_git_log(tmp_path: Path) -> None:
    from tests.core.qa.publication_fakes import commit_file, init_repo

    init_repo(tmp_path)
    commit_file(tmp_path, "src/butlers/example.py", "a = 1\n", "add example")
    await asyncio.to_thread(_sign_head_with_a_fake_signature, tmp_path)
    marker = tmp_path / "ran-marker"
    script = tmp_path / "planted.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    script.chmod(0o755)
    config = tmp_path / ".git" / "config"
    with config.open("a") as handle:
        handle.write(f"[log]\n\tshowSignature = true\n[gpg]\n\tprogram = {script}\n")
        handle.write(f"[core]\n\tpager = {script}\n\tfsmonitor = {script}\n")

    lines = await eb._git_history(tmp_path, _finding(call_site="src/butlers/example.py:1"))

    assert len(lines) == 1
    assert not marker.exists()


async def test_attempt_cleanup_can_remove_a_worktree_holding_the_bundle(tmp_path: Path) -> None:
    import subprocess

    from tests.core.qa.publication_fakes import commit_file, init_repo

    init_repo(tmp_path)
    commit_file(tmp_path, "a.txt", "a\n", "init")
    worktree = tmp_path / "wt"
    await asyncio.to_thread(
        subprocess.run,
        ["git", "-C", str(tmp_path), "worktree", "add", "-q", "-b", "qa/x", str(worktree)],
        check=True,
    )
    bundle = await _build(_pool(sessions=[_session_row()]))
    await asyncio.to_thread(write_bundle_file, bundle, worktree / ".tmp" / "qa-agent" / "evidence")

    done = await asyncio.to_thread(
        subprocess.run,
        ["git", "-C", str(tmp_path), "worktree", "remove", "--force", str(worktree)],
        capture_output=True,
    )

    assert done.returncode == 0 and not worktree.exists()
