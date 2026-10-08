"""Fresh-process import contracts; provider/model execution is never required.

REQ-module-memory-014, REQ-testing-040, REQ-testing-039,
REQ-core-modules-004, REQ-dashboard-api-066.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
_ROOT = Path(__file__).resolve().parents[2]
# Provisional cap calibrated from five clean post-change imports; see source receipt.
_CONFTST_IMPORT_BUDGET_SECONDS = 5


def _child(code: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=_ROOT, capture_output=True, text=True, timeout=40
    )
    assert result.returncode == 0, "fresh own-source import control failed"
    return json.loads(result.stdout)


def test_unused_imports_are_lazy_and_calibrated():
    for target in (
        "conftest",
        "butlers.modules.memory.storage",
        "butlers.modules.memory.tools._helpers",
        "butlers.modules.approvals.module",
        "butlers.daemon, butlers.api.app",
    ):
        data = _child(
            "import json,sys,time,resource; start=time.monotonic(); "
            f"import {target}; "
            "print(json.dumps({'elapsed':time.monotonic()-start,'rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"
            "'heavy':[x for x in ('torch','transformers','sentence_transformers') if x in sys.modules],"
            "'roster':[x for x in sys.modules if x.startswith(('butlers.modules._roster_','butlers.jobs._roster.'))]}))"
        )
        assert data["heavy"] == [], target
        if target == "conftest":
            assert data["roster"] == []
            assert data["elapsed"] <= _CONFTST_IMPORT_BUDGET_SECONDS


def test_canonical_memory_identity_and_first_use():
    data = _child("""
import json,sys,types
from butlers.modules.memory import storage,search,embedding,search_vector
from butlers.modules.memory.tools import _helpers
assert _helpers._storage is storage
assert _helpers._search is search
assert _helpers.EmbeddingEngine is storage.EmbeddingEngine is embedding.EmbeddingEngine
assert search.preprocess_search_query is search_vector.preprocess_search_query
assert not any(x in sys.modules for x in ('torch','transformers','sentence_transformers'))
# Controlled constructor/encoding seam: not pretrained weights or provider evidence.
backend=types.ModuleType('sentence_transformers'); calls=[]
class Vector:
 def tolist(self): return [1.0]*384
class Model:
 def __init__(self,name): calls.append(name)
 def encode(self,value,show_progress_bar=False):
  assert value == ' '
  return Vector()
backend.SentenceTransformer=Model
sys.modules['sentence_transformers']=backend
engine=embedding.EmbeddingEngine('configured-model')
assert engine.model_name == 'configured-model' and engine.dimension == 384
assert engine.embed(None) == [1.0]*384 and engine.embed_batch([]) == []
assert calls == ['configured-model']
print(json.dumps({'positive':True}))
""")
    assert data["positive"]
    installed = _child("""
import builtins,json,sys
from unittest.mock import patch
from butlers.modules.memory.embedding import EmbeddingEngine
assert not any(x in sys.modules for x in ('torch','transformers','sentence_transformers'))
original_import=builtins.__import__
class Constructor:
 def __init__(self,name): self.name=name
 def encode(self,*a,**k): raise AssertionError('No pretrained/provider execution')
def importing(name,*args,**kwargs):
 module=original_import(name,*args,**kwargs)
 if name == 'sentence_transformers': module.SentenceTransformer=Constructor
 return module
with patch('builtins.__import__', importing):
 engine=EmbeddingEngine('controlled-real-dependency-import')
assert all(x in sys.modules for x in ('torch','transformers','sentence_transformers'))
assert engine.model_name == 'controlled-real-dependency-import' and engine.dimension == 384
# Same canonical helper cache: failed construction leaves no entry; recovery caches it.
from butlers.modules.memory.tools import _helpers
with patch.object(_helpers,'EmbeddingEngine',side_effect=[RuntimeError('controlled constructor failure'),engine]) as build:
 try: _helpers.get_embedding_engine('controlled-cache')
 except RuntimeError: pass
 else: raise AssertionError('failure must propagate')
 assert 'controlled-cache' not in _helpers._embedding_engines
 assert _helpers.get_embedding_engine('controlled-cache') is engine
 assert _helpers.get_embedding_engine('controlled-cache') is engine
 assert build.call_count == 2
