"""Immutable public source controls for the run16 seam regressions.

Bodies are exact public source at 461e03b32ac3b88e2a92d487432f77a73aa2b829.
They execute against the same disposable migrated PostgreSQL fixture as the fix;
no shallow-checkout git fetch, applied migration edit or live connection is used.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_FIXTURE_SHA256 = "c247d36c3d64fcbb2f19421b66609e7c3c717b32182ffa4fb96624c75753132d"


def baseline_function(name: str, globals_: dict[str, Any]):
    raw = (Path(__file__).parent / "fixtures" / "three_seams_baseline.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _FIXTURE_SHA256
    item = json.loads(raw)[name]
    assert hashlib.sha256(item["body"].encode()).hexdigest() == item["body_sha256"]
    namespace = dict(globals_)
    exec(compile(item["body"], item["path"] + "@" + item["git_sha"], "exec"), namespace)
    return namespace[name]
