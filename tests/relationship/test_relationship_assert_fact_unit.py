"""Unit-level regressions for relationship_assert_fact internals."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from butlers.tools.relationship.relationship_assert_fact import (
    AssertOutcome,
    _assert_on_conn,
    _create_pending_action,
    _upsert_fact,
    contact_info_type_to_predicate,
)

# ---------------------------------------------------------------------------
# Lazy loader for roster/relationship/api/models (not on sys.path; loaded
# once per session via importlib so no __init__.py is needed).
# ---------------------------------------------------------------------------


def _load_relationship_api_models():  # noqa: ANN201
    """Return the roster/relationship/api/models module (loaded on demand)."""
    module_name = "relationship_api_models"
    if module_name in sys.modules:
        return sys.modules[module_name]
    models_path = Path(__file__).parents[2] / "roster" / "relationship" / "api" / "models.py"
    spec = importlib.util.spec_from_file_location(module_name, models_path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_PRED_HAS_EMAIL = "has-email"


# ---------------------------------------------------------------------------
# contact_info_type_to_predicate — mapping contract (bead bu-55ggu)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_contact_info_type_to_predicate_known_types() -> None:
    """All supported contact_info types map to the correct predicate."""
    expected: dict[str, str] = {
        "email": "has-email",
        "phone": "has-phone",
        "telegram": "has-handle",
        "telegram_user_id": "has-handle",
        "telegram_username": "has-handle",
        "telegram_chat_id": "has-handle",
        "linkedin": "has-handle",
        "twitter": "has-handle",
        "website": "has-website",
        "other": "has-handle",
    }
    for ci_type, expected_predicate in expected.items():
        result = contact_info_type_to_predicate(ci_type)
        assert result == expected_predicate, (
            f"contact_info_type_to_predicate({ci_type!r}) returned {result!r}, "
            f"expected {expected_predicate!r}"
        )


@pytest.mark.unit
def test_contact_info_type_to_predicate_unmapped_returns_none() -> None:
    """Types with no predicate home return None (callers must skip the triple write)."""
    unmapped = [
        # telegram_chat_id is NOT here: RFC 0004 Amendment 3 (PR #2471) mapped it
        # to has-handle, so it is asserted in the known-types test above instead.
        "google_health",  # OAuth routing/credential identifier
        "home_assistant_url",  # service URL, not a contact channel
        "address",
        "fax",
        "unknown_type",
    ]
    for ci_type in unmapped:
        result = contact_info_type_to_predicate(ci_type)
        assert result is None, (
            f"contact_info_type_to_predicate({ci_type!r}) should return None "
            f"(unmapped type), but returned {result!r}"
        )


async def test_supersession_insert_is_conflict_safe() -> None:
    """The replacement insert must be conflict-safe via DO NOTHING (never DO UPDATE).

    Spec (bu-be16a): an ON CONFLICT ... DO UPDATE would overwrite conf/observed_at
    on an existing active row in place. The race-safe contract is
    ``INSERT ... ON CONFLICT DO NOTHING`` plus a re-read/retry that routes any
    collision through supersession.
    """
    old_id = uuid.uuid4()
    new_id = uuid.uuid4()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "id": old_id,
            "src": "source-a",
            "conf": 1.0,
            "verified": False,
            "last_seen": None,
        }
    )
    # The supersession UPDATE reports one row flipped active -> superseded.
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.fetchval = AsyncMock(return_value=new_id)

    result = await _upsert_fact(
        conn,
        subject=uuid.uuid4(),
        predicate=_PRED_HAS_EMAIL,
        object="alice@example.com",
        object_kind="literal",
        src="source-b",
        conf=1.0,
        last_seen=None,
        observed_at=datetime.now(UTC),
        weight=None,
        verified=False,
        primary=None,
    )

    insert_sql = conn.fetchval.call_args.args[0]
    assert result.outcome == AssertOutcome.superseded
    assert "ON CONFLICT (subject, predicate, object)" in insert_sql
    assert "WHERE validity = 'active'" in insert_sql
    assert "DO NOTHING" in insert_sql
    assert "DO UPDATE" not in insert_sql

    # The supersession UPDATE must be guarded on the row id AND validity='active'
    # so a lost race re-reads instead of double-superseding.
    update_sql = conn.execute.call_args.args[0]
    assert "validity   = 'superseded'" in update_sql or "validity = 'superseded'" in update_sql
    assert "validity = 'active'" in update_sql


# ---------------------------------------------------------------------------
# Owner carve-out: dedup + rationale (regression for duplicate reconciler
# approvals appearing every 30 min in the dashboard with blank why/evidence).
# ---------------------------------------------------------------------------


async def test_create_pending_action_reuses_existing_pending_row() -> None:
    """When dedup_match finds a pending row, no new INSERT and same id returned."""
    existing_id = uuid.uuid4()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=existing_id)
    conn.execute = AsyncMock()

    returned = await _create_pending_action(
        conn,
        "relationship_assert_fact",
        {"subject": "s", "predicate": "p", "object": "o"},
        "summary",
        dedup_match={"subject": "s", "predicate": "p", "object": "o"},
        why="why",
        evidence=["a", "b"],
    )

    assert returned == existing_id
    # The dedup probe ran, but no INSERT followed.
    probe_sql = conn.fetchval.call_args.args[0]
    assert "SELECT id FROM pending_actions" in probe_sql
    assert "tool_args @> $2::jsonb" in probe_sql
    conn.execute.assert_not_called()


async def test_create_pending_action_inserts_with_why_and_evidence() -> None:
    """When no pending duplicate exists, INSERT includes why and evidence columns."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)  # no existing pending row
    conn.execute = AsyncMock()

    returned = await _create_pending_action(
        conn,
        "relationship_assert_fact",
        {"subject": "s", "predicate": "p", "object": "o"},
        "summary",
        dedup_match={"subject": "s", "predicate": "p", "object": "o"},
        why="human-readable rationale",
        evidence=["ci_id=123", "contact_id=456"],
    )

    assert isinstance(returned, uuid.UUID)
    insert_sql = conn.execute.call_args.args[0]
    assert "INSERT INTO pending_actions" in insert_sql
    assert "why" in insert_sql
    assert "evidence" in insert_sql

    insert_params = conn.execute.call_args.args[1:]
    # Order: (id, tool_name, tool_args, summary, session_id, status,
    #         requested_at, expires_at, why, evidence)
    assert insert_params[1] == "relationship_assert_fact"
    assert insert_params[5] == "pending"
    assert insert_params[8] == "human-readable rationale"
    assert insert_params[9] == ["ci_id=123", "contact_id=456"]