print(json.dumps({'positive':True}))
""")
    assert installed["positive"]


def test_roster_namespaces_share_real_demanded_modules():
    data = _child(r"""
import importlib,json,sys
from concurrent.futures import ThreadPoolExecutor
assert not any(k.startswith('butlers.modules._roster_') for k in sys.modules)
with ThreadPoolExecutor(max_workers=2) as workers:
 mods=list(workers.map(importlib.import_module,['butlers.modules._roster_relationship']*2))
assert mods[0] is mods[1]
assert 'butlers.modules._roster_health' not in sys.modules
from butlers.jobs._roster.health_jobs import compute_recovery_state
from butlers.jobs._roster_loader import load_roster_jobs
assert compute_recovery_state is load_roster_jobs('health').compute_recovery_state
from butlers.api._roster.health import models,router
from butlers.api.router_discovery import discover_butler_routers
assert models is router._models
found=dict(discover_butler_routers())
assert found['health'] is router
assert found['relationship'].router.routes
from butlers.entity_facts_channels import ef_predicate_to_ci_type
from butlers.tools.relationship._ef_channel_helpers import ef_predicate_to_ci_type as old_formatter
from butlers.entity_fact_attribution import attribution_response
from butlers.tools.relationship.fact_identity_decisions import attribution_response as old_response
assert old_formatter is ef_predicate_to_ci_type
assert old_response is attribution_response
for malformed in ('../health','health/jobs','health.foo'):
 try: load_roster_jobs(malformed)
 except ValueError: pass
 else: raise AssertionError('malformed roster job name accepted')
try: load_roster_jobs('missing_butler')
except FileNotFoundError: pass
else: raise AssertionError('missing job was not refused')
# Normal Python failed-load cleanup/retry, through the actual fixed namespace finder.
import tempfile,pathlib
import butlers.roster_imports as owning
import butlers.jobs._roster_loader as jobs
with tempfile.TemporaryDirectory() as directory:
 root=pathlib.Path(directory);owning._ROSTER=root
 jobs._roster_root=lambda: root
 package=root/'fault'/'modules';package.mkdir(parents=True)
 source=package/'__init__.py';source.write_text("raise RuntimeError('controlled module failure')\n")
 module_name='butlers.modules._roster_fault'
 try: importlib.import_module(module_name)
 except RuntimeError: pass
 else: raise AssertionError('module failure disappeared')
 assert module_name not in sys.modules
 source.write_text('identity = object()\n');importlib.invalidate_caches()
 first_retry=importlib.import_module(module_name)
 assert first_retry is importlib.import_module(module_name)
 jobdir=root/'fault'/'jobs';jobdir.mkdir()
 job=(jobdir/'fault_jobs.py');job.write_text("raise RuntimeError('controlled job failure')\n")
 job_name='butlers.jobs._roster.fault_jobs'
 try: jobs.load_roster_jobs('fault')
 except RuntimeError: pass
 else: raise AssertionError('job failure disappeared')
 assert job_name not in sys.modules and job_name not in jobs._MODULE_CACHE
 job.write_text('identity = object()\n');importlib.invalidate_caches()
 recovered=jobs.load_roster_jobs('fault')
 assert recovered is importlib.import_module(job_name) is jobs.load_roster_jobs('fault')
# The real discovery that follows must select the real checkout again.
owning._ROSTER=pathlib.Path.cwd()/'roster'
from butlers.modules.registry import default_registry
first=default_registry();second=default_registry()
assert first is not second and first.available_modules == second.available_modules
assert 'memory' in first.available_modules and 'relationship' in first.available_modules
print(json.dumps({'positive':True}))
""")
    assert data["positive"]
