"""Cross-butler delegation tools: delegate_ask, delegate_receive, delegate_answer.

bu-gxmfx (2026-07-04 JARVIS pursuit dossier follow-on). See
``src/butlers/core/delegation_ledger.py`` for the shared writer/reader and
design rationale, and ``alembic/versions/core/core_162_delegation_ledger.py``
for the table.

Registered fleet-wide (non-STAFFER only, mirroring ``notify``/``remind`` in
``_notifications.py``) so any domain butler can both ask a question that
another butler's domain covers, and receive/answer one routed to it.

Routing always goes through the Switchboard's existing ``route()`` primitive
-- via ``daemon.switchboard_client.call_tool("route", ...)`` for every butler
except Switchboard itself, which calls the underlying ``route()`` function
directly in-process (it already owns the pool ``route()`` needs), exactly
mirroring ``notify()``'s client-vs-self-delivery split.
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
    get_delegation,
    mark_dispatch_outcome,
    record_answer,
    record_ask,
    resolve_target_via_catalog,
)
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


def register_delegation_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register delegation-ledger tools: delegate_ask, delegate_receive, delegate_answer.

    Registered for every non-STAFFER butler (same gate as ``notify``) since any
    domain butler may ask a cross-butler question or be the resolved target of
    one.
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

        route_tool_args = {
            "target_butler": target_butler,
            "tool_name": "delegate_receive",
            "args": {
                "ledger_id": ledger_id,
                "question": question,
                "asking_butler": butler_name,
            },
            "source_butler": butler_name,
        }

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

        client = daemon.switchboard_client
        route_error: str | None = None
        retryable = False

        if client is not None:
            try:
                result = await asyncio.wait_for(
                    client.call_tool("route", route_tool_args),
                    timeout=_ROUTE_TIMEOUT_S,
                )
            except TimeoutError:
                route_error = f"Switchboard route() call timed out after {_ROUTE_TIMEOUT_S}s."
                retryable = True
            except (ConnectionError, OSError) as exc:
                route_error = f"Switchboard unreachable: {exc}"
                retryable = True
            except Exception as exc:
                route_error = f"{type(exc).__name__}: {exc}"

            if route_error is None:
                if result.is_error:
                    route_error = _extract_mcp_error_text(result)
                else:
                    data = result.data
                    if isinstance(data, dict) and data.get("error"):
                        route_error = str(data["error"])
        elif butler_name == "switchboard":
            from butlers.tools.switchboard.routing.route import route as _switchboard_route

            try:
                raw = await _switchboard_route(
                    pool,
                    target_butler,
                    "delegate_receive",
                    route_tool_args["args"],
                    source_butler=butler_name,
                )
            except Exception as exc:
                route_error = f"{type(exc).__name__}: {exc}"

            if route_error is None and isinstance(raw, dict) and raw.get("error"):
                route_error = str(raw["error"])
        else:
            route_error = (
                "Switchboard is not connected. Cannot route the delegated question. "
                "This is a transient infrastructure issue — retry after a delay."
            )
            retryable = True

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
            return {
                "status": "error",
                "error": (
                    f"Could not record an answer for ledger_id={ledger_id!r} on butler "
                    f"{butler_name!r}: no matching 'routed' row targeted at this butler "
                    "(already answered, never routed, unroutable/failed, or the id does "
                    "not exist)."
                ),
            }

        return {"status": "ok", "ledger_id": ledger_id}
