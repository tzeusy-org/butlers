"""Cross-butler delegation tools: delegate_ask/receive/answer/wake.

bu-gxmfx (2026-07-04 JARVIS pursuit dossier follow-on). See
``src/butlers/core/delegation_ledger.py`` for the shared writer/reader and
design rationale, and ``alembic/versions/core/core_162_delegation_ledger.py``
for the table.

``delegate_wake`` (bu-27dxl.5.2) implements the durable delegated-answer wake
path defined by the merged ``activate-delegation-wake-loop`` OpenSpec change:
on first valid answer, ``delegate_answer`` commits an immutable wake identity
and attempts a Switchboard-routed callback so the original asker can create
its own bounded return task. See ``src/butlers/core/delegation_wake.py`` for
the asker-local task reconciliation ``delegate_wake`` delegates to.

Registered fleet-wide (non-STAFFER only, mirroring ``notify``/``remind`` in
``_notifications.py``) so any domain butler can ask a question that another
butler's domain covers, receive/answer one routed to it, and receive the
wake callback for one it asked. Per the merged core-daemon spec ("Delegation
Core Tool Inventory"), all four tools share the one reserved ``delegation``
core group -- ``delegate_wake``'s security boundary is NOT the group gate (the
framework has no LLM-hidden-but-registered tier) but the ledger
re-verification Switchboard and ``delegate_wake`` itself perform before any
local write (see ``delegation_ledger.verify_wake_callback`` and
``delegation_wake.handle_delegate_wake``).

Routing always goes through the Switchboard's existing ``route()`` primitive
-- via ``daemon.switchboard_client.call_tool("route", ...)`` for every butler
except Switchboard itself, which calls the underlying ``route()`` function
directly in-process (it already owns the pool ``route()`` needs), exactly
mirroring ``notify()``'s client-vs-self-delivery split. ``_dispatch_via_switchboard``
below is the shared helper for that split, used by both ``delegate_ask``'s
dispatch to a target and ``delegate_answer``'s wake callback to an asker.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from pydantic import Field

from butlers.config import ButlerType
from butlers.core.delegation_ledger import (
    classify_unaccepted_answer,
    get_delegation,
    mark_dispatch_outcome,
    mark_wake_callback_failed,
    record_answer,
    record_ask,
    record_wake_attempt,
    resolve_target_via_catalog,
)
from butlers.core.delegation_wake import handle_delegate_wake
from butlers.core.scheduler import schedule_create as _schedule_create
from butlers.core.telemetry import tool_span
from butlers.core_tools._base import ToolContext

logger = logging.getLogger(__name__)

_ROUTE_TIMEOUT_S = 30


def _extract_mcp_error_text(result: Any) -> str:
    """Best-effort extraction of MCP error text from a CallToolResult."""
    content = getattr(result, "content", None) or []
    if content:
        first = content[0]
        return str(getattr(first, "text", "") or first)
    return "route tool returned an error"


async def _dispatch_via_switchboard(
    daemon: Any,
    pool: Any,
    butler_name: str,
    *,
    target_butler: str,
    tool_name: str,
    args: dict[str, Any],
) -> tuple[str | None, bool]:
    """Dispatch one tool call through the Switchboard's ``route()`` primitive.

    Mirrors ``notify()``'s client-vs-self-delivery split: every butler except
    Switchboard itself calls ``daemon.switchboard_client.call_tool("route", ...)``;
    Switchboard calls the underlying ``route()`` function directly in-process
    (it already owns the pool ``route()`` needs). Shared by ``delegate_ask``'s
    dispatch to a target and ``delegate_answer``'s wake callback to an asker.

    Returns ``(error_text, retryable)`` -- ``error_text`` is ``None`` on a
    successful dispatch (a route()-level success; it says nothing about what
    the target tool itself returned as its logical result).
    """
    route_tool_args = {
        "target_butler": target_butler,
        "tool_name": tool_name,
        "args": args,
        "source_butler": butler_name,
    }

    client = daemon.switchboard_client
    if client is not None:
        try:
            result = await asyncio.wait_for(
                client.call_tool("route", route_tool_args),
                timeout=_ROUTE_TIMEOUT_S,
            )
        except TimeoutError:
            return f"Switchboard route() call timed out after {_ROUTE_TIMEOUT_S}s.", True
        except (ConnectionError, OSError) as exc:
            return f"Switchboard unreachable: {exc}", True
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}", False

        if result.is_error:
            return _extract_mcp_error_text(result), False
        data = result.data
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"]), False
        return None, False

    if butler_name == "switchboard":
        from butlers.tools.switchboard.routing.route import route as _switchboard_route

        try:
            raw = await _switchboard_route(
                pool,
                target_butler,
                tool_name,
                args,
                source_butler=butler_name,
            )
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}", False

        if isinstance(raw, dict) and raw.get("error"):
            return str(raw["error"]), False
        return None, False

    return (
        "Switchboard is not connected. Cannot route the delegated call. "
        "This is a transient infrastructure issue — retry after a delay."
    ), True


def register_delegation_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register delegation-ledger tools: delegate_ask/receive/answer/wake.

    Registered for every non-STAFFER butler (same gate as ``notify``) since any
    domain butler may ask a cross-butler question, be the resolved target of
    one, or receive the wake callback for one it asked.
    """
    if ctx.butler_type == ButlerType.STAFFER:
        return

    daemon = ctx.daemon
    pool = ctx.pool
    butler_name = ctx.butler_name

    @_core_tool("delegation")
    @tool_span("delegate_ask", butler_name=butler_name)
    async def delegate_ask(
        question: Annotated[
            str,
            Field(
                description=(
                    "The question to delegate to whichever butler's domain covers it. "
                    "Self-contained — the target butler has no access to your session "
                    "context, only this text."
                )
            ),
        ],
    ) -> dict:
        """Ask a cross-butler question, routed to whichever butler's domain covers it.

        Resolution reuses ``public.memory_catalog`` (the same shared discovery
        index behind Fleet Knowledge search): the top hybrid-search hit for
        ``question`` names the owning butler. Routing then goes through the
        Switchboard's ``route()`` — never a parallel dispatch path.

        Every outcome is durably recorded in ``public.delegation_ledger`` and
        returned via ``ledger_id``, including when nothing could be routed:

        - ``{"status": "unroutable", ...}`` — no catalog domain match, or the
          resolved target is yourself (delegating to yourself is never useful
          — query your own domain directly instead).
        - ``{"status": "failed", ...}`` — a domain match was found but
          dispatch via the Switchboard failed (target unreachable, stale,
          etc). ``retryable`` is set when the failure looks transient.
        - ``{"status": "routed", "ledger_id": ..., "target_butler": ...}`` —
          the target acknowledged and scheduled itself to answer. Check back
          later (dashboard delegation ledger, or ask the target directly) for
          the answer under this ``ledger_id``.
        """
        if not question or not question.strip():
            return {"status": "error", "error": "question must not be empty."}

        target_butler, catalog_match_id, catalog_score = await resolve_target_via_catalog(
            pool, question
        )

        if target_butler is None:
            ledger_id = await record_ask(
                pool,
                asking_butler=butler_name,
                question=question,
                status="unroutable",
                reason="no_catalog_match",
            )
            return {
                "status": "unroutable",
                "ledger_id": ledger_id,
                "reason": "no_catalog_match",
            }

        if target_butler == butler_name:
            ledger_id = await record_ask(
                pool,
                asking_butler=butler_name,
                question=question,
                status="unroutable",
                target_butler=target_butler,
                catalog_match_id=catalog_match_id,
                catalog_score=catalog_score,
                reason="self_target",
            )
            return {
                "status": "unroutable",
                "ledger_id": ledger_id,
                "target_butler": target_butler,
                "reason": "self_target",
            }

        ledger_id = await record_ask(
            pool,
            asking_butler=butler_name,
            question=question,
            status="pending",
            target_butler=target_butler,
            catalog_match_id=catalog_match_id,
            catalog_score=catalog_score,
        )

        async def _fail(error_text: str, *, retryable: bool = False) -> dict:
            try:
                await mark_dispatch_outcome(pool, ledger_id, status="failed", reason=error_text)
            except Exception:
                logger.warning(
                    "delegate_ask: failed to record 'failed' outcome for ledger_id=%s",
                    ledger_id,
                    exc_info=True,
                )
            result = {
                "status": "failed",
                "ledger_id": ledger_id,
                "target_butler": target_butler,
                "error": error_text,
            }
            if retryable:
                result["retryable"] = True
            return result

        route_error, retryable = await _dispatch_via_switchboard(
            daemon,
            pool,
            butler_name,
            target_butler=target_butler,
            tool_name="delegate_receive",
            args={
                "ledger_id": ledger_id,
                "question": question,
                "asking_butler": butler_name,
            },
        )
        if route_error is not None:
            return await _fail(route_error, retryable=retryable)

        try:
            await mark_dispatch_outcome(pool, ledger_id, status="routed")
        except Exception as exc:
            # Dispatch itself succeeded (the target already scheduled its
            # answer) but we could not persist that outcome. Surface this as
            # its own honest state rather than claiming a confirmed "routed"
            # we cannot back up, or silently swallowing the inconsistency.
            logger.warning(
                "delegate_ask: dispatch to %s succeeded but failed to mark "
                "ledger_id=%s as 'routed'",
                target_butler,
                ledger_id,
                exc_info=True,
            )
            return {
                "status": "error",
                "ledger_id": ledger_id,
                "target_butler": target_butler,
                "error": (
                    f"Question was routed to {target_butler!r} but recording the ledger "
                    f"outcome failed: {exc}"
                ),
            }

        return {"status": "routed", "ledger_id": ledger_id, "target_butler": target_butler}

    @_core_tool("delegation")
    @tool_span("delegate_receive", butler_name=butler_name)
    async def delegate_receive(
        ledger_id: Annotated[str, Field(description="delegation_ledger row id from delegate_ask.")],
        question: Annotated[str, Field(description="The delegated question text.")],
        asking_butler: Annotated[str, Field(description="Name of the butler that asked.")],
    ) -> dict:
        """Receive a delegated question routed to this butler via the Switchboard.

        Schedules a one-shot task (fires within ~1 minute) that instructs this
        butler's next spawned session to answer the question and call
        ``delegate_answer`` with the same ``ledger_id``. Does not answer
        synchronously — dispatch and answering are decoupled, like
        ``remind()``/``notify()``, so this call (and the Switchboard ``route()``
        call ahead of it) returns quickly instead of blocking on an LLM run.
        """
        if not question or not question.strip():
            return {"status": "error", "error": "question must not be empty."}

        try:
            row = await get_delegation(pool, ledger_id)
        except Exception as exc:
            return {"status": "error", "error": f"Failed to look up ledger row: {exc}"}

        if row is None:
            return {
                "status": "error",
                "error": f"No delegation_ledger row for id={ledger_id!r}.",
            }
        if row["target_butler"] != butler_name:
            return {
                "status": "error",
                "error": (
                    f"delegate_receive called on butler {butler_name!r} but ledger row "
                    f"{ledger_id!r} targets {row['target_butler']!r}."
                ),
            }
        if row["status"] != "pending":
            # Already scheduled (e.g. a Switchboard route() reconnect-retry
            # re-delivered the same call) or already answered — never
            # double-schedule an answering task for the same ledger row.
            return {"status": f"already_{row['status']}", "ledger_id": ledger_id}

        now = datetime.now(UTC)
        target_time = now + timedelta(minutes=1)
        cron = f"{target_time.minute} {target_time.hour} {target_time.day} {target_time.month} *"
        until_at = target_time + timedelta(minutes=1)
        prompt = (
            f"Butler '{asking_butler}' delegated a question to you via the cross-butler "
            f"delegation ledger (ledger_id={ledger_id}):\n\n{question}\n\n"
            "Answer it using your own domain's knowledge/memory, then call the "
            f'delegate_answer tool with ledger_id="{ledger_id}" and your answer text.'
        )
        try:
            task_id = await _schedule_create(
                pool,
                f"delegate-answer-{ledger_id}",
                cron,
                prompt,
                until_at=until_at,
            )
        except Exception as exc:
            return {
                "status": "error",
                "error": f"Failed to schedule the answering task: {exc}",
            }

        return {"status": "scheduled", "ledger_id": ledger_id, "task_id": str(task_id)}

    @_core_tool("delegation")
    @tool_span("delegate_answer", butler_name=butler_name)
    async def delegate_answer(
        ledger_id: Annotated[str, Field(description="delegation_ledger row id to answer.")],
        answer: Annotated[str, Field(description="The answer text for the delegated question.")],
    ) -> dict:
        """Post the answer to a delegated question this butler was asked.

        Only succeeds for a ``'routed'`` row whose ``target_butler`` matches
        this butler — never records an answer against a question asked of a
        different butler.

        On the first valid answer, durably commits an immutable wake identity
        and attempts a Switchboard-routed callback (``delegate_wake``) so the
        original asking butler can create its own bounded return task. The
        answer's durability never depends on that callback: a callback
        failure is reported as an honest ``wake_state="callback_failed"``
        partial success, never fabricated as complete. A resubmission of the
        exact same answer replays the same wake identity (safe to retry); a
        resubmission with different text is an integrity conflict and
        schedules nothing.
        """
        if not answer or not answer.strip():
            return {"status": "error", "error": "answer must not be empty."}

        try:
            updated = await record_answer(
                pool, ledger_id, answering_butler=butler_name, answer=answer
            )
        except Exception as exc:
            return {"status": "error", "error": f"Failed to record answer: {exc}"}

        if updated is None:
            classification = await classify_unaccepted_answer(
                pool, ledger_id, answering_butler=butler_name, answer=answer
            )
            if classification.outcome == "duplicate":
                # Same text resubmitted — a legitimate replay of the existing
                # wake identity (D2), not a new answer. Fall through to the
                # callback-attempt path below using the row already on file.
                updated = classification.row
            elif classification.outcome == "legacy":
                return {
                    "status": "ok",
                    "ledger_id": ledger_id,
                    "answer_recorded": True,
                    "wake_state": "not_applicable",
                    "note": (
                        "This row predates the delegated-answer wake protocol; no callback "
                        "was attempted."
                    ),
                }
            elif classification.outcome == "changed":
                return {
                    "status": "error",
                    "error": (
                        f"ledger_id={ledger_id!r} was already answered with different text. "
                        "The original answer is immutable — resubmitting a changed answer is "
                        "an integrity conflict, not a retry."
                    ),
                }
            else:
                return {
                    "status": "error",
                    "error": (
                        f"Could not record an answer for ledger_id={ledger_id!r} on butler "
                        f"{butler_name!r}: no matching 'routed' row targeted at this butler "
                        "(already answered, never routed, unroutable/failed, or the id does "
                        "not exist)."
                    ),
                }

        wake_key = updated.get("wake_key")
        asking_butler = updated.get("asking_butler")
        if not wake_key or not asking_butler:
            # Unreachable given record_answer's atomic write, but never
            # fabricate a callback attempt against an incomplete row.
            return {"status": "ok", "ledger_id": ledger_id, "answer_recorded": True}

        route_error, retryable = await _dispatch_via_switchboard(
            daemon,
            pool,
            butler_name,
            target_butler=asking_butler,
            tool_name="delegate_wake",
            args={"ledger_id": ledger_id, "wake_key": wake_key},
        )

        try:
            await record_wake_attempt(
                pool,
                ledger_id,
                stage="callback_dispatch",
                result="failed" if route_error is not None else "routed",
                actor_butler=butler_name,
                retryable=retryable if route_error is not None else None,
                error_message=route_error,
            )
        except Exception:
            logger.warning(
                "delegate_answer: failed to record wake-attempt evidence for ledger_id=%s",
                ledger_id,
                exc_info=True,
            )

        if route_error is not None:
            try:
                await mark_wake_callback_failed(pool, ledger_id, wake_key)
            except Exception:
                logger.warning(
                    "delegate_answer: failed to mark wake_state=callback_failed for ledger_id=%s",
                    ledger_id,
                    exc_info=True,
                )
            return {
                "status": "ok",
                "ledger_id": ledger_id,
                "answer_recorded": True,
                "wake_state": "callback_failed",
                "callback_retryable": retryable,
                "callback_error": route_error,
            }

        return {"status": "ok", "ledger_id": ledger_id, "answer_recorded": True}

    @_core_tool("delegation")
    @tool_span("delegate_wake", butler_name=butler_name)
    async def delegate_wake(
        ledger_id: Annotated[
            str, Field(description="delegation_ledger row id from the delegated-answer callback.")
        ],
        wake_key: Annotated[
            str,
            Field(description="Immutable wake key from the delegation ledger; must match exactly."),
        ],
    ) -> dict:
        """Server-to-server delegated-answer wake callback.

        Reachable only through the Switchboard's ``route()`` callback path for
        a row this butler asked and that has already been answered —
        Switchboard independently re-verifies the ledger row before ever
        dispatching this call, and this tool repeats every check itself
        rather than trusting that upstream gate. Never call this directly.

        Independently re-reads the ledger row (never trusts ``ledger_id`` or
        ``wake_key`` as anything but a lookup/replay key — the question and
        answer text are treated as untrusted reference data) before creating
        or reconciling its own bounded one-shot return task
        (``delegate-return-<ledger_id>``) in this butler's own schema. Never
        writes to a sibling schema. Duplicate delivery, reconnect, and replay
        all converge on the same single logical task.
        """
        return await handle_delegate_wake(
            pool, ledger_id=ledger_id, wake_key=wake_key, asking_butler=butler_name
        )