async def test_owner_carveout_passes_dedup_match_and_rationale() -> None:
    """The owner branch of _assert_on_conn must dedup on identity and populate why/evidence."""
    subject_id = uuid.uuid4()
    existing_action_id = uuid.uuid4()

    conn = AsyncMock()

    # Sequence of fetchrow/fetchval calls in _assert_on_conn (owner branch):
    #  1. _validate_predicate: fetchval -> True (predicate is registered)
    #  2. _is_owner_entity:    fetchrow -> {"roles": ["owner"]}
    #  3. _create_pending_action dedup probe: fetchval -> existing_action_id (dedup HIT)
    conn.fetchval = AsyncMock(side_effect=[True, existing_action_id])
    conn.fetchrow = AsyncMock(return_value={"roles": ["owner"]})
    conn.execute = AsyncMock()

    result = await _assert_on_conn(
        conn,
        subject=subject_id,
        predicate=_PRED_HAS_EMAIL,
        object="owner@example.com",
        object_kind="literal",
        src="reconciler",
        conf=1.0,
        last_seen=None,
        observed_at=datetime.now(UTC),
        weight=None,
        verified=False,
        primary=True,
        wrap_transaction=False,
        why="reconciler rationale",
        evidence=["ci_id=abc"],
    )

    assert result.outcome == AssertOutcome.pending_approval
    assert result.action_id == existing_action_id
    assert result.fact_id is None
    # Dedup HIT means no INSERT (only the validate + dedup probe queries ran).
    conn.execute.assert_not_called()

    # The dedup probe should have matched on the identity triple, not on
    # provenance fields. Inspect the JSONB probe argument.
    dedup_call_args = conn.fetchval.call_args_list[1].args
    probe_jsonb = dedup_call_args[2]
    assert probe_jsonb["subject"] == str(subject_id)
    assert probe_jsonb["predicate"] == _PRED_HAS_EMAIL
    assert probe_jsonb["object"] == "owner@example.com"
    assert probe_jsonb["object_kind"] == "literal"
    # Provenance fields must NOT be in the probe — otherwise two reconciler
    # runs with different `conf` would each create their own pending row.
    assert "src" not in probe_jsonb
    assert "conf" not in probe_jsonb


