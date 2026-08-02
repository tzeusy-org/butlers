"""Real-Postgres checks for the dashboard-turn Stop control migration.

The cancellation protocol is deliberately implemented in SECURITY DEFINER SQL
because its users span the API, Switchboard, and target-butler processes. Unit
mocks cannot prove the transition ordering or the table-vs-function ACL split,
so this test drives the actual core Alembic chain against PostgreSQL.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _turn_call(
    conn,
    function: str,
    *,
    role: str | None = None,
    **params: object,
) -> dict[str, object]:
    """Execute one dashboard-turn function and return its sole result row."""
    placeholders = ", ".join(f":{name}" for name in params)
    if role is not None:
        conn.execute(text(f"SET ROLE {_quote_identifier(role)}"))
    try:
        row = (
            conn.execute(text(f"SELECT * FROM public.{function}({placeholders})"), params)
            .mappings()
            .one()
        )
        return dict(row)
    finally:
        if role is not None:
            conn.execute(text("RESET ROLE"))


def _seed_dashboard_user_turn(conn) -> tuple[uuid.UUID, uuid.UUID]:
    conversation_id = uuid.uuid4()
    message_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO public.dashboard_conversations
                (id, butler_name, title, status, created_at, updated_at)
            VALUES (:conversation_id, 'switchboard', 'Stop protocol test', 'active', now(), now())
            """
        ),
        {"conversation_id": conversation_id},
    )
    conn.execute(
        text(
            """
            INSERT INTO public.dashboard_messages
                (id, conversation_id, role, content, created_at)
            VALUES (:message_id, :conversation_id, 'user', 'Please handle this.', now())
            """
        ),
        {"message_id": message_id, "conversation_id": conversation_id},
    )
    return conversation_id, message_id


def _execute_as_role(db_url: str, role: str, sql: str, *, scalar: bool = False):
    quoted_role = _quote_identifier(role)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET ROLE {quoted_role}"))
            try:
                result = conn.execute(text(sql))
                return result.scalar() if scalar else None
            finally:
                conn.execute(text("RESET ROLE"))
    finally:
        engine.dispose()


