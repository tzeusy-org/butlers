"""Roster code has one production loader and canonical per-process identity.

REQ-core-modules-004, REQ-dashboard-api-066. Private test copies and
Alembic/script loaders are separate from production roster execution.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
_ROOT = Path(__file__).resolve().parents[2]


def _child(code: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=_ROOT, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_api_and_daemon_await_the_same_registry():
    _child(r"""
import asyncio, importlib, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import httpx
from butlers.api.deps import get_db
from butlers.daemon import ButlerDaemon
from tests.api.auth_helpers import create_authenticated_domain_app

async def main():
 app=create_authenticated_domain_app()
 router=next(entry.module for entry in app.state.router_registry if entry.butler_name=='switchboard')
 registry=importlib.import_module('butlers.tools.switchboard.registry.registry')
 pool=SimpleNamespace(fetchrow=AsyncMock(side_effect=[None, {'eligibility_state':'active','last_seen_at':None}]),execute=AsyncMock(return_value='UPDATE 1'),fetchval=AsyncMock(return_value=1))
 app.dependency_overrides[get_db]=lambda:SimpleNamespace(pool=lambda name:pool)
 with patch.object(registry,'register_butler',new=AsyncMock()) as register:
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
   response=await client.post('/api/switchboard/heartbeat',json={'butler_name':'general'})
  assert response.status_code==200, response.status_code
  register.assert_awaited_once()
 with patch.object(registry,'seed_missing_roster_butlers',new=AsyncMock()) as seed:
  daemon=SimpleNamespace(_shutting_down=False,db=SimpleNamespace(pool=pool),config=SimpleNamespace(name='switchboard'),config_dir=Path.cwd()/'roster/switchboard',_boot_instance_id=uuid4())
  assert await ButlerDaemon._register_boot_epoch(daemon)
  seed.assert_awaited_once_with(pool,Path.cwd()/'roster')
 importlib.import_module('butlers.modules._roster_switchboard.tools')
 from butlers.scheduled_jobs import _load_switchboard_eligibility_sweep_job
 _load_switchboard_eligibility_sweep_job()
 source=Path(registry.__file__).resolve()
 assert {id(m) for m in tuple(sys.modules.values()) if getattr(m,'__file__',None) and Path(m.__file__).resolve()==source}=={id(registry)}
asyncio.run(main())
""")


def test_loaded_production_roster_files_have_one_identity():
    _child(r"""
import builtins, importlib, sys, tempfile
from collections import defaultdict
from pathlib import Path
from butlers.api.app import create_app
from butlers.api.router_discovery import discover_butler_routers
from butlers.modules.registry import default_registry
from butlers import scheduled_jobs
from butlers.roster_imports import roster_module
app=create_app()
default_registry()
for name in ('eligibility_sweep','rule_promotion_trigger','dropped_known_watch'):
 getattr(scheduled_jobs, '_load_switchboard_'+name+'_job')()
finance=next(e.module for e in app.state.router_registry if e.butler_name=='finance')
assert finance._load_facts_tools() is importlib.import_module('butlers.tools.finance.facts')
root=Path.cwd()/'roster'
identities=defaultdict(set)
for module in tuple(sys.modules.values()):
 file=getattr(module,'__file__',None)
 if file and Path(file).resolve().is_relative_to(root): identities[Path(file).resolve()].add(id(module))
