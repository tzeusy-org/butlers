Reviewed commit: 2c4c54c685265e126f299b0295dff35cc89024e8

# Synthetic audit reproductions

Run from a checkout of the reviewed tree with its real installed environment. Save a block to an external temporary .py file and run `PYTHONPATH=.:src uv run --no-sync python /absolute/path/to/file.py`. Replace the hardcoded ROOT in the condensation example with that checkout. No real credentials, provider writes or live database connections are used. The Calendar carrier models SQL operations; it is not PostgreSQL concurrency evidence.

## F1: cross-owner uncertainty fence

```python
import asyncio, copy, uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from tests.modules.test_calendar_response import _CommandPool, _invitation
from butlers.modules.calendar import CalendarModule, CalendarConfig
from butlers.modules.calendar_response import CalendarResponseUncertainError
from butlers.core.approvals_hooks import DecisionDossier
from butlers.modules.approvals.execution_context import ApprovalExecutionContext, approval_tool_args_digest, set_approval_execution_context, reset_approval_execution_context

async def main():
    account={'entity_id':str(uuid.uuid4()), 'email':'owner@example.test'}
    resource=_invitation()
    after=copy.deepcopy(resource)
    after['etag']='"version-two"'
    after['attendees'][0]['responseStatus']='accepted'
    modules=[]
    for name in ['messenger','relationship']:
        target=dict(entry_id=uuid.uuid4(), source_id=uuid.uuid4(), event_id=uuid.uuid4(),instance_status='confirmed',event_status='confirmed',provider='google',source_kind='provider_event',lane='user',butler_name=None,writable=True,source_key='provider:google:owner',calendar_id='owner@example.test',provider_event_id='instance-one',source_metadata={'account_email':'owner@example.test'})
        mod=CalendarModule(); mod._db=SimpleNamespace(pool=_CommandPool(target)); mod._butler_name=name
        mod._config=CalendarConfig(provider='google',response_enabled=True); mod._response_ready=True
        mod._verified_response_account=AsyncMock(return_value=account)
        mod._project_provider_changes=AsyncMock()
        mod._provider=SimpleNamespace(get_response_calendar=AsyncMock(return_value={'id':'owner@example.test','accessRole':'writer'}),get_response_event=AsyncMock(return_value=resource),prepare_response_write=AsyncMock(return_value='synthetic'),respond_to_invitation=AsyncMock(return_value=after))
        modules.append(mod)
    modules[0]._provider.respond_to_invitation.side_effect=CalendarResponseUncertainError('response_transport_uncertain')
    async def park(conn, **kw):
        conn.pool.pending[kw['action_id']]={'tool_name':kw['tool_name'],'tool_args':kw['tool_args'],'status':'pending'}
    with patch('butlers.modules.calendar_response.park_pending_action', park):
        for mod in modules:
            req=dict(entry_id=str(mod._db.pool.target['entry_id']),request_id=str(uuid.uuid4()),response_status='accepted',send_updates='none')
            now=datetime.now(UTC)
            parked=await mod._response_coordinator.prepare_and_park(tool_args=req,requested_at=now,expires_at=now+timedelta(hours=1),dossier=DecisionDossier('Synthetic test',[],'self','compensable'))
            ident=uuid.UUID(parked['command_id']); pending=mod._db.pool.pending[ident]; args=pending['tool_args']
            pending.update(status='approved',decided_by='human:dashboard:rest-api')
            token=set_approval_execution_context(ApprovalExecutionContext(action_id=ident,session_id=None,actor=pending['decided_by'],tool_name='calendar_respond',tool_args_digest=approval_tool_args_digest(args),authorized_task=asyncio.current_task()))
            try: result=await mod._response_coordinator.execute(args)
            finally: reset_approval_execution_context(token)
            print(mod._butler_name, result['status'], 'provider writes=', mod._provider.respond_to_invitation.await_count)
    rows=[next(iter(m._db.pool.commands.values())) for m in modules]
    print('Same physical target key:', rows[0]['action_payload']['target_key']==rows[1]['action_payload']['target_key'])
    print('First fence still unresolved:', rows[0]['action_payload']['phase']=='uncertain')
asyncio.run(main())
```