def test_dashboard_turn_stop_transitions_and_runtime_acl(postgres_container) -> None:
    """Stop wins pre-invoke, waits through active invocation, and keeps tables private."""
    from butlers.migrations import run_migrations

    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            # Normal ingress claims at the generator boundary, then a Stop
            # before any runtime starts terminally blocks a later route target.
            conversation_id, message_id = _seed_dashboard_user_turn(conn)
            request_id = uuid.uuid4()
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_open",
                    p_message_id=message_id,
                    p_conversation_id=conversation_id,
                )["outcome"]
                == "ready"
            )
            assert (
                _turn_call(conn, "dashboard_turn_claim_ingress", p_message_id=message_id)["outcome"]
                == "dispatch"
            )
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_bind_ingress",
                    p_message_id=message_id,
                    p_request_id=request_id,
                )["outcome"]
                == "accepted"
            )
            assert (
                _turn_call(conn, "dashboard_turn_request_cancel", p_message_id=message_id)[
                    "outcome"
                ]
                == "cancelled"
            )
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_claim_target",
                    role="butler_general_rw",
                    p_message_id=message_id,
                    p_request_id=request_id,
                    p_target_butler="general",
                )["outcome"]
                == "cancelled"
            )

            # A Stop after the API creates the durable row but before the SSE
            # generator claims outbound ingress prevents Switchboard dispatch.
            conversation_id, message_id = _seed_dashboard_user_turn(conn)
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_open",
                    p_message_id=message_id,
                    p_conversation_id=conversation_id,
                )["outcome"]
                == "ready"
            )
            assert (
                _turn_call(conn, "dashboard_turn_request_cancel", p_message_id=message_id)[
                    "outcome"
                ]
                == "cancelled"
            )
            assert (
                _turn_call(conn, "dashboard_turn_claim_ingress", p_message_id=message_id)["outcome"]
                == "cancelled"
            )

            # If Stop races an already-claimed outbound ingress, the API is
            # initially honest that the network call is still settling. Once
            # that call returns accepted and no runtime ever crossed invoke,
            # the control row must become terminally cancelled without asking
            # the owner to click Stop a second time.
            conversation_id, message_id = _seed_dashboard_user_turn(conn)
            request_id = uuid.uuid4()
            _turn_call(
                conn,
                "dashboard_turn_open",
                p_message_id=message_id,
                p_conversation_id=conversation_id,
            )
            assert (
                _turn_call(conn, "dashboard_turn_claim_ingress", p_message_id=message_id)["outcome"]
                == "dispatch"
            )
            assert (
                _turn_call(conn, "dashboard_turn_request_cancel", p_message_id=message_id)[
                    "outcome"
                ]
                == "settling"
            )
            assert (
                _turn_call(conn, "dashboard_turn_claim_ingress", p_message_id=message_id)["outcome"]
                == "cancelling"
            )
            settled_ingress = _turn_call(
                conn,
                "dashboard_turn_bind_ingress",
                p_message_id=message_id,
                p_request_id=request_id,
            )
            assert settled_ingress["outcome"] == "cancelled"
            assert settled_ingress["terminal_state"] == "cancelled"
            assert (
                _turn_call(conn, "dashboard_turn_dispatch_status", p_message_id=message_id)[
                    "outcome"
                ]
                == "cancelled"
            )

            # The same terminal proof is required when the already-claimed
            # ingress settles as an error: no runtime crossed invoke, so the
            # Stop must not leave a permanently "cancelling" row behind.
            conversation_id, message_id = _seed_dashboard_user_turn(conn)
            _turn_call(
                conn,
                "dashboard_turn_open",
                p_message_id=message_id,
                p_conversation_id=conversation_id,
            )
            assert (
                _turn_call(conn, "dashboard_turn_claim_ingress", p_message_id=message_id)["outcome"]
                == "dispatch"
            )
            assert (
                _turn_call(conn, "dashboard_turn_request_cancel", p_message_id=message_id)[
                    "outcome"
                ]
                == "settling"
            )
            settled_failure = _turn_call(
                conn,
                "dashboard_turn_record_ingress_failure",
                p_message_id=message_id,
                p_state="retryable_error",
                p_detail="Switchboard unavailable",
            )
            assert settled_failure["outcome"] == "cancelled"
            assert settled_failure["terminal_state"] == "cancelled"
            assert (
                _turn_call(conn, "dashboard_turn_dispatch_status", p_message_id=message_id)[
                    "outcome"
                ]
                == "cancelled"
            )

            # Once the generator has crossed its outbound claim, Stop stays
            # settling until the later Spawner registration proves no runtime
            # crossed invoke; that proof then becomes a real cancellation.
            conversation_id, message_id = _seed_dashboard_user_turn(conn)
            request_id = uuid.uuid4()
            session_id = uuid.uuid4()
            _turn_call(
                conn,
                "dashboard_turn_open",
                p_message_id=message_id,
                p_conversation_id=conversation_id,
            )
            assert (
                _turn_call(conn, "dashboard_turn_claim_ingress", p_message_id=message_id)["outcome"]
                == "dispatch"
            )
            assert (
                _turn_call(conn, "dashboard_turn_request_cancel", p_message_id=message_id)[
                    "outcome"
                ]
                == "settling"
            )
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_register_session",
                    role="butler_switchboard_rw",
                    p_message_id=message_id,
                    p_session_id=session_id,
                    p_request_id=request_id,
                    p_butler_name="switchboard",
                    p_phase="classification",
                )["outcome"]
                == "cancelled"
            )
            settled = _turn_call(
                conn,
                "dashboard_turn_complete_session",
                role="butler_switchboard_rw",
                p_message_id=message_id,
                p_session_id=session_id,
                p_success=False,
            )
            assert settled["outcome"] == "cancelled"
            assert settled["terminal_state"] == "cancelled"

            # Once an invocation is live, Stop remains non-terminal until the
            # runtime releases its active pre-invocation claim and API confirms it.
            conversation_id, message_id = _seed_dashboard_user_turn(conn)
            request_id = uuid.uuid4()
            session_id = uuid.uuid4()
            inbox_id = uuid.uuid4()
            _turn_call(
                conn,
                "dashboard_turn_open",
                p_message_id=message_id,
                p_conversation_id=conversation_id,
            )
            _turn_call(
                conn,
                "dashboard_turn_bind_ingress",
                p_message_id=message_id,
                p_request_id=request_id,
            )
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_claim_target",
                    role="butler_general_rw",
                    p_message_id=message_id,
                    p_request_id=request_id,
                    p_target_butler="general",
                )["outcome"]
                == "active"
            )
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_mark_route_enqueued",
                    role="butler_general_rw",
                    p_message_id=message_id,
                    p_route_inbox_id=inbox_id,
                )["outcome"]
                == "active"
            )
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_register_session",
                    role="butler_general_rw",
                    p_message_id=message_id,
                    p_session_id=session_id,
                    p_request_id=request_id,
                    p_butler_name="general",
                    p_phase="route",
                )["outcome"]
                == "active"
            )
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_claim_invoke",
                    role="butler_general_rw",
                    p_message_id=message_id,
                    p_session_id=session_id,
                )["outcome"]
                == "active"
            )
            assert (
                _turn_call(conn, "dashboard_turn_request_cancel", p_message_id=message_id)[
                    "outcome"
                ]
                == "cancelling"
            )
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_release_invoke",
                    role="butler_general_rw",
                    p_message_id=message_id,
                    p_session_id=session_id,
                )["outcome"]
                == "cancelling"
            )
            assert (
                _turn_call(conn, "dashboard_turn_confirm_cancel", p_message_id=message_id)[
                    "outcome"
                ]
                == "settling"
            )
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_acknowledge_cancel",
                    role="butler_general_rw",
                    p_message_id=message_id,
                    p_session_id=session_id,
                )["outcome"]
                == "cancelling"
            )
            assert (
                _turn_call(conn, "dashboard_turn_confirm_cancel", p_message_id=message_id)[
                    "outcome"
                ]
                == "cancelled"
            )

            # A Stop after one classifier attempt ended is settling, not a false
            # kill confirmation. Its concrete completed outcome becomes terminal.
            conversation_id, message_id = _seed_dashboard_user_turn(conn)
            request_id = uuid.uuid4()
            session_id = uuid.uuid4()
            _turn_call(
                conn,
                "dashboard_turn_open",
                p_message_id=message_id,
                p_conversation_id=conversation_id,
            )
            _turn_call(
                conn,
                "dashboard_turn_bind_ingress",
                p_message_id=message_id,
                p_request_id=request_id,
            )
            _turn_call(
                conn,
                "dashboard_turn_register_session",
                role="butler_switchboard_rw",
                p_message_id=message_id,
                p_session_id=session_id,
                p_request_id=request_id,
                p_butler_name="switchboard",
                p_phase="classification",
            )
            _turn_call(
                conn,
                "dashboard_turn_claim_invoke",
                role="butler_switchboard_rw",
                p_message_id=message_id,
                p_session_id=session_id,
            )
            _turn_call(
                conn,
                "dashboard_turn_release_invoke",
                role="butler_switchboard_rw",
                p_message_id=message_id,
                p_session_id=session_id,
            )
            assert (
                _turn_call(conn, "dashboard_turn_request_cancel", p_message_id=message_id)[
                    "outcome"
                ]
                == "settling"
            )
            settled = _turn_call(
                conn,
                "dashboard_turn_complete_session",
                role="butler_switchboard_rw",
                p_message_id=message_id,
                p_session_id=session_id,
                p_success=False,
            )
            assert settled["outcome"] == "finished"
            assert settled["terminal_state"] == "failed"

            # A stale route-inbox lease does not prove that the old target
            # runtime is dead. Recovery must therefore preserve the live
            # predecessor as evidence, terminalize the turn as ambiguous, and
            # never authorize a replacement runtime that could duplicate its
            # external effects.
            conversation_id, message_id = _seed_dashboard_user_turn(conn)
            request_id = uuid.uuid4()
            inbox_id = uuid.uuid4()
            crashed_session_id = uuid.uuid4()
            replacement_session_id = uuid.uuid4()
            _turn_call(
                conn,
                "dashboard_turn_open",
                p_message_id=message_id,
                p_conversation_id=conversation_id,
            )
            _turn_call(
                conn,
                "dashboard_turn_bind_ingress",
                p_message_id=message_id,
                p_request_id=request_id,
            )
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_claim_target",
                    role="butler_general_rw",
                    p_message_id=message_id,
                    p_request_id=request_id,
                    p_target_butler="general",
                )["outcome"]
                == "active"
            )
            _turn_call(
                conn,
                "dashboard_turn_mark_route_enqueued",
                role="butler_general_rw",
                p_message_id=message_id,
                p_route_inbox_id=inbox_id,
            )
            _turn_call(
                conn,
                "dashboard_turn_register_session",
                role="butler_general_rw",
                p_message_id=message_id,
                p_session_id=crashed_session_id,
                p_request_id=request_id,
                p_butler_name="general",
                p_phase="route",
            )
            _turn_call(
                conn,
                "dashboard_turn_claim_invoke",
                role="butler_general_rw",
                p_message_id=message_id,
                p_session_id=crashed_session_id,
            )
            recovered = _turn_call(
                conn,
                "dashboard_turn_reconcile_route_recovery",
                role="butler_general_rw",
                p_message_id=message_id,
                p_request_id=request_id,
                p_route_inbox_id=inbox_id,
            )
            assert recovered["outcome"] == "ambiguous"
            assert recovered["terminal_state"] == "ambiguous"
            crashed_session = (
                conn.execute(
                    text(
                        """
                    SELECT invoke_active, completed_at
                    FROM public.dashboard_conversation_turn_sessions
                    WHERE message_id = :message_id AND session_id = :session_id
                    """
                    ),
                    {"message_id": message_id, "session_id": crashed_session_id},
                )
                .mappings()
                .one()
            )
            assert crashed_session["invoke_active"] is True
            assert crashed_session["completed_at"] is None
            # Repeated recovery observes the same unresolved terminal fact;
            # it must not recast it as a normal finished turn.
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_reconcile_route_recovery",
                    role="butler_general_rw",
                    p_message_id=message_id,
                    p_request_id=request_id,
                    p_route_inbox_id=inbox_id,
                )["outcome"]
                == "ambiguous"
            )
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_register_session",
                    role="butler_general_rw",
                    p_message_id=message_id,
                    p_session_id=replacement_session_id,
                    p_request_id=request_id,
                    p_butler_name="general",
                    p_phase="route",
                )["outcome"]
                == "ambiguous"
            )

            # An ambiguous predecessor remains non-replayable but Stop still
            # persists intent and addresses its exact active session. Even a
            # successful local cancellation cannot erase the fact that work
            # before the lost lease remains unprovable.
            assert (
                _turn_call(conn, "dashboard_turn_request_cancel", p_message_id=message_id)[
                    "outcome"
                ]
                == "cancelling"
            )
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_acknowledge_cancel",
                    role="butler_general_rw",
                    p_message_id=message_id,
                    p_session_id=crashed_session_id,
                )["outcome"]
                == "ambiguous"
            )
            assert (
                _turn_call(conn, "dashboard_turn_confirm_cancel", p_message_id=message_id)[
                    "outcome"
                ]
                == "ambiguous"
            )
            assert (
                _turn_call(conn, "dashboard_turn_dispatch_status", p_message_id=message_id)[
                    "outcome"
                ]
                == "ambiguous"
            )

            # A target runtime may only mutate the target assigned to its own
            # active database role; General cannot terminalize a Finance turn.
            conversation_id, finance_message_id = _seed_dashboard_user_turn(conn)
            finance_request_id = uuid.uuid4()
            _turn_call(
                conn,
                "dashboard_turn_open",
                p_message_id=finance_message_id,
                p_conversation_id=conversation_id,
            )
            _turn_call(
                conn,
                "dashboard_turn_bind_ingress",
                p_message_id=finance_message_id,
                p_request_id=finance_request_id,
            )
            assert (
                _turn_call(
                    conn,
                    "dashboard_turn_claim_target",
                    role="butler_finance_rw",
                    p_message_id=finance_message_id,
                    p_request_id=finance_request_id,
                    p_target_butler="finance",
                )["outcome"]
                == "active"
            )
            with pytest.raises(ProgrammingError, match="dashboard turn control is not authorized"):
                _turn_call(
                    conn,
                    "dashboard_turn_mark_terminal",
                    role="butler_general_rw",
                    p_message_id=finance_message_id,
                    p_state="completed",
                )

            # Seed one more row for the runtime-role function-vs-table ACL check.
            conversation_id, message_id = _seed_dashboard_user_turn(conn)
            request_id = uuid.uuid4()
            _turn_call(
                conn,
                "dashboard_turn_open",
                p_message_id=message_id,
                p_conversation_id=conversation_id,
            )
            _turn_call(
                conn,
                "dashboard_turn_bind_ingress",
                p_message_id=message_id,
                p_request_id=request_id,
            )
    finally:
        engine.dispose()

    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(
            db_url,
            "butler_general_rw",
            "INSERT INTO public.dashboard_conversation_turns "
            "(message_id, conversation_id) VALUES (gen_random_uuid(), gen_random_uuid())",
        )
    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(
            db_url,
            "connector_writer",
            "INSERT INTO public.dashboard_conversation_turns "
            "(message_id, conversation_id) VALUES (gen_random_uuid(), gen_random_uuid())",
        )
    with pytest.raises(ProgrammingError, match="dashboard turn control is not authorized"):
        _execute_as_role(
            db_url,
            "butler_health_rw",
            "SELECT outcome FROM public.dashboard_turn_claim_target("
            f"'{message_id}', '{request_id}', 'general'"
            ")",
            scalar=True,
        )
    assert (
        _execute_as_role(
            db_url,
            "butler_general_rw",
            "SELECT outcome FROM public.dashboard_turn_claim_target("
            f"'{message_id}', '{request_id}', 'general'"
            ")",
            scalar=True,
        )
        == "active"
    )