assert identities and all(len(ids)==1 for ids in identities.values())
with tempfile.TemporaryDirectory() as temporary:
 custom_root=Path(temporary)/'roster'
 defaults={entry.butler_name:entry.module for entry in app.state.router_registry if entry.butler_name}
 for name, router in defaults.items():
  api=custom_root/name/'api';api.mkdir(parents=True)
  (api/'router.py').write_bytes(Path(router.__file__).read_bytes())
  (api/'models.py').write_bytes((root/name/'api/models.py').read_bytes()+b'\nCUSTOM_ROOT_CONTROL=True\n')
 custom=dict(discover_butler_routers(custom_root))
 assert set(custom)==set(defaults) and len(custom)==10
 for name, router in custom.items():
  models=getattr(router,'_models_module',getattr(router,'_models',None))
  default_models=roster_module(name,'api/models')
  assert Path(models.__file__).resolve()==custom_root/name/'api/models.py'
  assert models.CUSTOM_ROOT_CONTROL and models is not default_models
  assert default_models is importlib.import_module(f'butlers.api._roster.{name}.models')
  assert getattr(defaults[name],'_models_module',getattr(defaults[name],'_models',None)) is default_models
  again=dict(discover_butler_routers(custom_root))[name]
  assert again is router
  assert getattr(again,'_models_module',getattr(again,'_models',None)) is models
 # A broken or escaped adjacent file must not silently use healthy default models.
 broken=Path(temporary)/'broken';api=broken/'lifestyle/api';api.mkdir(parents=True)
 (api/'router.py').write_bytes((root/'lifestyle/api/router.py').read_bytes())
 assert discover_butler_routers(broken)==[]
 (api/'models.py').write_text("raise RuntimeError('custom models failure')\n")
 assert discover_butler_routers(broken)==[]
 assert not any(getattr(m,'__file__',None) and Path(m.__file__).resolve().is_relative_to(broken) for m in tuple(sys.modules.values()))
 (api/'models.py').write_bytes((root/'lifestyle/api/models.py').read_bytes())
 repaired=dict(discover_butler_routers(broken))['lifestyle']
 assert Path(repaired._models.__file__).resolve()==api/'models.py'
 escaped=Path(temporary)/'escaped';api=escaped/'lifestyle/api';api.mkdir(parents=True)
 (api/'router.py').write_bytes((root/'lifestyle/api/router.py').read_bytes())
 outside=Path(temporary)/'outside.py';outside.write_text('import builtins\nbuiltins._custom_model_escape_executed=True\nraise AssertionError("escaped model executed")\n')
 (api/'models.py').symlink_to(outside)
 assert discover_butler_routers(escaped)==[]
 assert not getattr(builtins,'_custom_model_escape_executed',False)
""")


def test_roster_loader_admission_cleanup_concurrency_and_aliases():
    _child(r"""