## F2: conflicting invitation and source ownership

```python
from datetime import UTC,datetime,timedelta
from uuid import UUID
from butlers.api.read_models.calendar_workspace_v1 import _dedup_workspace_rows
from butlers.api.routers.calendar_workspace import _select_canonical_source_rows
now=datetime(2026,10,13,tzinfo=UTC)
rows=[]
for n,owner in enumerate(['finance','messenger'],1):
 rows.append(dict(instance_id=UUID(int=n),db_butler=owner,source_key='provider:google:owner@example.test',origin_ref='same-provider-event',recurrence_rule=None,title='Synthetic invitation',instance_starts_at=now,instance_updated_at=now if owner=='finance' else now-timedelta(minutes=5)))
sources=[]
for n,owner in enumerate(['finance','messenger'],1):
 sources.append(dict(source_id=UUID(int=n),db_butler=owner,source_key='provider:google:owner@example.test',source_kind='provider_event',lane='user',source_metadata={},last_success_at=now,last_synced_at=now))
rows.sort(key=lambda r:(r['instance_starts_at'],r['instance_id']))
survivors,_=_dedup_workspace_rows(rows)
canonical=_select_canonical_source_rows(sources,core_calendar_butlers={'messenger'})
print('Actual instance owner:',survivors[0]['db_butler'])
print('Advertised source owner:',canonical[0]['db_butler'])
print('Response enabled according to API:',canonical[0]['db_butler'] in {'messenger','relationship'})
```

## F4: executable test suppression

```python
"""Disposable committed repositories: no project or tracker writes."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path('/home/orca/orca/projects/butlers')
sys.path.insert(0, str(ROOT / 'scripts'))
import check_condensation_ledger as ledger

results = []
with tempfile.TemporaryDirectory(prefix='audit-condense-') as directory:
    root = Path(directory)
    (root / 'tests').mkdir()
    test = root / 'tests/test_a.py'
    test.write_text('def test_a():\n    assert False\n')

    def git(*arguments):
        return subprocess.check_output(
            ['git', '-C', directory, '-c', 'core.hooksPath=/dev/null', *arguments],
            stderr=subprocess.DEVNULL,
        ).decode().strip()

    git('init')
    git('config', 'user.name', 'Audit')
    git('config', 'user.email', 'audit@example.invalid')
    git('add', '.')
    git('commit', '-m', 'base')
    base = git('rev-parse', 'HEAD')
    for variant in ('baseline', 'committed-new-conftest', 'committed-appended-skip-attribute'):
        if variant == 'committed-new-conftest':
            (root / 'tests/conftest.py').write_text(
                'import pytest\n\n@pytest.fixture(autouse=True)\n'
                'def unavailable():\n    pytest.skip("unavailable")\n'
            )
        if variant == 'committed-appended-skip-attribute':
            (root / 'tests/conftest.py').unlink()
            test.write_text(
                'import pytest\n\ndef test_a():\n    assert False\n\n'
                'test_a.pytestmark = pytest.mark.skip(reason="unavailable")\n'
            )
        git('add', '.')
        git('commit', '--allow-empty', '-m', variant)
        execution = subprocess.run(
            [str(ROOT / '.venv/bin/python'), '-m', 'pytest', '-q',
             '-p', 'no:cacheprovider', 'tests/'],
            cwd=directory, capture_output=True, text=True,
            env={**__import__('os').environ, 'PYTHONDONTWRITEBYTECODE': '1'},
        )
        results.append({
            'variant': variant,
            'head': git('rev-parse', 'HEAD'),
            'tracked_dirty': git('status', '--porcelain', '--untracked-files=no'),
            'pytest_exit': execution.returncode,
            'pytest_summary': execution.stdout.strip().splitlines()[-1],
            'guard': ledger.verify(root, base, []),
        })
print(json.dumps(results, indent=2))
```
