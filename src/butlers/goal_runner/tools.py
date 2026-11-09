"""The D8 goal tools, as the broker's goal endpoint serves them.

They are not domain capabilities. The lead holds ``goal.delegate``,
``goal.await``, ``goal.stop_child``, ``goal.complete`` and ``goal.escalate``;
a child holds only ``goal.return``. Each is listed by the calling invocation's
depth, read from its ledger row under the goal lock, and each handler checks
that depth again in its own transaction, so a child can neither list nor call
a lead's tool. MCP names use the catalog's ``<namespace>__<tool>`` spelling
(``goal__delegate``); the ``goal`` namespace is reserved, so no published
capability can shadow a goal tool.

``context.search``, ``guidance.search`` and ``guidance.load`` are not served
yet (their contracts are separate beads).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.capability_broker.service import Broker

from .runner import GoalRunner

NAMESPACE = "goal"

_UUID = {"type": "string", "format": "uuid"}


@dataclass(frozen=True, slots=True)
class GoalTool:
    name: str
    depth: int
    description: str
    input_schema: dict[str, Any]


_TOOLS = (
    GoalTool(
        "goal__delegate",
        0,
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
        0,
        "Read a child's status, typed result and effect statuses.",
        {
            "type": "object",
            "properties": {"child_id": _UUID},
            "required": ["child_id"],
            "additionalProperties": False,
        },
    ),
    GoalTool(
        "goal__stop_child",
        0,
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
        0,
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
        "goal__escalate",
        0,
        "Hand this goal to a lead on the next model tier; refused at the top of the ladder.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    GoalTool(
        "goal__return",
        1,
        "Return your result in the brief's fixed schema and end.",
        {
            "type": "object",
            "properties": {"result": {"type": "object"}},
            "required": ["result"],
            "additionalProperties": False,
        },
    ),
)

Handler = Callable[[UUID, str, dict[str, Any]], Awaitable[dict[str, Any]]]


def _closed(arguments: Any, keys: set[str], required: set[str]) -> dict[str, Any]:
    if type(arguments) is not dict or not required <= set(arguments) or not set(arguments) <= keys:
        raise BrokerRefused(Reason.POLICY)
    return arguments


class GoalTools:
    def __init__(self, broker: Broker, runner: GoalRunner):
        self.broker = broker
        self.runner = runner
        self._handlers: dict[str, Handler] = {
            "goal__delegate": self._delegate,
            "goal__await": self._await,
            "goal__stop_child": self._stop_child,
            "goal__complete": self._complete,
            "goal__escalate": self._escalate,
            "goal__return": self._return,
        }

    @staticmethod
    def reserved(name: str) -> bool:
        return name.startswith(NAMESPACE + "__")

    @staticmethod
    def listed(depth: int) -> tuple[GoalTool, ...]:
        return tuple(tool for tool in _TOOLS if tool.depth == depth)

    async def call(self, goal_id: UUID, token: str, name: str, arguments: Any) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
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

    async def _escalate(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        _closed(arguments, set(), set())
        tier = await self.runner.escalate(goal_id, token)
        return {"status": "escalated", "tier": tier}

    async def _return(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        args = _closed(arguments, {"result"}, {"result"})
        await self.runner.goal_return(goal_id, token, args["result"])
        return {"status": "returned"}