import importlib, pathlib, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
import butlers.roster_imports as owner
from butlers.tools._loader import register_butler_tools
original=owner._ROSTER
with tempfile.TemporaryDirectory() as tmp:
 root=pathlib.Path(tmp)/'roster';root.mkdir();owner._ROSTER=root
 tools=root/'controlled'/'tools';tools.mkdir(parents=True)
 source=tools/'__init__.py'
 source.write_text("raise RuntimeError('controlled failure')\n")
 name='butlers.tools.controlled'
 try: register_butler_tools('controlled',root/'controlled')
 except RuntimeError: pass
 else: raise AssertionError('failed tools body accepted')
 assert name not in sys.modules
 source.write_text("import time\ntime.sleep(0.02)\nidentity=object()\n")
 importlib.invalidate_caches()
 with ThreadPoolExecutor(max_workers=2) as threads:
  modules=list(threads.map(lambda _:owner.roster_module('controlled','tools'),range(2)))
 assert modules[0] is modules[1] is sys.modules[name]
 assert importlib.import_module('butlers.tools').controlled is modules[0]
 (tools/'not-valid.py').write_text('raise AssertionError("invalid-name body executed")\n')
 try: importlib.import_module('butlers.tools.controlled.not-valid')
 except ModuleNotFoundError: pass
 else: raise AssertionError('invalid direct tools name accepted')
 package=root/'controlled'/'modules';package.mkdir()
 (package/'__init__.py').write_text('identity=object()\n')
 (package/'not-valid.py').write_text('raise AssertionError("invalid module body executed")\n')
 importlib.import_module('butlers.modules._roster_controlled')
 try: importlib.import_module('butlers.modules._roster_controlled.not-valid')
 except ModuleNotFoundError: pass
 else: raise AssertionError('invalid direct module name accepted')
 api=root/'controlled'/'api';api.mkdir()
 (api/'models.py').write_text("raise RuntimeError('controlled models failure')\n")
 for _ in range(2):
  try: owner.roster_module('controlled','api/models')
  except RuntimeError: pass
  else: raise AssertionError('failed models body accepted')
  assert 'controlled_api_models' not in sys.modules
 import builtins
 from threading import Event
 from concurrent.futures import TimeoutError
 entered=Event();release=Event();attempted=Event()
 builtins._roster_model_events=(entered,release)
 builtins._roster_model_executions=0
 (api/'models.py').write_text('import builtins\nbuiltins._roster_model_executions+=1\nentered,release=builtins._roster_model_events\nentered.set()\nassert release.wait(5)\nidentity=object()\n');importlib.invalidate_caches()
 def second_load():
  attempted.set()
  return owner.roster_module('controlled','api/models')
 with ThreadPoolExecutor(max_workers=2) as threads:
  first=threads.submit(owner.roster_module,'controlled','api/models')
  assert entered.wait(5)
  second=threads.submit(second_load)
  try:
   assert attempted.wait(5)
   try: second.result(timeout=0.05)
   except TimeoutError: pass
   else: raise AssertionError('partially initialized API module was visible')
  finally: release.set()
  model=first.result(timeout=5)
  assert model is second.result(timeout=5) and builtins._roster_model_executions==1
 del builtins._roster_model_events,builtins._roster_model_executions
 assert model is importlib.import_module('butlers.api._roster.controlled.models')
 assert 'controlled_api_router' not in sys.modules
 (api/'router.py').write_text("from butlers.roster_imports import roster_module\n_models=roster_module('controlled','api/models')\n")
 router=owner.roster_module('controlled','api/router')
 assert router is importlib.import_module('butlers.api._roster.controlled.router')
 assert router._models is model
 jobs=root/'controlled'/'jobs';jobs.mkdir()
 (jobs/'controlled_jobs.py').write_text('identity=object()\n')
 (jobs/'refresh.py').write_text("raise RuntimeError('controlled child job failure')\n")
 try: owner.roster_module('controlled','jobs/refresh')
 except RuntimeError: pass
 else: raise AssertionError('failed child job accepted')
 assert 'butlers.jobs._roster.controlled.refresh' not in sys.modules
 (jobs/'refresh.py').write_text('identity=object()\n');importlib.invalidate_caches()
 flat=owner.roster_module('controlled','jobs/controlled_jobs')
 child=owner.roster_module('controlled','jobs/refresh')
 assert flat is importlib.import_module('butlers.jobs._roster.controlled_jobs')
 assert child is importlib.import_module('butlers.jobs._roster.controlled.refresh')
 assert flat is not child
 for path in ('jobs/__init__','jobs/controlled/refresh','jobs/../refresh','/tools','tools/../other','api/other','unknown'):
  try: owner.roster_module('controlled',path)
  except ValueError: pass
  else: raise AssertionError('unsupported roster path accepted')
 for butler in ('../controlled','Controlled','controlled.x','x/y',''):
  try: owner.roster_module(butler,'tools')
  except ValueError: pass
  else: raise AssertionError('invalid butler accepted')
 try: owner.roster_module('controlled_jobs','jobs/refresh')
 except ValueError: pass
 else: raise AssertionError('ambiguous job family accepted')
 outside=pathlib.Path(tmp)/'outside.py';outside.write_text('raise AssertionError("escaped")\n')
 (tools/'escape.py').symlink_to(outside)
 try: owner.roster_module('controlled','tools/escape')
 except ValueError: pass
 else: raise AssertionError('symlink escape accepted')
 try: importlib.import_module('butlers.tools.controlled.escape')
 except ValueError: pass
 else: raise AssertionError('direct import symlink escape accepted')
 escaped_api=root/'escaped'/'api';escaped_api.mkdir(parents=True)
 (escaped_api/'models.py').symlink_to(outside)
 try: owner.roster_module('escaped','api/models')
 except ValueError: pass
 else: raise AssertionError('API models symlink escape accepted')
 other=pathlib.Path(tmp)/'other';other.mkdir();(other/'tools.py').write_text('identity=object()\n')
 try: register_butler_tools('controlled',other)
 except ValueError: pass
 else: raise AssertionError('wrong tools root accepted')
 owner._ROSTER=original