async def test_owner_carveout_inserts_with_caller_supplied_why() -> None:
    """When no pending duplicate exists, the carve-out INSERT carries the caller's why/evidence."""
    subject_id = uuid.uuid4()

    conn = AsyncMock()
    # fetchval sequence: predicate-registered=True, dedup probe miss=None.
    conn.fetchval = AsyncMock(side_effect=[True, None])
    conn.fetchrow = AsyncMock(return_value={"roles": ["owner"]})
    conn.execute = AsyncMock()

    caller_why = "The contact-info reconciler found a missing has-email triple."
    caller_evidence = ["ci_id=xyz", "contact_id=789", "is_primary=True"]

    result = await _assert_on_conn(
        conn,
        subject=subject_id,
        predicate=_PRED_HAS_EMAIL,
        object="owner@example.com",
        object_kind="literal",
        src="reconciler",
        conf=1.0,
        last_seen=None,
        observed_at=datetime.now(UTC),
        weight=None,
        verified=False,
        primary=True,
        wrap_transaction=False,
        why=caller_why,
        evidence=caller_evidence,
    )

    assert result.outcome == AssertOutcome.pending_approval
    insert_params = conn.execute.call_args.args[1:]
    # (id, tool_name, tool_args, summary, session_id, status,
    #  requested_at, expires_at, why, evidence)
    assert insert_params[8] == caller_why
    assert insert_params[9] == caller_evidence


