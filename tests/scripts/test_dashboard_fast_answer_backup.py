"""Separate complete fast-runtime snapshot path; software and real-PG scopes differ."""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import shutil
import subprocess
import time
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from butlers.core.dashboard_fast_answer_store import FastAnswerFence, FastAnswerStore, reply_payload
from butlers.core.dashboard_turns import request_cancel
from tests.integration.test_dashboard_fast_answer_db import _seed_turn
from tests.integration.test_dashboard_fast_answer_db import fast_db as owning_fast_db
from tests.scripts.test_pg_dump_backup import (
    _PSQL_SNAPSHOT_BARRIER,
    _SCRIPT,
    _backup_command,
    _run_backup_script,
)


@pytest_asyncio.fixture(name="fast_db", loop_scope="function")
async def _fast_runtime_db(postgres_container):
    # Invoke the actual owning fixture with its same function-loop lifetime.
    generator = owning_fast_db.__wrapped__(postgres_container)
    try:
        yield await anext(generator)
    finally:
        await generator.aclose()


@pytest.mark.unit
def test_actual_backup_fast_stage_rejects_unknown_or_failed_export_before_publication(tmp_path):
    """Execute the whole shell driver with source-bound command capture, no SQL."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "commands"
    psql = bin_dir / "psql"
    psql.write_text(
        r"""#!/usr/bin/env python3
import os, sys
from pathlib import Path
args=sys.argv[1:]
if '-c' in args:
    command=args[args.index('-c')+1]
    with open(os.environ['CAPTURE'],'a') as out: out.write(command+'\n')
    if 'butlers_fast_restore_authorized' in command:
        print('SELECT true AS butlers_fast_restore_authorized \\gset\n'
              '\\if :butlers_fast_restore_authorized\nBEGIN;\nSET ROLE butlers;')
    else:
        print('SELECT true AS butlers_cost_claim_restore_authorized \\gset\n'
              '\\if :butlers_cost_claim_restore_authorized\nSET ROLE butlers;')
    sys.exit(0)
target=None
captured=[]
for line in sys.stdin:
    captured.append(line)
    if line.startswith('\\o '): target=Path(line[3:].strip())
    elif line.strip()=='\\o' and target is not None:
        values={'id':'ABCDEF-012345','policy-count':'3',
                'fast-runtime-proof':os.environ.get('FAST_PROOF','ready')}
        target.write_text(values.get(target.name,'')+'\n')
        target=None
with open(os.environ['CAPTURE'],'a') as out: out.write(''.join(captured))
if any('SET TRANSACTION SNAPSHOT' in line for line in captured):
    if os.environ.get('EXPORT_FAIL')=='1': sys.exit(23)
    print('7b7d')