""")


def _load_calls(tree):
    names = {"spec_from_file_location", "SourceFileLoader", "run_path"}
    aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in names
    }
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in names
            or isinstance(node.func, ast.Name)
            and node.func.id in names | aliases
        )
    ]


def _production_loads(root):
    found = {}
    for folder in ("src", "scripts", "roster"):
        for path in (root / folder).rglob("*.py"):
            rel = path.relative_to(root)
            if "tests" in rel.parts or path.name.startswith("test_"):
                continue
            calls = _load_calls(ast.parse(path.read_text()))
            if calls:
                found[str(rel)] = len(calls)
    return found


def test_production_roster_execution_has_one_owner(tmp_path):
    assert _production_loads(_ROOT) == {
        "src/butlers/roster_imports.py": 3,
        # Alembic revisions are not importable roster runtime modules.
        "src/butlers/bootstrap_prerequisite.py": 1,
        "src/butlers/migration_preflight.py": 1,
        # Dedicated private migration copies for tests, not roster runtime execution.
        "src/butlers/testing/migration.py": 1,
    }
    planted = tmp_path / "src" / "escape.py"
    planted.parent.mkdir()
    for source in (
        "from importlib.util import spec_from_file_location as load\nload('escape', path)\n",
        "import importlib.util as private\nprivate.spec_from_file_location('escape', path)\n",
        "from importlib.machinery import SourceFileLoader as load\nload('escape', path)\n",
        "import runpy\nrunpy.run_path(path)\n",
    ):
        planted.write_text(source)
        assert _production_loads(tmp_path) == {"src/escape.py": 1}
    planted.write_text(
        "import importlib\nmodule = importlib.import_module('butlers.tools.general')\n"
    )
    assert _production_loads(tmp_path) == {}


def _canonical_test_loads(paths):
    import re

    from butlers.testing.source_guard import local_bindings, scope_nodes, scopes

    canonical = re.compile(
        r"(?:[a-z][a-z0-9_]*_api_(?:router|models)|relationship_api_models_internal|butlers\..+)\Z"
    )
    found = []
    for path in paths:
        source = path.read_text()
        if not any(
            name in source for name in ("spec_from_file_location", "SourceFileLoader", "run_path")
        ):
            continue
        tree = ast.parse(source)
        load_ids = {id(call) for call in _load_calls(tree)}
        if not load_ids:
            continue
        module_bindings = local_bindings(scope_nodes(tree))
        for scope in scopes(tree):
            nodes = scope_nodes(scope)
            bindings = module_bindings | local_bindings(nodes)
            for call in nodes:
                if id(call) not in load_ids or not call.args:
                    continue
                value = call.args[0]
                visited = set()
                while (
                    isinstance(value, ast.Name) and value.id in bindings and value.id not in visited
                ):
                    visited.add(value.id)
                    value = bindings[value.id]
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    if canonical.fullmatch(value.value):
                        found.append((str(path), call.lineno))
    return found


def test_tests_do_not_execute_canonical_roster_names_by_path(tmp_path):
    paths = [*(_ROOT / "tests").rglob("*.py"), *(_ROOT / "roster").glob("*/tests/**/*.py")]
    assert _canonical_test_loads(paths) == []
    planted = tmp_path / "test_loader.py"
    planted.write_text(
        "import importlib.util as private\nNAME='health_api_router'\n"
        "def load():\n local=NAME\n return private.spec_from_file_location(local, path)\n"
    )
    assert len(_canonical_test_loads([planted])) == 1
    planted.write_text(
        "from importlib.util import spec_from_file_location as load\n"
        "name='butlers.modules._roster_health'\nload(name,path)\n"
    )
    assert len(_canonical_test_loads([planted])) == 1
    planted.write_text(
        "import importlib.util\nimportlib.util.spec_from_file_location('_private_router', path)\n"
    )
    assert _canonical_test_loads([planted]) == []
