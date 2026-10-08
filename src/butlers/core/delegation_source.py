"""Constructor-owned native delegation writer dispatch without runtime imports.

This private adapter is not a tool, source selector or actor field. Ordinary
unconfigured callers keep their existing Pool/Connection writer contract.
"""

from __future__ import annotations

from typing import Any

_writers: dict[Any, Any] = {}


def register_writer(pool: Any, writer: Any) -> None:
    prior = _writers.get(pool)
    if prior is not None and prior is not writer:
        raise RuntimeError("Native delegation writer is already registered")
    _writers[pool] = writer


def clear_writer(pool: Any, writer: Any) -> None:
    if _writers.get(pool) is writer:
        del _writers[pool]


async def capture_ask(pool: Any, fields: dict, write: Any) -> str | None:
    selected = [writer for writer in _writers.values() if writer.capture_active()]
    if not selected:
        if pool in _writers:
            raise RuntimeError("Native delegation source producer is unavailable")
        return None
    if len(selected) != 1 or _writers.get(pool) is not selected[0]:
        # An active native producer cannot switch to a caller's Connection or
        # unrelated Pool and quietly fall back to the ordinary writer path.
        raise RuntimeError("Native delegation owning writer differs")
    return await selected[0].capture_ask(fields, write)


async def receive_question(pool: Any, canonical: dict) -> Any:
    writer = _writers.get(pool)
    if writer is None:
        return None  # Explicit ordinary unconfigured contract, not source classification.
    from butlers.chronicler.location_delegation_receivers import reserve_received_question

    return await reserve_received_question(writer, canonical)


async def create_question_schedule(pool: Any, admission: Any, prompt: str, write: Any) -> Any:
    if admission is None:
        return await write(pool)
    writer = _writers.get(pool)
    if writer is None or getattr(admission, "writer", None) is not writer:
        raise RuntimeError("Native question schedule owning writer differs")
    from butlers.chronicler.location_delegation_receivers import schedule_received_question

    return await schedule_received_question(admission, prompt, write)


async def dispatch_scheduled_question(
    pool: Any, task: Any, prompt: str, dispatch: Any, kwargs: dict
):
    """Actual scheduler boundary; public task/trigger fields cannot mint a binding."""
    from butlers.chronicler.location_delegation_processing import scheduled_question_scope

    async with scheduled_question_scope(pool, task, prompt):
        return await dispatch(**kwargs)


async def capture_answer(pool: Any, ledger: Any, answering: str, answer: str, write: Any):
    selected = [writer for writer in _writers.values() if writer.capture_active()]
    if not selected:
        if pool in _writers:
            raise RuntimeError("Native answer source producer is unavailable")
        return await write(pool)  # Explicit ordinary unconfigured writer contract.
    if len(selected) != 1 or _writers.get(pool) is not selected[0]:
        raise RuntimeError("Native answer owning writer differs")
    from butlers.chronicler.location_delegation_answers import capture_answer as capture

    return await capture(selected[0], ledger, answering, answer, write)
