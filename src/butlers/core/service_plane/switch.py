"""The default-off ``BUTLERS_SERVICE_PLANE_AUTH`` switch (RFC 0038 D7).

A leaf on purpose: the dashboard's ``butlers.api.deps`` reads it, and deps
sits inside the relationship Finder's no-LLM import walk
(``roster/relationship/tests/test_finder_no_llm_transitive.py``). Import only
the standard library here.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

SWITCH_ENV = "BUTLERS_SERVICE_PLANE_AUTH"


def service_plane_auth_enabled(environ: Mapping[str, str] = os.environ) -> bool:
    """The default-off verification switch for every service-plane check."""
    return environ.get(SWITCH_ENV) == "1"
