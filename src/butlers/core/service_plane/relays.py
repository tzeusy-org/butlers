"""Named service-plane relays: fixed schema, one target, a caller allowlist (RFC 0038 D7).

A relay replaces one use of the generic ``route`` proxy. It forwards to exactly
one (butler, tool) target, accepts only its own argument schema, and admits
only the callers its allowlist names. Every check runs before any target is
contacted, and a refusal never falls back to the generic proxy.

The caller is the authenticated workload that sent the request, plus the
daemon name it presents inside that workload. The daemon name narrows the
allowlist; it is never authority on its own, and a request body field such as
``source_butler`` is never read as the caller.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_BY_NAME


@dataclass(frozen=True, slots=True)
class ServiceCaller:
    """An authenticated calling workload and the daemon it speaks for."""

    workload: str
    daemon: str


class RelayRefused(Exception):
    """A relay refused the call before contacting its target.

    ``status`` is the HTTP status the relay endpoint answers with: ``403`` for
    the relay's own judgement (caller, target, arguments), ``404`` when nothing
    serves the relay, ``400`` for a malformed body.
    """

    def __init__(self, code: str, *, status: int = 403) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


Dispatch = Callable[[str, str, dict[str, Any]], Awaitable[Any]]
#: Builds the (target butler, target args) pair from the authenticated caller
#: and the validated request, or raises ``RelayRefused``.
Prepare = Callable[[ServiceCaller, BaseModel], tuple[str, dict[str, Any]]]


def _single_target(endpoint: RelayEndpoint) -> Prepare:
    def prepare(caller: ServiceCaller, validated: BaseModel) -> tuple[str, dict[str, Any]]:
        (butler,) = endpoint.target_butlers
        return butler, validated.model_dump(mode="json")

    return prepare


@dataclass(frozen=True, slots=True)
class RelayEndpoint:
    """One named relay, its one target tool and the butlers that tool may live on.

    Most relays have exactly one target butler. A relay with several (domain
    events: the contract's permitted subscribers) must supply ``prepare``,
    which picks one of them from the request; the pick is checked against
    ``target_butlers`` again before dispatch.
    """

    name: str
    target_butlers: frozenset[str]
    target_tool: str
    args_model: type[BaseModel]
    allowed_callers: frozenset[ServiceCaller]
    prepare: Prepare | None = None
    #: Deliver to a target whose registry heartbeat is stale (the QA relay's
    #: existing behaviour: error reports must still reach a lagging QA).
    allow_stale: bool = False


def target_is_llm_presentable(tool: str) -> bool:
    """True unless the presentation inventory declares the tool not presentable.

    An undeclared tool counts as presentable, so a relay target must be
    declared ``presentable=False`` before any relay can reach it.
    """
    presentation = TOOL_PRESENTATION_BY_NAME.get(tool)
    return presentation is None or presentation.llm_presentable


async def relay_call(
    endpoint: RelayEndpoint,
    caller: ServiceCaller | None,
    args: Mapping[str, Any],
    dispatch: Dispatch,
) -> Any:
    """Check the caller, the target and the arguments, then forward once."""
    if caller is None:
        raise RelayRefused("caller_unauthenticated")
    if caller not in endpoint.allowed_callers:
        raise RelayRefused("caller_not_allowed")
    if target_is_llm_presentable(endpoint.target_tool):
        raise RelayRefused("target_llm_presentable")
    try:
        validated = endpoint.args_model.model_validate(dict(args))
    except ValidationError:
        raise RelayRefused("args_invalid") from None
    if endpoint.prepare is None and len(endpoint.target_butlers) != 1:
        raise RelayRefused("target_not_allowed")
    prepare = endpoint.prepare or _single_target(endpoint)
    target_butler, target_args = prepare(caller, validated)
    if target_butler not in endpoint.target_butlers:
        raise RelayRefused("target_not_allowed")
    return await dispatch(target_butler, endpoint.target_tool, target_args)