""",
        encoding="utf-8",
    )
    psql.chmod(0o755)
    dump = bin_dir / "pg_dump"
    dump.write_text(
        "#!/usr/bin/env python3\nimport os,sys\n"
        "with open(os.environ['CAPTURE'],'a') as f: f.write('PGDUMP '+repr(sys.argv[1:])+'\\n')\n"
        "print('-- controlled schema source\\n'+'SELECT 1;\\n'*300)\n",
        encoding="utf-8",
    )
    dump.chmod(0o755)
    for label, proof, fail, success in (
        ("healthy", "ready", "0", True),
        ("wrong-owner-policy", "refused", "0", False),
        ("unknown-importer", "", "0", False),
        ("failed-export", "ready", "1", False),
        ("restored", "ready", "0", True),
    ):
        target = tmp_path / label
        result = subprocess.run(
            ["sh", str(_SCRIPT)],
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "BACKUP_DIR": str(target),
                "CAPTURE": str(capture),
                "FAST_PROOF": proof,
                "EXPORT_FAIL": fail,
            },
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert (result.returncode == 0) is success
        files = list(target.glob("butlers_*.sql.gz"))
        assert bool(files) is success
        assert not list(target.glob("*.tmp"))
        assert json.loads((target / "last_run.json").read_text())["result"] == (
            "success" if success else "failed"
        )
        if success:
            content = gzip.decompress(files[0].read_bytes()).decode()
            assert "Butlers complete owner-only fast-runtime data" in content
            assert "public.dashboard_fast_answer_restore_row(" in content
            assert "fast runtime restored population differs" in content
            assert "SELECT public.cost_claim_restore_row(" in content
    commands = capture.read_text()
    assert "--exclude-table-data=public.dashboard_fast_answer_runtimes" in commands
    assert "--exclude-table=public.dashboard_fast_answer_runtimes" not in commands
    assert "SET TRANSACTION SNAPSHOT 'ABCDEF-012345'" in commands
    assert "pg_has_role(current_user,relowner,'MEMBER')" in commands
    assert "EXCEPT ALL" in commands and "SET LOCAL row_security=on" in commands


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_real_fast_runtime_snapshot_retains_fences_and_atomically_restores_all_rows(
    fast_db, postgres_container, tmp_path
):
    """Real owner/policy/typed rows, complete published stage and independent readback."""
    pool, role_pool, url = fast_db
    store = FastAnswerStore(role_pool)
    for state in ("active", "cancelled", "completed", "pending_reconciliation"):
        conversation, message, request = await _seed_turn(pool)
        session, owner = uuid4(), uuid4()
        registered = await store.register(message, request, session, owner)
        assert registered["outcome"] == "registered"
        fence = FastAnswerFence(message, session, owner, registered["lease_generation"])
        await store.intent(message, "answer")
        if state == "cancelled":
            await request_cancel(pool, message_id=message)
            assert (await store.control(fence, "cancelled"))["outcome"] == "cancelled"
        elif state == "pending_reconciliation":
            assert (await store.control(fence, "uncertain"))["outcome"] == state
            await pool.execute(
                "UPDATE public.dashboard_fast_answer_runtimes SET "
                "reconcile_anchor_expires_at=lease_expires_at,"
                "reconcile_deadline_at=lease_expires_at+interval '15 minutes',"
                "lease_generation=lease_generation+1 WHERE message_id=$1",
                message,
            )
        elif state == "completed":
            payload = reply_payload(
                message,
                conversation,
                "Controlled fleet reply.",
                [
                    {
                        "kind": "view",
                        "ref": "concierge.v_fleet_sessions",
                        "as_of": "2026-10-13T00:00:00Z",
                    }
                ],
            )
            assert (await store.control(fence, "reply_claim", {"digest": payload["digest"]}))[
                "outcome"
            ] == "accepted"
            assert (await store.control(fence, "reply", payload))["outcome"] == "completed"
    original = await pool.fetch("SELECT public.dashboard_fast_answer_backup_rows() AS row")
    expected = [row["row"] for row in original]
    assert len(expected) == 4 and {r["state"] for r in expected} == {
        "active",
        "cancelled",
        "completed",
        "pending_reconciliation",
    }
    assert all(r["lease_generation"] >= 1 for r in expected)
    assert any(r["reply_message_id"] is not None for r in expected)
    assert any(r["reconcile_deadline_at"] is not None for r in expected)
    metadata = await pool.fetchrow(
        "SELECT relrowsecurity,relforcerowsecurity,relowner FROM pg_class "
        "WHERE oid='public.dashboard_fast_answer_runtimes'::regclass"
    )
    assert metadata["relrowsecurity"] and metadata["relforcerowsecurity"]
    assert metadata["relowner"] == await pool.fetchval(
        "SELECT relowner FROM pg_class WHERE oid='public.dashboard_conversation_turns'::regclass"
    )
    backup_dir = tmp_path / "good"
    backup_dir.mkdir()
    backed_up = await asyncio.to_thread(
        _run_backup_script, url, backup_dir, str(postgres_container.get_exposed_port(5432))
    )
    assert backed_up.returncode == 0  # Operands and dump content never go into assertion text.
    files = list(backup_dir.glob("butlers_*.sql.gz"))
    assert len(files) == 1
    content = gzip.decompress(files[0].read_bytes()).decode()
    assert "CREATE TABLE public.dashboard_fast_answer_runtimes" in content
    assert "FORCE ROW LEVEL SECURITY" in content
    assert "fast_answer_definer_only" in content
    stage = content.split("-- Butlers complete owner-only fast-runtime data\n", 1)[1]
    lines = stage.split("FROM stdin;\n", 1)[1].split("\\.\n", 1)[0].splitlines()
    staged = [json.loads(bytes.fromhex(line).decode()) for line in lines]
    assert staged == expected
    # The exact emitted component is replayed with upstream turn/reply data
    # intact. This is not a whole-cluster bootstrap/disaster-recovery claim.
    await pool.execute("DELETE FROM public.dashboard_fast_answer_runtimes")
    from urllib.parse import urlparse

    parsed = urlparse(url)
    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--add-host=host.docker.internal:host-gateway",
        "-e",
        "PGPASSWORD_FOR_TEST",
        "postgres:17-alpine",
        "sh",
        "-c",
        'PGPASSWORD="$PGPASSWORD_FOR_TEST" psql --host=host.docker.internal '
        '--port="$1" --username="$2" --dbname="$3" --no-password --set=ON_ERROR_STOP=1',
        "restore-component",
        str(postgres_container.get_exposed_port(5432)),
        parsed.username,
        parsed.path.lstrip("/"),
    ]
    restored = await asyncio.to_thread(
        subprocess.run,
        command,
        input=stage,
        text=True,
        capture_output=True,
        env={**os.environ, "PGPASSWORD_FOR_TEST": parsed.password or ""},
    )
    assert restored.returncode == 0
    assert [
        r["row"]
        for r in await pool.fetch("SELECT public.dashboard_fast_answer_backup_rows() AS row")
    ] == expected
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.PostgresError):
            async with connection.transaction():
                await connection.execute("DELETE FROM public.dashboard_fast_answer_runtimes")
                await connection.execute(
                    "SELECT public.dashboard_fast_answer_restore_row($1::jsonb)", expected[0]
                )
                await connection.execute(
                    "SELECT public.dashboard_fast_answer_restore_row($1::jsonb)",
                    {"message_id": str(uuid4())},
                )
    assert [
        r["row"]
        for r in await pool.fetch("SELECT public.dashboard_fast_answer_backup_rows() AS row")
    ] == expected  # Independent acquisition confirms rollback, not a caught exception alone.
    async with pool.acquire() as connection:
        await connection.execute(
            "GRANT EXECUTE ON FUNCTION public.dashboard_fast_answer_restore_row(jsonb),public.dashboard_fast_answer_backup_rows() TO butler_general_rw"
        )
        try:
            await connection.execute("SET ROLE butler_general_rw")
            for call in (
                "SELECT public.dashboard_fast_answer_backup_rows()",
                "SELECT public.dashboard_fast_answer_restore_row('{}'::jsonb)",
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute(call)
        finally:
            await connection.execute("RESET ROLE")
            await connection.execute(
                "REVOKE EXECUTE ON FUNCTION public.dashboard_fast_answer_restore_row(jsonb),public.dashboard_fast_answer_backup_rows() FROM butler_general_rw"
            )
    for mode in ("policy", "importer"):
        target = tmp_path / mode
        target.mkdir()
        try:
            if mode == "policy":
                await pool.execute(
                    "ALTER POLICY fast_answer_definer_only ON public.dashboard_fast_answer_runtimes USING (false) WITH CHECK (false)"
                )
            else:
                await pool.execute(
                    "ALTER FUNCTION public.dashboard_fast_answer_restore_row(jsonb) RENAME TO fast_restore_unavailable"
                )
            refused = await asyncio.to_thread(
                _run_backup_script, url, target, str(postgres_container.get_exposed_port(5432))
            )
            assert refused.returncode != 0 and not list(target.glob("butlers_*.sql.gz"))
        finally:
            if mode == "policy":
                await pool.execute(
                    "ALTER POLICY fast_answer_definer_only ON public.dashboard_fast_answer_runtimes USING (current_user=pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid='public.dashboard_fast_answer_runtimes'::regclass))) WITH CHECK (current_user=pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid='public.dashboard_fast_answer_runtimes'::regclass)))"
                )
            else:
                await pool.execute(
                    "ALTER FUNCTION public.fast_restore_unavailable(jsonb) RENAME TO dashboard_fast_answer_restore_row"
                )
    assert [
        r["row"]
        for r in await pool.fetch("SELECT public.dashboard_fast_answer_backup_rows() AS row")
    ] == expected
    # Plant drift after the script's ordinary precheck but before its actual
    # exported snapshot. The full current policy admission must refuse it.
    drift = tmp_path / "snapshot-drift"
    drift.mkdir()
    barrier_bin = tmp_path / "snapshot-barrier-bin"
    barrier_bin.mkdir()
    wrapper = barrier_bin / "psql"
    wrapper.write_text(_PSQL_SNAPSHOT_BARRIER)
    wrapper.chmod(0o755)
    ready, release = drift / "snapshot-ready", drift / "snapshot-release"
    drift_command, drift_env = _backup_command(
        url,
        drift,
        str(postgres_container.get_exposed_port(5432)),
        extra_env={
            "BACKUP_TEST_SNAPSHOT_READY": "/backups/snapshot-ready",
            "BACKUP_TEST_SNAPSHOT_RELEASE": "/backups/snapshot-release",
            "PATH": "/backup-test-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        },
        extra_mounts=[(barrier_bin, "/backup-test-bin")],
    )
    process = subprocess.Popen(
        drift_command, env=drift_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        deadline = time.monotonic() + 30
        while not ready.exists():
            assert process.poll() is None, "backup ended before controlled snapshot admission"
            assert time.monotonic() < deadline, "backup did not reach controlled snapshot admission"
            await asyncio.sleep(0.05)
        await pool.execute(
            "CREATE POLICY fast_runtime_snapshot_drift ON public.dashboard_fast_answer_runtimes "
            "AS RESTRICTIVE FOR ALL USING (false)"
        )
        release.touch()
        await asyncio.to_thread(process.communicate, timeout=60)
        assert process.returncode != 0
        assert not list(drift.glob("butlers_*.sql.gz"))
        assert json.loads((drift / "last_run.json").read_text())["result"] == "failed"
    finally:
        release.touch(exist_ok=True)
        if process.poll() is None:
            process.terminate()
            await asyncio.to_thread(process.communicate, timeout=30)
        await pool.execute(
            "DROP POLICY IF EXISTS fast_runtime_snapshot_drift ON public.dashboard_fast_answer_runtimes"
        )
    restored_target = tmp_path / "restored-policy"
    restored_target.mkdir()
    restored_backup = await asyncio.to_thread(
        _run_backup_script, url, restored_target, str(postgres_container.get_exposed_port(5432))
    )
    assert restored_backup.returncode == 0
    restored_content = gzip.decompress(
        next(restored_target.glob("butlers_*.sql.gz")).read_bytes()
    ).decode()
    restored_lines = restored_content.split(
        "-- Butlers complete owner-only fast-runtime data\n", 1
    )[1]
    restored_lines = restored_lines.split("FROM stdin;\n", 1)[1].split("\\.\n", 1)[0].splitlines()
    assert [json.loads(bytes.fromhex(line).decode()) for line in restored_lines] == expected
