"""Collectors: closed local facts become observations, and never claim more than was measured.

Two sources exist today. Credential states (the secrets inventory's own vocabulary) say whether a
provider's credential is usable; a direct, short-timeout probe of the conditions store says whether
that store answers. Free text never leaves this module: failures are classified by exception type
and dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Protocol

from butlers.capability_health.model import Observation

#: Credential key (``<scope>:<name>``) to the declared dependency it feeds.
CREDENTIAL_DEPENDENCIES = {
    "u:google": "google",
    "u:steam": "steam",
    "u:homeassistant": "home_assistant",
    "u:owntracks": "owntracks",
    "u:spotify": "spotify",
    "s:BUTLER_TELEGRAM_TOKEN": "telegram",
}
#: Credential state to ``(state, cause)``; ``None`` state is "could not measure".
_STATE_MAP: dict[str, tuple[str | None, str | None]] = {
    "ok": ("available", None),
    # Expiring still works; the lifecycle job notifies the owner about it.
    "expiring": ("available", None),
    "never_set": ("unconfigured", "credential_missing"),
    "expired": ("down", "oauth_revoked"),
    "failing": ("down", "unknown"),
}
PROBE_TIMEOUT_S = 2.0
ACQUIRE_TIMEOUT_S = 2.0
_PROBE_SQL = "SELECT 1 FROM public.condition_instances LIMIT 1"
_NETWORK = (TimeoutError, ConnectionError, OSError)


#: States that rest on a probe: the claim is as old as the probe, and without a probe time it is
#: no claim. ``never_set`` and ``expired`` are facts about the stored row, true as of the read.
_PROBED = frozenset({"ok", "expiring", "failing"})


def credential_observations(
    credentials: Iterable[tuple[str, str, datetime | None]], now: datetime
) -> list[Observation]:
    """Observations for ``(credential key, state, last probe time)`` that feed a dependency.

    A probe-backed state is observed when the provider was last probed, not when the inventory
    was read, so an old success ages out in the reducer instead of reading as current. A missing
    or timezone-naive probe time makes it unmeasured.
    """
    out = []
    for key, state, probed_at in credentials:
        dependency = CREDENTIAL_DEPENDENCIES.get(key)
        if dependency is None:
            continue
        mapped, cause = _STATE_MAP.get(state, (None, None))
        observed_at = now
        if state in _PROBED:
            if probed_at is None or probed_at.tzinfo is None:
                mapped, cause = None, None
            else:
                observed_at = probed_at
        out.append(Observation(dependency, mapped, cause, observed_at))
    return out


class ProbeConnection(Protocol):
    async def fetchval(self, query: str, *args: Any, timeout: float | None = None) -> Any: ...  # noqa: ASYNC109


class ProbePool(Protocol):
    async def acquire(self, *, timeout: float | None = None) -> ProbeConnection: ...  # noqa: ASYNC109

    async def release(self, connection: ProbeConnection) -> None: ...


async def probe_conditions(
    pool: ProbePool,
    *,
    acquire_s: float = ACQUIRE_TIMEOUT_S,
    deadline_s: float = PROBE_TIMEOUT_S,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Observation:
    """Ask the conditions store directly. Only a prompt answer is ``available``.

    Getting a connection from a busy shared pool says nothing about the store, so it is bounded
    separately and a wait there is unmeasured. Only the query is timed against the store: a
    timeout or connection failure is ``down``/``domain_unreachable``. Any other failure (a
    missing grant or table, a driver error) says nothing about reachability and is unmeasured.
    """
    started = clock()
    unmeasured = Observation("conditions", None, None, started)
    try:
        connection = await asyncio.wait_for(pool.acquire(timeout=acquire_s), acquire_s + 0.5)
    except TimeoutError:
        return unmeasured  # pool contention, not the store
    except PermissionError:
        return unmeasured
    except _NETWORK:
        return Observation("conditions", "down", "domain_unreachable", started)
    except Exception:
        return unmeasured
    try:
        await asyncio.wait_for(connection.fetchval(_PROBE_SQL, timeout=deadline_s), deadline_s)
    except PermissionError:  # an OSError, but a local fault that says nothing about the store
        return unmeasured
    except _NETWORK:
        return Observation("conditions", "down", "domain_unreachable", started)
    except Exception:
        return unmeasured
    finally:
        with contextlib.suppress(Exception):
            await pool.release(connection)
    return Observation("conditions", "available", None, started)
