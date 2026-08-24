"""The gate between a mounted signer and a Dashboard that may use it.

REQ-core-credentials-002 activates the production signer mount and the signed
callers in one step, and the order inside that step is the whole point: a
Dashboard image that still carries a local model-adapter probe path must not be
able to sign, because it would then hold *both* a private signer and a direct
runtime child --- exactly the pairing the sandbox work (task 3.6b) exists to
prevent.

So the signer is not read directly by any caller.  Every signing path reaches
it through :func:`activated_signer_snapshot`, which first proves this image has
no dashboard-local probe left.  If one is reintroduced --- by a revert, a bad
merge, or a well-meaning fallback --- the snapshot becomes *unavailable* and
the client signs nothing.  The mount can be present and correct and the plane
still stays closed, which is the failure direction we want.

:data:`DEFERRED_LOCAL_PROBE_MODULES` is the other half.  Task 3.6b recorded the
model-settings Test/verify adapter callsites as the one deliberate exception it
was allowed to leave behind; this module is where that exception is written
down, and it is now empty.  Emptiness is the contract, not a coincidence: a
module added back to it would have to be added here, in front of a reviewer.

Nothing here reads, derives, or renders key material.  The guard's diagnostic
names our own modules and attributes and nothing from a provider, a request, or
a document.
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import Final

from butlers.core.runtime_probe_control.client import RuntimeProbeControlClient
from butlers.core.runtime_probe_control.keys import SignerSnapshot, signer_snapshot

logger = logging.getLogger(__name__)

#: Switchboard's MCP listener, which also carries the private control routes.
SWITCHBOARD_CONTROL_PORT: Final = 41100

#: Modules that once built a Dashboard-local verification adapter and are now
#: required to be clean.  The guard *imports* each one, so a module listed here
#: that no longer exists is a hard error rather than a silent pass.
GUARDED_MODULES: Final = (
    "butlers.api.routers.model_settings",
    "butlers.jobs.model_verify",
)

#: Attribute names that mean "this module can build or hold a runtime adapter".
#: ``get_adapter`` and ``resolve_provider_config`` are the two imports the old
#: dashboard probe path needed; ``_create_verification_adapter`` was the factory
#: it wrapped them in.  Any one of them back on a guarded module is a probe path
#: regardless of whether a request currently reaches it.
LOCAL_PROBE_SYMBOLS: Final = (
    "get_adapter",
    "resolve_provider_config",
    "_create_verification_adapter",
)

#: The deferred allowlist task 3.6b handed to this cutover, after the cutover.
#: It is empty and must stay empty; see the module docstring.
DEFERRED_LOCAL_PROBE_MODULES: Final[frozenset[str]] = frozenset()

#: The one diagnostic a blocked activation produces.  Fixed text: it reaches
#: logs, and every value that could have varied is a key-plane value.
LOCAL_PROBE_PRESENT_REASON: Final = "a dashboard-local model adapter probe path is still present"


def local_model_probe_callsites() -> tuple[str, ...]:
    """Every ``module.attribute`` on a guarded module that is a local probe path.

    Reported in sorted order so a diff of the guard's output is stable.  The
    empty tuple is the healthy state and the state this cutover asserts.
    """
    found: list[str] = []
    for module_name in GUARDED_MODULES:
        if module_name in DEFERRED_LOCAL_PROBE_MODULES:
            continue
        module = importlib.import_module(module_name)
        found.extend(
            f"{module_name}.{symbol}" for symbol in LOCAL_PROBE_SYMBOLS if hasattr(module, symbol)
        )
    return tuple(sorted(found))


def activated_signer_snapshot() -> SignerSnapshot:
    """The signer snapshot, or an unavailable one if this image may not sign.

    Deliberately not cached.  The check is a handful of ``getattr`` calls on
    already-imported modules, and recomputing it means the gate answers for the
    process as it is now rather than as it was at the first probe.
    """
    callsites = local_model_probe_callsites()
    if callsites:
        logger.error(
            "runtime-probe control signing is blocked: %s (%s)",
            LOCAL_PROBE_PRESENT_REASON,
            ", ".join(callsites),
        )
        return SignerSnapshot(unavailable_reason=LOCAL_PROBE_PRESENT_REASON)
    return signer_snapshot()


def switchboard_control_base_url() -> str:
    """Where Switchboard's private control plane lives, from this process.

    Mirrors the ``BUTLERS_HOST`` convention every other Dashboard-to-butler
    caller already uses (``butlers.api.deps.ButlerConnectionInfo.sse_url``):
    Compose sets it to the ``butlers-up`` service name on both Dashboard
    containers, and it is unset inside ``butlers-up`` itself, where the
    daemons see each other on loopback.
    """
    host = os.environ.get("BUTLERS_HOST") or "localhost"
    return f"http://{host}:{SWITCHBOARD_CONTROL_PORT}"


_clients: dict[str, RuntimeProbeControlClient] = {}


def probe_client(caller: str) -> RuntimeProbeControlClient:
    """The process-wide signed client for *caller*, built at most once.

    One instance per caller class keeps the readiness latch meaningful: the
    client asks Switchboard whether it can verify this signer until the answer
    is yes, and a fresh client per request would re-ask forever.
    """
    client = _clients.get(caller)
    if client is None:
        client = RuntimeProbeControlClient(
            switchboard_control_base_url(),
            caller=caller,
            signer=activated_signer_snapshot,
        )
        _clients[caller] = client
    return client


def _reset_clients_for_tests() -> None:
    """Drop the cached clients.  Tests only --- production rebuilds by restart."""
    _clients.clear()