# ---------------------------------------------------------------------------
# Security regression: owner-self carve-out src non-spoofable (bu-vj46x)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_mcp_tool_wrapper_has_no_src_parameter() -> None:
    """The MCP tool wrapper must NOT expose ``src`` as a public parameter.

    Removing ``src`` from the tool signature is the primary enforcement layer
    that prevents LLM sessions from supplying a trusted source and bypassing
    the owner carve-out gate (bu-vj46x).

    This test inspects the wrapper function's signature directly.  If ``src``
    reappears (e.g. after a merge conflict), the test fails immediately.
    """
    # Import the module that registers MCP tools; locate the wrapper by name.
    # The tools module must be importable (it is under src/butlers via the
    # module loader) through the standard module discovery path.
    # We use a lazy importlib load that mirrors what conftest does so that
    # this test remains isolated and does not depend on module registration
    # order.
    module_name = "relationship_mcp_tools_wrapper"
    if module_name not in sys.modules:
        tools_path = Path(__file__).parents[2] / "roster" / "relationship" / "modules" / "tools.py"
        spec = importlib.util.spec_from_file_location(module_name, tools_path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[module_name] = mod
        try:
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except Exception:
            # The module performs DB/pool setup on exec which may fail in unit
            # tests.  We only need the source text, so fall back to a raw
            # source inspection if exec fails.
            del sys.modules[module_name]
            source = tools_path.read_text()
            assert "src: str" not in source or "src=.relationship." in source, (
                "relationship MCP tools wrapper unexpectedly exposes 'src' as a "
                "free-form str parameter — this allows LLM sessions to supply "
                "trusted source values and bypass the owner carve-out gate."
            )
            return

    # If exec succeeded, check the signature of the inner wrapper function.
    # The function is nested inside register_tools(); we can still inspect
    # the source for the signature contract.
    tools_path = Path(__file__).parents[2] / "roster" / "relationship" / "modules" / "tools.py"
    source = tools_path.read_text()

    # The wrapper must NOT have 'src: str' in the relationship_assert_fact
    # tool function block.  A hardcoded src="relationship" assignment is
    # expected at the call site instead.
    assert 'src="relationship"' in source, (
        "Expected hardcoded src='relationship' in relationship MCP tools wrapper "
        "but it was not found — the security fix (bu-vj46x) may have been reverted."
    )
    # Grep for a 'src: str' parameter declaration in the tool definition block.
    # We look for the pattern within 20 lines of the def to avoid false positives
    # from other tools (e.g. relationship_lookup which legitimately has no src).
    lines = source.splitlines()
    in_raf_tool = False
    for i, line in enumerate(lines):
        if "async def relationship_assert_fact(" in line:
            in_raf_tool = True
            tool_start = i
        if in_raf_tool and i > tool_start and "async def " in line:
            # Entered the next tool definition — stop scanning.
            break
        if in_raf_tool and "src: str" in line:
            pytest.fail(
                "relationship_assert_fact MCP tool wrapper exposes 'src: str' "
                "as a public parameter at line "
                f"{i + 1}: {line.strip()!r}\n"
                "This allows LLM sessions to pass src='owner-self' and bypass "
                "the owner carve-out gate (bu-vj46x)."
            )


@pytest.mark.unit
@pytest.mark.parametrize(
    "model_name,kwargs,trusted_src",
    [
        (
            "AddContactRequest",
            {"predicate": "has-email", "value": "attacker@evil.com"},
            "owner-self",
        ),
        (
            "AddContactRequest",
            {"predicate": "has-phone", "value": "+10000000000"},
            "owner-bootstrap",
        ),
        ("UpdateContactRequest", {"new_value": "attacker@evil.com"}, "owner-self"),
        ("UpdateContactRequest", {"new_value": "+10000000000"}, "owner-bootstrap"),
    ],
    ids=[
        "add-owner-self",
        "add-owner-bootstrap",
        "update-owner-self",
        "update-owner-bootstrap",
    ],
)
def test_contact_request_rejects_trusted_internal_src(
    model_name: str, kwargs: dict, trusted_src: str
) -> None:
    """Add/UpdateContactRequest must reject reserved internal src values from HTTP
    callers — the loc=('src',) error is the security gate (bu-vj46x). Both models
    enumerate owner-self and owner-bootstrap."""
    from pydantic import ValidationError

    models = _load_relationship_api_models()
    model = getattr(models, model_name)

    with pytest.raises(ValidationError) as exc_info:
        model(src=trusted_src, **kwargs)

    errors = exc_info.value.errors()
    assert any(e["loc"] == ("src",) for e in errors), (
        "Expected validation error on 'src' field, got: " + str([e["loc"] for e in errors])
    )


@pytest.mark.unit
def test_add_contact_request_accepts_untrusted_custom_src() -> None:
    """AddContactRequest must accept any non-reserved src value.

    Validation must only block the specific reserved strings, not all custom
    sources (bu-vj46x — guard is targeted, not a blanket block).
    """
    models = _load_relationship_api_models()
    AddContactRequest = models.AddContactRequest

    # These should all succeed without raising.
    for src_value in ("relationship", "reconciler", "import", "user-provided", ""):
        req = AddContactRequest(predicate="has-email", value="x@example.com", src=src_value)
        assert req.src == src_value


@pytest.mark.unit
async def test_owner_entity_with_non_trusted_src_parks_to_pending() -> None:
    """When src is NOT in _OWNER_SELF_SOURCES and subject is owner, assert parks.

    This covers the MCP tool path: since the wrapper hardcodes src='relationship',
    owner-entity assertions from LLM sessions always go to pending_approval — the
    carve-out bypass is unreachable via MCP regardless of what the LLM requests.
    """
    subject_id = uuid.uuid4()

    conn = AsyncMock()
    # fetchval sequence: predicate-registered=True, dedup-probe-miss=None.
    conn.fetchval = AsyncMock(side_effect=[True, None])
    conn.fetchrow = AsyncMock(return_value={"roles": ["owner"]})
    conn.execute = AsyncMock()

    result = await _assert_on_conn(
        conn,
        subject=subject_id,
        predicate=_PRED_HAS_EMAIL,
        object="owner@example.com",
        object_kind="literal",
        src="relationship",  # The value MCP tool hardcodes — NOT a trusted source.
        conf=1.0,
        last_seen=None,
        observed_at=datetime.now(UTC),
        weight=None,
        verified=False,
        primary=None,
        wrap_transaction=False,
        why=None,
        evidence=None,
    )

    # With src='relationship' (not in _OWNER_SELF_SOURCES), the owner carve-out
    # gate fires and the write parks to pending_approval.
    assert result.outcome == AssertOutcome.pending_approval, (
        f"Expected pending_approval but got {result.outcome!r} — "
        "an LLM session's write to owner entity should always park, never bypass."
    )
    assert result.fact_id is None
    assert result.action_id is not None
