"""Engineering-executor launch profile (RFC 0038 D18, bu-ejow5p.5).

A ``qa`` session is a repository engineer in an isolated worktree, not a butler. It gets one small
core-owned system prompt: no butler persona, memory, situational or timezone preamble, blind-spot
disclosure or DB prompt override. The profile is versioned; its receipt names the single source
:data:`EXECUTOR_PROFILE_SOURCE`, so a session's ``prompt_provenance`` proves what it was given.

A missing profile fails closed (:class:`ExecutorProfileUnavailable`): the launch is refused rather
than falling back to the persona prompt.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

from butlers.core.spawner_context import EffectiveSystemPromptReceipt, PromptProvenanceEntry

PROFILE_VERSION: Final[int] = 1
EXECUTOR_PROFILE_SOURCE: Final[str] = f"engineering_executor_profile@v{PROFILE_VERSION}"
#: Trigger sources launched with the executor profile. Healing is a separate follow-up.
EXECUTOR_TRIGGER_SOURCES: Final[frozenset[str]] = frozenset({"qa"})
#: Dispatch reason when the profile cannot be read; not counted toward the circuit breaker.
PROFILE_UNAVAILABLE_REASON: Final[str] = "executor_profile_unavailable"

#: Dispatch-attempt outcome for a provider auth failure inside the executor sandbox. Not in the
#: breaker's qualifying outcomes, so it never opens the fleet-visible catalog-entry breaker.
EXECUTOR_ENV_UNFIT_OUTCOME: Final[str] = "executor_env_unfit"

_PROFILE_PATH = Path(__file__).with_name("executor_system_prompt.md")


class ExecutorProfileUnavailable(RuntimeError):
    """The executor system prompt could not be read."""


def is_executor_trigger(trigger_source: str | None) -> bool:
    return trigger_source in EXECUTOR_TRIGGER_SOURCES


def executor_prompt_receipt() -> EffectiveSystemPromptReceipt:
    """Return the executor prompt with a single-source, content-blind receipt."""
    try:
        prompt = _PROFILE_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ExecutorProfileUnavailable(PROFILE_UNAVAILABLE_REASON) from exc
    if not prompt:
        raise ExecutorProfileUnavailable(PROFILE_UNAVAILABLE_REASON)
    encoded = prompt.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return EffectiveSystemPromptReceipt(
        prompt=prompt,
        digest=digest,
        total_bytes=len(encoded),
        provenance=(
            PromptProvenanceEntry(
                source=EXECUTOR_PROFILE_SOURCE,
                status="present",
                bytes=len(encoded),
                sha=digest,
            ),
        ),
    )
