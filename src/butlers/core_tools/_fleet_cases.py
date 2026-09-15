"""Fleet Case File contribution tools (bu-8cdl1.7 Slices 3-4 and 7, RFC 0032).

See ``src/butlers/core/fleet_cases.py`` for the shared data layer and
``about/legends-and-lore/rfcs/0032-fleet-case-file.md`` for the full design.
Slice 1 (schema/RLS, core_217) and Slice 2 (read API,
``roster/switchboard/api/router.py``) already landed; Slice 3 adds six MCP
tools: ``find_open_case``, ``open_case``, ``contribute_case_evidence``,
``propose_case_posture``, ``close_case``, ``read_case``. Slice 4
(situation-scoped attention) adds no new tool -- it folds a
``case_attention`` field into ``contribute_case_evidence`` and
``propose_case_posture``'s existing results (see
``fleet_cases.evaluate_case_attention``). Slice 7 (three-ledger binding)
adds one new tool, ``record_case_link``, and wires it into the two call
sites that can observe a genuine cross-ledger reference: a linkable
``kind``/``ref`` on ``contribute_case_evidence``, and an actually-recorded
urgent-bypass event from ``evaluate_case_attention``.

Registered fleet-wide, every butler type included (unlike ``domain_events``/
``delegation``, which exclude STAFFER): Switchboard itself is a STAFFER and is
the one role that can actually write ``fleet_cases``/``fleet_case_links``
(RLS, ``core_217_fleet_case_file.py``), so it needs these tools registered on
its own daemon too, not just as a routing target.

``open_case``/``propose_case_posture``/``close_case``/``record_case_link``
mutate ``fleet_cases``/``fleet_case_links``, both RLS-restricted to
``butler_switchboard_rw``. On Switchboard's own daemon the tool writes
directly through the caller's own pool (already the switchboard role); on
every other butler's daemon it is forwarded through the Switchboard's
``route()`` primitive to Switchboard's own registration of the same tool --
mirroring ``_delegation.py``/``_domain_events.py``'s existing
client-vs-self-delivery split via the shared
``_switchboard_route_dispatch.dispatch_via_switchboard_route`` -- so the
actual write always lands under Switchboard's own role regardless of which
butler initiated it. ``propose_case_posture``'s "Switchboard arbitrates" is,
in this slice, a plain last-write-wins update once forwarded; no quorum/
voting model ships here.

``find_open_case`` and ``read_case`` need no forwarding: both
``fleet_cases``/``fleet_case_links`` are readable by every role. Evidence
insertion inside ``contribute_case_evidence`` also needs no forwarding
(``fleet_case_evidence`` has no RLS restriction), but that same call can
now also produce a ``fleet_case_links`` write (Slice 7) when the evidence
cites one of the three ledgers -- that write goes through the same
Switchboard-forwarding helper as ``record_case_link`` itself, so
``contribute_case_evidence`` is no longer unconditionally forwarding-free.
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


async def _write_case_link(
    ctx: ToolContext,
    daemon: Any,
    pool: Any,
    butler_name: str,
    *,
    case_id: str,
    link_kind: str,
    ref: str,
) -> dict[str, Any]:
    """Write one ``fleet_case_links`` row, forwarding through Switchboard when
    this daemon isn't Switchboard's own -- the same is_switchboard split as
    ``open_case``/``propose_case_posture``/``close_case``, shared by
    ``record_case_link`` itself and by the two internal call sites
    (``contribute_case_evidence``, ``propose_case_posture``) that can also
    observe a genuine cross-ledger reference (RFC 0032 Slice 7).
    """
    if ctx.is_switchboard:
        try:
            link, newly_recorded = await fleet_cases.write_case_link(
                pool, case_id=case_id, link_kind=link_kind, ref=ref
            )
        except fleet_cases.FleetCaseError as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "ok", "link": link, "newly_recorded": newly_recorded}
    return await _dispatch_fleet_case_write(
        daemon,
        pool,
        butler_name,
        tool_name="record_case_link",
        args={"case_id": case_id, "link_kind": link_kind, "ref": ref},
    )


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
                    "What kind of evidence this is. Open vocabulary, your own words -- "
                    "e.g. 'session', 'observation' -- except for three reserved values "
                    "that also bind this case to another ledger's entry (RFC 0032 Slice "
                    "7): 'insight_candidate', 'owner_condition', 'attention_record'. Use "
                    "one of those three only when ref is that ledger's own id and you "
                    "have a genuine reason to believe it is about this case's situation "
                    "-- not a speculative or incidental correlation."
                )
            ),
        ],
        ref: Annotated[
            str,
            Field(
                description=(
                    "A reference to what you observed -- a session id, a task name, or "
                    "(for the three reserved kind values above) that ledger's own id: "
                    "public.insight_candidates.id, public.owner_conditions.id, or "
                    "public.attention_ledger.id. Something a reader can open."
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

        When *kind* is one of the three reserved ``fleet_cases.LINK_KINDS``
        values (RFC 0032 Slice 7), a newly-recorded contribution also binds
        the case to that ledger entry via ``fleet_case_links`` -- forwarded
        through Switchboard when this daemon isn't Switchboard's own.

        Returns ``{"status": "ok", "evidence": {...}, "newly_recorded": bool,
        "case_attention": {...} | None, "link": {...} | None}``.
        ``case_attention`` (Slice 4, situation-scoped attention) is only
        evaluated for a newly-recorded contribution -- a repeat report is a
        no-op and cannot itself trigger a quiet-hours bypass -- and is
        ``None`` when the case no longer exists by the time attention is
        evaluated. See ``fleet_cases.evaluate_case_attention`` for what its
        fields mean. ``link`` is the Slice 7 binding described above, or
        ``None`` when *kind* isn't a reserved link kind, the contribution
        wasn't newly recorded, or the link write itself failed (a link
        failure never fails the evidence contribution it accompanies).
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
        link = None
        if newly_recorded:
            if kind in fleet_cases.LINK_KINDS:
                link_result = await _write_case_link(
                    ctx, daemon, pool, butler_name, case_id=case_id, link_kind=kind, ref=ref
                )
                if link_result.get("status") == "ok":
                    link = link_result.get("link")
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
                attention_ledger_id = case_attention.get("attention_ledger_id")
                if case_attention.get("bypass") and attention_ledger_id:
                    await _write_case_link(
                        ctx,
                        daemon,
                        pool,
                        butler_name,
                        case_id=case_id,
                        link_kind="attention_record",
                        ref=attention_ledger_id,
                    )
        return {
            "status": "ok",
            "evidence": evidence,
            "newly_recorded": newly_recorded,
            "case_attention": case_attention,
            "link": link,
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
        ``fleet_cases.evaluate_case_attention`` for what its fields mean. An
        actually-recorded bypass also binds the case to that
        ``attention_ledger`` row via ``fleet_case_links`` (Slice 7).
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
            attention_ledger_id = case_attention.get("attention_ledger_id")
            if case_attention.get("bypass") and attention_ledger_id:
                await _write_case_link(
                    ctx,
                    daemon,
                    pool,
                    butler_name,
                    case_id=case_id,
                    link_kind="attention_record",
                    ref=attention_ledger_id,
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
    @tool_span("record_case_link", butler_name=butler_name)
    async def record_case_link(
        case_id: Annotated[str, Field(description="fleet_cases.id to bind.")],
        link_kind: Annotated[
            str,
            Field(description=("One of: insight_candidate, owner_condition, attention_record.")),
        ],
        ref: Annotated[
            str,
            Field(
                description=(
                    "The other ledger's own id for the entry you are binding to -- "
                    "public.insight_candidates.id, public.owner_conditions.id, or "
                    "public.attention_ledger.id."
                )
            ),
        ],
    ) -> dict:
        """Bind this case to a genuine, already-existing entry in another ledger.

        RFC 0032 Slice 7 -- the three-ledger binding. Only call this for an
        explicit cross-ledger reference you have a concrete id for; do not
        call it for a speculative or incidental correlation.
        ``contribute_case_evidence``/``propose_case_posture`` already call
        this automatically when they observe one (a linkable evidence
        ``kind``, or an urgent-bypass attention event), so most callers never
        need to call it directly.

        Idempotent: repeating the same ``(case_id, link_kind, ref)`` is a
        no-op, not a duplicate row. Only Switchboard may actually write
        ``fleet_case_links`` (RLS); a call from any other butler is forwarded
        through Switchboard's ``route()``.

        Returns ``{"status": "ok", "link": {...}, "newly_recorded": bool}``.
        """
        return await _write_case_link(
            ctx, daemon, pool, butler_name, case_id=case_id, link_kind=link_kind, ref=ref
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
