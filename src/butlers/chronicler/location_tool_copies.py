"""Private native MCP processing reservations and exact result witnesses.

The configured receiving runtime and the guard's private invocation select the
writer. A model tool name, input fingerprint, returned ID or session locator
cannot enroll a receiving principal. These witnesses preserve unresolved
copies; only the full owning runtime disposition can finish their loans.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_retention import PolicyUnavailableError
from butlers.core.tool_call_capture import fingerprint_tool_call_payload


@dataclass
class _ToolCopy:
    runtime: Any
    generation: UUID
    session: UUID
    name: str
    module: str
    active: bool = True
    read_observed: bool = False
    mixed_inputs: bool = False


# These names alone grant nothing: actual native selected-row producers must
# commit their exact source/body/receiving births and mark every selected row.
NATIVE_MEMORY_READ_TOOLS = frozenset(
    {"memory_catalog_search", "memory_search", "memory_recall", "memory_get"}
)


_current_tool_copy: ContextVar[_ToolCopy | None] = ContextVar("native_tool_copy", default=None)


def current_tool_copy(runtime: Any) -> _ToolCopy | None:
    binding = _current_tool_copy.get()
    if binding is None:
        return None
    if not binding.active or binding.runtime is not runtime or not runtime.active:
        raise PolicyUnavailableError("Native tool receiving lifetime differs")
    return binding


def registered_copy_invocation(target: str):
    """Resolve only the guard's still registered exact private lifetime.

    This is a receiving-copy binding, never fact/custody privilege. Target or
    session fields on an object (including a copied genuine cell) do not
    replace current identity membership in the native invocation registry.
    """
    from butlers.core.copy_lifetime import _current_copy_invocation
    from butlers.core.fact_authority import _Invocation, _invocations

    invocation = _current_copy_invocation.get()
    if invocation is None:
        return None
    if (
        not isinstance(invocation, _Invocation)
        or invocation.target != target
        or invocation.deadline <= time.monotonic()
        or not any(cell is invocation for cell in _invocations.values())
    ):
        raise PolicyUnavailableError("Native tool invocation differs")
    return invocation


async def begin_tool_copy(butler: str, module: str, name: str, fingerprint: str):
    from butlers.core.copy_lifetime import _current_copy_invocation
    from butlers.core.delegation_source import _writers

    invocation = _current_copy_invocation.get()
    if invocation is None:
        return None
    runtime = next(
        (w.runtime for w in _writers.values() if w.runtime.name == butler and w.runtime.active),
        None,
    )
    if runtime is None:
        return None
    invocation = registered_copy_invocation(runtime.name)
    session, generation = UUID(invocation.runtime_session), uuid4()
    digest = bytes.fromhex(fingerprint)
    if len(digest) != 32:
        raise PolicyUnavailableError("Native tool full input differs")
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            if not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM sessions WHERE id=$1 AND completed_at IS NULL)",
                session,
            ):
                raise PolicyUnavailableError("Native tool receiving session is unavailable")
            if await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_runtime_context_dispositions d "
                "JOIN location_runtime_context_bindings b USING(input_generation) "
                "WHERE b.receiving_session=$1)",
                session,
            ):
                raise PolicyUnavailableError("Native tool input was disposed")
            await conn.execute(
                "INSERT INTO location_runtime_tool_intents "
                "(tool_generation,receiving_session,tool_name,module_name,input_digest) "
                "VALUES($1,$2,$3,$4,$5)",
                generation,
                session,
                name,
                module,
                digest,
            )
    if (
        await runtime.domain.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_runtime_tool_intents "
            "WHERE tool_generation=$1 AND receiving_session=$2 AND input_digest=$3)",
            generation,
            session,
            digest,
        )
        is not True
    ):
        raise PolicyUnavailableError("Committed native tool input is unknown")
    binding = _ToolCopy(runtime, generation, session, name, module)
    return binding, _current_tool_copy.set(binding)


async def bind_tool_loan(conn: Any, binding: _ToolCopy, loan: UUID, digest: bytes) -> None:
    if current_tool_copy(binding.runtime) is not binding:
        raise PolicyUnavailableError("Native tool loan constructor differs")
    await conn.execute(
        "INSERT INTO location_runtime_tool_inputs(tool_generation,loan_id,body_digest) "
        "VALUES($1,$2,$3)",
        binding.generation,
        loan,
        digest,
    )


async def finish_tool_copy(handle: Any, result: Any = None, *, failed: bool = False) -> None:
    if handle is None:
        return
    binding, token = handle
    try:
        if current_tool_copy(binding.runtime) is not binding:
            raise PolicyUnavailableError("Native tool result constructor differs")
        digest = bytes.fromhex(fingerprint_tool_call_payload(result)) if not failed else None
        outcome = "error" if failed else "success"
        receipt = uuid4()
        async with binding.runtime.domain.acquire() as conn:
            async with conn.transaction():
                await binding.runtime.lock_domain(conn)
                await conn.execute(
                    "INSERT INTO location_runtime_tool_results "
                    "(tool_generation,outcome,result_digest,receipt_id,exclusive_inputs) "
                    "VALUES($1,$2,$3,$4,$5)",
                    binding.generation,
                    outcome,
                    digest,
                    receipt,
                    binding.read_observed and not binding.mixed_inputs,
                )
        if (
            await binding.runtime.domain.fetchval(
                "SELECT receipt_id FROM location_runtime_tool_results WHERE tool_generation=$1",
                binding.generation,
            )
            != receipt
        ):
            raise PolicyUnavailableError("Committed native tool result is unknown")
    finally:
        binding.active = False
        _current_tool_copy.reset(token)


def matched_tool_records(calls: Any, witnesses: list[Any]) -> bool:
    """One-to-one full executed record match; a digest selects no authority."""
    if not isinstance(calls, list) or len(calls) != len(witnesses):
        return False
    unmatched = list(witnesses)
    for call in calls:
        if not isinstance(call, dict) or call.get("outcome") != "success":
            return False
        found = next(
            (
                row
                for row in unmatched
                if row["tool_name"] == call.get("name")
                and row["module_name"] == call.get("module")
                and row["input_digest"].hex() == call.get("input_fingerprint")
                and row["outcome"] == "success"
                and row["result_digest"]
                == bytes.fromhex(fingerprint_tool_call_payload(call.get("result")))
            ),
            None,
        )
        if found is None:
            return False
        unmatched.remove(found)
    return not unmatched
