"""The D8 goal tools, as the broker's goal endpoint serves them.

They are not domain capabilities. The lead holds ``goal.delegate``,
``goal.await``, ``goal.stop_child``, ``goal.complete``, ``goal.wait_approval``,
``goal.wait_owner``, ``goal.wait_until``, ``goal.plan``, ``goal.escalate`` and ``goal.inbox``;
a child holds only ``goal.return``. Each is listed by the calling invocation's
depth, read from its ledger row under the goal lock, and each handler checks
that depth again in its own transaction, so a child can neither list nor call
a lead's tool. MCP names use the catalog's ``<namespace>__<tool>`` spelling
(``goal__delegate``); the ``goal`` namespace is reserved, so no published
capability can shadow a goal tool.

``context.search`` (``context__search``) is served to both depths when a
``ContextSearch`` is configured; its ``context`` namespace is reserved the same way.
``guidance.search`` and ``guidance.load`` (``guidance__search``, ``guidance__load``)
are served to both depths when a ``Guidance`` is configured; ``guidance`` is
reserved too. The packet's ``refs`` name exactly the optional tools served.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.capability_broker.service import Broker

from .context_search import TOOL as CONTEXT_SEARCH
from .context_search import ContextSearch
from .guidance import LOAD_TOOL as GUIDANCE_LOAD
from .guidance import SEARCH_TOOL as GUIDANCE_SEARCH
from .guidance import Guidance
from .ledger import MAX_PLAN_REFS
from .own_conditions import MAX_WAKES
from .packet import PacketComposer
from .runner import GoalRunner

NAMESPACE = "goal"
RESERVED_NAMESPACES = (NAMESPACE, "context", "guidance")
# Served only when configured; an unserved one is refused CAPABILITY.
_OPTIONAL = frozenset({CONTEXT_SEARCH, GUIDANCE_SEARCH, GUIDANCE_LOAD})

_UUID = {"type": "string", "format": "uuid"}


@dataclass(frozen=True, slots=True)
class GoalTool:
    name: str
    depths: tuple[int, ...]
    description: str
    input_schema: dict[str, Any]


_TOOLS = (
    GoalTool(
        "goal__delegate",
        (0,),
        "Start one focused child with a brief, requested capabilities (a subset of your "
        "grant), a model tier and a budget share. Returns the child's id.",
        {
            "type": "object",
            "properties": {
                "brief": {"type": "object"},
                "capabilities": {"type": "array", "items": {"type": "object"}},
                "model_tier": {"type": "string"},
                "budget_share": {"type": "object"},
            },
            "required": ["brief", "capabilities", "model_tier", "budget_share"],
            "additionalProperties": False,
        },
    ),
    GoalTool(
        "goal__await",
        (0,),
        "Read a child's status, end reason (launch_failed: it never ran), typed result and "
        "effect statuses.",
        {
            "type": "object",
            "properties": {"child_id": _UUID},
            "required": ["child_id"],
            "additionalProperties": False,
        },
    ),
    GoalTool(
        "goal__stop_child",
        (0,),
        "Stop one child of this goal.",
        {
            "type": "object",
            "properties": {"child_id": _UUID},
            "required": ["child_id"],
            "additionalProperties": False,
        },
    ),
    GoalTool(
        "goal__complete",
        (0,),
        "Record the goal outcome and the effects it relies on, including the owner reply.",
        {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "enum": ["completed", "partial", "failed"]},
                "relied_effects": {"type": "array", "items": _UUID},
                "reply_effect_id": _UUID,
            },
            "required": ["outcome"],
            "additionalProperties": False,
        },
    ),
    GoalTool(
        "goal__wait_approval",
        (0,),
        "End your turn and wait for the owner's decision on one effect parked for approval. "
        "A successor continues the goal once the approved effect has run; a decline ends it.",
        {
            "type": "object",
            "properties": {"effect_id": _UUID},
            "required": ["effect_id"],
            "additionalProperties": False,
        },
    ),
    GoalTool(
        "goal__wait_owner",
        (0,),
        "End your turn and wait for the owner's answer to a prompt you delivered (a committed "
        "owner message that accepts input and has not expired). The owner's reply to that "
        "prompt starts your successor; no other message wakes the goal.",
        {
            "type": "object",
            "properties": {"effect_id": _UUID},
            "required": ["effect_id"],
            "additionalProperties": False,
        },
    ),
    GoalTool(
        "goal__wait_until",
        (0,),
        "End your turn and wait until a condition over this goal's own effects holds: the "
        "owner's decision on an effect parked for approval ({tag: decision, subject_id, "
        "outcome}) or an effect settling ({tag: effect_settled, effect_id, status_set}), "
        "combined with all/any. The goal ends if the deadline passes first. Returns woken when "
        "the condition already holds.",
        {
            "type": "object",
            "properties": {
                "predicate": {"type": "object"},
                "deadline": {"type": "string", "format": "date-time"},
                "max_wakes": {"type": "integer", "minimum": 1, "maximum": MAX_WAKES},
            },
            "required": ["predicate", "deadline", "max_wakes"],
            "additionalProperties": False,
        },
    ),
    GoalTool(
        "goal__plan",
        (0,),
        "Record your plan as the goal's next version: the premise it rests on, the evidence and "
        "the steps, each a reference to something of this goal (effect:<id>, inbox:<id>, "
        "child:<id> or plan:<version>), never text. The same content keeps the current "
        "version; a changed premise records a new one. A wait keeps the version it opened on.",
        {
            "type": "object",
            "properties": {
                name: {"type": "array", "items": {"type": "string"}, "maxItems": MAX_PLAN_REFS}
                for name in ("premise_refs", "evidence_refs", "step_refs")
            },
            "required": ["premise_refs", "evidence_refs", "step_refs"],
            "additionalProperties": False,
        },
    ),
    GoalTool(
        "goal__inbox",
        (0,),
        "Take the owner's replies to this goal's prompts that arrived since your last call. "
        "Each reply is delivered once; call it between steps while you wait on an answer.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    GoalTool(
        "goal__escalate",
        (0,),
        "Hand this goal to a lead on the next model tier; refused at the top of the ladder.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    GoalTool(
        "goal__return",
        (1,),
        "Return your result in the brief's fixed schema and end.",
        {
            "type": "object",
            "properties": {"result": {"type": "object"}},
            "required": ["result"],
            "additionalProperties": False,
        },
    ),
)

_CONTEXT_SEARCH = GoalTool(
    CONTEXT_SEARCH,
    (0, 1),
    "Search the memory and facts of the domains your grant covers. Returns whole items, each "
    "with its source, reference, authority and a bounded excerpt.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "anchors": {"type": "array", "items": _UUID},
            "as_of": {"type": "string", "format": "date-time"},
            "since": {"type": "string", "format": "date-time"},
            "until": {"type": "string", "format": "date-time"},
            "regions": {"type": "array", "items": {"type": "string"}},
            "kinds": {"type": "array", "items": {"type": "string"}},
            "k": {"type": "integer", "minimum": 1},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)

Handler = Callable[[UUID, str, dict[str, Any]], Awaitable[dict[str, Any]]]


def _closed(arguments: Any, keys: set[str], required: set[str]) -> dict[str, Any]:
    if type(arguments) is not dict or not required <= set(arguments) or not set(arguments) <= keys:
        raise BrokerRefused(Reason.POLICY)
    return arguments


_GUIDANCE_SEARCH = GoalTool(
    GUIDANCE_SEARCH,
    (0, 1),
    "Find guidance documents of the domains your grant covers. Returns references; an "
    "owner-authored, untainted document also carries a bounded excerpt.",
    {
        "type": "object",
        "properties": {"query": {"type": "string"}, "k": {"type": "integer", "minimum": 1}},
        "required": ["query"],
        "additionalProperties": False,
    },
)

_GUIDANCE_LOAD = GoalTool(
    GUIDANCE_LOAD,
    (0, 1),
    "Load one guidance document by reference (<domain>:<name>), as search returns it and as "
    "a brief's guidance_refs name it. Only owner-authored, untainted text is returned.",
    {
        "type": "object",
        "properties": {"ref": {"type": "string"}},
        "required": ["ref"],
        "additionalProperties": False,
    },
)


class GoalTools:
    def __init__(
        self,
        broker: Broker,
        runner: GoalRunner,
        context: ContextSearch | None = None,
        *,
        guidance: Guidance | None = None,
    ):
        self.broker = broker
        self.runner = runner
        self.context = context
        self.guidance = guidance
        self._optional: tuple[GoalTool, ...] = (
            *(() if context is None else (_CONTEXT_SEARCH,)),
            *(() if guidance is None else (_GUIDANCE_SEARCH, _GUIDANCE_LOAD)),
        )
        # The packet's refs point at exactly the context tools this endpoint serves.
        if isinstance(runner.composer, PacketComposer):
            runner.composer = runner.composer.serving(tuple(t.name for t in self._optional))
        self._handlers: dict[str, Handler] = {
            "goal__delegate": self._delegate,
            "goal__await": self._await,
            "goal__stop_child": self._stop_child,
            "goal__complete": self._complete,
            "goal__wait_approval": self._wait_approval,
            "goal__wait_owner": self._wait_owner,
            "goal__wait_until": self._wait_until,
            "goal__plan": self._plan,
            "goal__escalate": self._escalate,
            "goal__inbox": self._inbox,
            "goal__return": self._return,
            CONTEXT_SEARCH: self._context_search,
            GUIDANCE_SEARCH: self._guidance_search,
            GUIDANCE_LOAD: self._guidance_load,
        }

    @staticmethod
    def reserved(name: str) -> bool:
        return name.startswith(tuple(f"{ns}__" for ns in RESERVED_NAMESPACES))

    def listed(self, depth: int) -> tuple[GoalTool, ...]:
        return tuple(tool for tool in (*_TOOLS, *self._optional) if depth in tool.depths)

    async def call(self, goal_id: UUID, token: str, name: str, arguments: Any) -> dict[str, Any]:
        handler = self._handlers.get(name)
        unserved = name in _OPTIONAL and name not in {t.name for t in self._optional}
        if handler is None or unserved:
            raise BrokerRefused(Reason.CAPABILITY)
        return await handler(goal_id, token, arguments)

    async def _delegate(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        child = await self.runner.delegate(goal_id, token, arguments)
        return {"status": "delegated", "child_id": str(child)}

    async def _await(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        args = _closed(arguments, {"child_id"}, {"child_id"})
        return await self.broker.goal_await(goal_id, token, args["child_id"])

    async def _stop_child(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        args = _closed(arguments, {"child_id"}, {"child_id"})
        child = await self.runner.stop_child(goal_id, token, args["child_id"])
        return {"status": "stopped", "child_id": str(child)}

    async def _complete(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        args = _closed(arguments, {"outcome", "relied_effects", "reply_effect_id"}, {"outcome"})
        status = await self.runner.complete(
            goal_id,
            token,
            args["outcome"],
            args.get("relied_effects"),
            args.get("reply_effect_id"),
        )
        return {"status": status}

    async def _wait_approval(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        args = _closed(arguments, {"effect_id"}, {"effect_id"})
        return {"status": await self.runner.wait_approval(goal_id, token, args["effect_id"])}

    async def _wait_owner(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        args = _closed(arguments, {"effect_id"}, {"effect_id"})
        return {"status": await self.runner.wait_owner(goal_id, token, args["effect_id"])}

    async def _wait_until(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        keys = {"predicate", "deadline", "max_wakes"}
        args = _closed(arguments, keys, keys)
        status = await self.runner.wait_until(
            goal_id, token, args["predicate"], args["deadline"], args["max_wakes"]
        )
        return {"status": status}

    async def _plan(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        keys = {"premise_refs", "evidence_refs", "step_refs"}
        args = _closed(arguments, keys, keys)
        version = await self.runner.plan(
            goal_id, token, args["premise_refs"], args["evidence_refs"], args["step_refs"]
        )
        return {"status": "planned", "plan_version": version}

    async def _escalate(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        _closed(arguments, set(), set())
        tier = await self.runner.escalate(goal_id, token)
        return {"status": "escalated", "tier": tier}

    async def _inbox(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        _closed(arguments, set(), set())
        return await self.broker.goal_inbox(goal_id, token)

    async def _return(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        args = _closed(arguments, {"result"}, {"result"})
        await self.runner.goal_return(goal_id, token, args["result"])
        return {"status": "returned"}

    async def _context_search(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        assert self.context is not None
        return await self.context.search(goal_id, token, arguments)

    async def _guidance_search(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        assert self.guidance is not None
        return await self.guidance.search(goal_id, token, arguments)

    async def _guidance_load(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        assert self.guidance is not None
        return await self.guidance.load(goal_id, token, arguments)
