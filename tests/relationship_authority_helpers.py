"""Immutable historical writer control, available in shallow CI checkouts.

The complete public source is exact at the recorded protected commit. Its
virtual historical filename cannot claim the identity of current production
source. Execution uses current imported dependencies and the same disposable
database; this is a historical writer control, not a complete old-system run.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType

_FIXTURE_SHA256 = "25b34086a5fc03d84730873e0bff8c55505d45a20057935e6311b8a1aa4ae16a"
_BASE = "461e03b32ac3b88e2a92d487432f77a73aa2b829"
_PATH = "roster/relationship/tools/relationship_assert_fact.py"


def baseline_writer() -> ModuleType:
    raw = (Path(__file__).parent / "fixtures/relationship_authority_baseline.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _FIXTURE_SHA256
    fixture = json.loads(raw)
    assert fixture["git_sha"] == _BASE and fixture["path"] == _PATH
    assert hashlib.sha256(fixture["body"].encode()).hexdigest() == fixture["body_sha256"]
    historical = ModuleType("_relationship_authority_historical_writer")
    sys.modules[historical.__name__] = historical
    historical.__file__ = f"<relationship-authority-baseline:{_BASE}:{_PATH}>"
    exec(compile(fixture["body"], historical.__file__, "exec"), historical.__dict__)
    return historical
