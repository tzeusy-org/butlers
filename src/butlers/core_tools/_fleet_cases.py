"""Fleet Case File contribution tools (bu-8cdl1.7 Slices 3-4, RFC 0032).

See ``src/butlers/core/fleet_cases.py`` for the shared data layer and
``about/legends-and-lore/rfcs/0032-fleet-case-file.md`` for the full design.
Slice 1 (schema/RLS, core_217) and Slice 2 (read API,
``roster/switchboard/api/router.py``) already landed; this adds the six MCP
tools the RFC's Slice 3 names: ``find_open_case``, ``open_case``,
``contribute_case_evidence``, ``propose_case_posture``, ``close_case``,
``read_case``. Slice 4 (situation-scoped attention) adds no new tool -- it
folds a ``case_attention`` field into ``contribute_case_evidence`` and
``propose_case_posture``'s existing results (see
``fleet_cases.evaluate_case_attention``).

Registered fleet-wide, every butler type included (unlike ``domain_events``/
``delegation``, which exclude STAFFER): Switchboard itself is a STAFFER and is
the one role that can actually write ``fleet_cases``/``fleet_case_links``
(RLS, ``core_217_fleet_case_file.py``), so it needs these tools registered on
its own daemon too, not just as a routing target.

``open_case``/``propose_case_posture``/``close_case`` mutate ``fleet_cases``,
which RLS restricts to ``butler_switchboard_rw``. On Switchboard's own daemon
the tool writes directly through the caller's own pool (already the
switchboard role); on every other butler's daemon it is forwarded through the
Switchboard's ``route()`` primitive to Switchboard's own registration of the
same tool -- mirroring ``_delegation.py``/``_domain_events.py``'s existing
client-vs-self-delivery split via the shared
``_switchboard_route_dispatch.dispatch_via_switchboard_route`` -- so the
actual write always lands under Switchboard's own role regardless of which
butler initiated it. ``propose_case_posture``'s "Switchboard arbitrates" is,
in this slice, a plain last-write-wins update once forwarded; no quorum/
voting model ships here.

``find_open_case``, ``contribute_case_evidence``, and ``read_case`` need no
forwarding: ``fleet_case_evidence`` has no RLS restriction (any role may
INSERT) and both tables are readable by every role, so these run directly
against the calling butler's own pool.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from pydantic import Field

from butlers.core import fleet_cases
from butlers.core.telemetry import tool_span
from butlers.core_tools._base import ToolContext
from butlers.core_tools._switchboard_route_dispatch import dispatch_via_switchboard_route


def _unwrap_fleet_case_route_result(raw: Any) -> tuple[dict[str, Any] | None, str | None, bool]:
    """Unwrap a ``route()`` return value into ``(data, error_text, retryable)``.

    Mirrors ``_domain_events._unwrap_route_result``'s envelope handling: every
    fleet-case tool below returns ``{"status": "ok"/"error", ...}``, so a
    successful route() dispatch whose target tool itself reports
    ``status: "error"`` is still an error from this caller's point of view.
    """
    if not isinstance(raw, dict):
        return None, "route() returned a non-dict result.", False
    if "error" in raw:
        error_text = str(raw["error"])
        retryable = raw.get("retryable")
        return None, error_text, retryable if isinstance(retryable, bool) else False
    data = raw.get("result")
    if isinstance(data, dict) and data.get("status") == "error":
        return None, str(data.get("error") or "Fleet case tool returned an error."), False
    return (data if isinstance(data, dict) else None), None, False


async def _dispatch_fleet_case_write(
    daemon: Any, pool: Any, butler_name: str, *, tool_name: str, args: dict[str, Any]
) -> dict[str, Any]:
    """Forward one ``fleet_cases``-mutating tool call to Switchboard."""
    data, error_text, retryable = await dispatch_via_switchboard_route(
        daemon.switchboard_client,
        pool,
        butler_name,
        target_butler="switchboard",
        tool_name=tool_name,
        args=args,
        classify=_unwrap_fleet_case_route_result,
        route_purpose="fleet case write",
    )
    if error_text is not None:
        result: dict[str, Any] = {"status": "error", "error": error_text}
        if retryable:
            result["retryable"] = True
        return result
    return data or {
        "status": "error",
        "error": "Switchboard returned no data for this fleet case write.",
    }


def register_fleet_case_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register the fleet case file contribution tools."""
    daemon = ctx.daemon
    pool = ctx.pool
    butler_name = ctx.butler_name

    @_core_tool("fleet_cases")
    @tool_span("find_open_case", butler_name=butler_name)
    async def find_open_case(
        correlation_key: Annotated[
            str,
            Field(description=("Readable situation key, e.g. 'health:owner:respiratory-illness'.")),
        ],
    ) -> dict:
        """Look up the non-closed case for a correlation key, if one exists.

        Call this before ``open_case`` for a situation you suspect is
        already tracked: at most one non-closed case can exist per
        correlation_key (the database enforces it either way), but checking
        first lets you branch straight to ``contribute_case_evidence``
        instead of hitting that conflict.

        Returns ``{"status": "ok", "case": {...} | null}`` -- ``null`` is a
        normal, expected result meaning no open case exists yet.
        """
        case = await fleet_cases.find_open_case(pool, correlation_key)
        return {"status": "ok", "case": case}

    @_core_tool("fleet_cases")
    @tool_span("open_case", butler_name=butler_name)
    async def open_case(
        correlation_key: Annotated[
            str,
            Field(
                description=(
                    "Readable key identifying this situation, e.g. "
                    "'health:owner:respiratory-illness'. Not a UUID -- pick something an "
                    "operator would recognize."
                )
            ),
        ],
    ) -> dict:
        """Open a new fleet case for a situation, at posture='silent'.

        Refused if a non-closed case already exists for this
        correlation_key -- call ``find_open_case`` first and contribute
        evidence to the existing case instead of opening a duplicate.

        Returns ``{"status": "ok", "case": {...}}``.
        """
        if ctx.is_switchboard:
            try:
                case = await fleet_cases.open_case(pool, correlation_key=correlation_key)
            except fleet_cases.FleetCaseError as exc:
                return {"status": "error", "error": str(exc)}
            return {"status": "ok", "case": case}
        return await _dispatch_fleet_case_write(
            daemon,
            pool,
            butler_name,
            tool_name="open_case",
            args={"correlation_key": correlation_key},
        )

    @_core_tool("fleet_cases")
    @tool_span("contribute_case_evidence", butler_name=butler_name)
    async def contribute_case_evidence(
        case_id: Annotated[str, Field(description="fleet_cases.id from find_open_case/open_case.")],
        kind: Annotated[
            str,
            Field(
                description=(
                    "What kind of evidence this is, e.g. 'candidate', 'session', "
                    "'observation'. Open vocabulary, your own words."
                )
            ),
        ],
        ref: Annotated[
            str,
            Field(
                description=(
                    "A reference to what you observed -- an insight candidate id, a "
                    "session id, a task name. Something a reader can open."
                )
            ),
        ],
        payload: Annotated[
            dict[str, Any] | None,
            Field(description="Optional structured detail about this observation."),
        ] = None,
    ) -> dict:
        """Attach one piece of evidence to a case, attributed to you.

        Idempotent: reporting the same ``(kind, ref)`` again is a no-op, not
        a duplicate row -- safe to call more than once for the same
        observation. Any butler may contribute; this never needs
        Switchboard.

        Returns ``{"status": "ok", "evidence": {...}, "newly_recorded": bool,
        "case_attention": {...} | None}``. ``case_attention`` (Slice 4,
        situation-scoped attention) is only evaluated for a newly-recorded
        contribution -- a repeat report is a no-op and cannot itself trigger a
        quiet-hours bypass -- and is ``None`` when the case no longer exists
        by the time attention is evaluated. See
        ``fleet_cases.evaluate_case_attention`` for what its fields mean.
        """
        try:
            evidence, newly_recorded = await fleet_cases.contribute_evidence(
                pool,
                case_id=case_id,
                contributor=butler_name,
                kind=kind,
                ref=ref,
                payload=payload,
            )
        except fleet_cases.FleetCaseError as exc:
            return {"status": "error", "error": str(exc)}

        case_attention = None
        if newly_recorded:
            case_summary = await fleet_cases.get_case_summary(pool, case_id)
            if case_summary is not None:
                case_attention = await fleet_cases.evaluate_case_attention(
                    pool,
                    case_id=case_id,
                    correlation_key=case_summary["correlation_key"],
                    posture=case_summary["posture"],
                    state=case_summary["state"],
                    origin_butler=butler_name,
                )
        return {
            "status": "ok",
            "evidence": evidence,
            "newly_recorded": newly_recorded,
            "case_attention": case_attention,
        }

    @_core_tool("fleet_cases")
    @tool_span("propose_case_posture", butler_name=butler_name)
    async def propose_case_posture(
        case_id: Annotated[str, Field(description="fleet_cases.id to propose a posture for.")],
        posture: Annotated[str, Field(description="One of: silent, routine, active, urgent.")],
    ) -> dict:
        """Propose a posture for a case.

        Switchboard is the sole arbiter of a case's actual posture (RLS
        restricts the write to it); a proposal from any other butler is
        forwarded through Switchboard's ``route()`` and takes effect as a
        plain last-write-wins update -- there is no quorum/voting model in
        this slice. Refused once the case is closed.

        Returns ``{"status": "ok", "case": {...}, "case_attention": {...}}``.
        ``case_attention`` (Slice 4, situation-scoped attention) reports
        whether *this* proposal broke quiet hours for the case -- see
        ``fleet_cases.evaluate_case_attention`` for what its fields mean.
        """
        if ctx.is_switchboard:
            try:
                case = await fleet_cases.propose_posture(pool, case_id=case_id, posture=posture)
            except fleet_cases.FleetCaseError as exc:
                return {"status": "error", "error": str(exc)}
            case_attention = await fleet_cases.evaluate_case_attention(
                pool,
                case_id=case_id,
                correlation_key=case["correlation_key"],
                posture=case["posture"],
                state=case["state"],
                origin_butler=butler_name,
            )
            return {"status": "ok", "case": case, "case_attention": case_attention}
        return await _dispatch_fleet_case_write(
            daemon,
            pool,
            butler_name,
            tool_name="propose_case_posture",
            args={"case_id": case_id, "posture": posture},
        )

    @_core_tool("fleet_cases")
    @tool_span("close_case", butler_name=butler_name)
    async def close_case(
        case_id: Annotated[str, Field(description="fleet_cases.id to close.")],
        outcome: Annotated[
            str,
            Field(
                description=(
                    "Required -- what happened. A case cannot close without a non-empty outcome."
                )
            ),
        ],
    ) -> dict:
        """Close a case with its outcome.

        Only Switchboard may actually close a case (RLS); a call from any
        other butler is forwarded through Switchboard's ``route()``.

        Returns ``{"status": "ok", "case": {...}}``.
        """
        if ctx.is_switchboard:
            try:
                case = await fleet_cases.close_case(pool, case_id=case_id, outcome=outcome)
            except fleet_cases.FleetCaseError as exc:
                return {"status": "error", "error": str(exc)}
            return {"status": "ok", "case": case}
        return await _dispatch_fleet_case_write(
            daemon,
            pool,
            butler_name,
            tool_name="close_case",
            args={"case_id": case_id, "outcome": outcome},
        )

    @_core_tool("fleet_cases")
    @tool_span("read_case", butler_name=butler_name)
    async def read_case(
        case_id: Annotated[str, Field(description="fleet_cases.id to read.")],
    ) -> dict:
        """Return one case with its accreted evidence and ledger links.

        Returns ``{"status": "ok", "case": {..., "evidence": [...], "links": [...]}}``,
        or an error result if no case with that id exists.
        """
        try:
            case = await fleet_cases.read_case(pool, case_id)
        except fleet_cases.FleetCaseError as exc:
            return {"status": "error", "error": str(exc)}
        if case is None:
            return {"status": "error", "error": f"No fleet case with id={case_id!r}."}
        return {"status": "ok", "case": case}
