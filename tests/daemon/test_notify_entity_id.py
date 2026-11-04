"""Tests for notify() entity_id parameter and contact-based resolution.

Covers tasks 7.1-7.4 from the contacts-identity-model spec:
  7.1 - notify() accepts entity_id parameter
  7.2 - entity_id resolves to channel identifier via entity_facts (bu-tv67t migration)
  7.3 - missing identifier parks action and notifies owner
  7.4 - neither param defaults to owner resolution

Migration notes (bu-km8xr):
  Resolution queries relationship.entity_facts keyed directly on entity_id — no
  public.contacts indirection. Tests seed entity_facts rows and assert queries
  target that path.

Every test runs a real booted daemon (bu-rvgzmf.2). Tests that deliver, park,
refuse with a ledger row or seed rows get a fresh boot; the rest share one.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.identity import ResolvedContact
from butlers.testing.daemon import write_butler_config
from butlers.testing.db import open_pool

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _fresh(booted_daemon, tmp_path: Path):
    """A butler of its own, for a test that writes rows."""
    return await booted_daemon(write_butler_config(tmp_path, "general"))


async def _relationship_butler(booted_daemon, tmp_path: Path):
    """The Relationship butler: the only runtime role that reads relationship.entity_facts.

    Other butlers' roles have no USAGE on the relationship schema, so their resolver
    returns None for every entity (see the xfail below, bu-rvgzmf.13).
    """
    return await booted_daemon(
        write_butler_config(tmp_path, "relationship", schema="relationship"), "core", "relationship"
    )


async def _notify_fn(booted) -> Callable[..., Any]:
    """The daemon's registered notify tool, called in process.

    In process because these tests pass arguments MCP never carries as written
    (``priority`` is excluded from the schema; entity ids are UUID objects).
    """
    tool = await booted.daemon.mcp.get_tool("notify")
    return tool.fn


def _use_client(monkeypatch: pytest.MonkeyPatch, daemon: Any, client: Any) -> Any:
    monkeypatch.setattr(daemon, "switchboard_client", client)
    return client


async def _ledger_rows(booted) -> list[Any]:
    return await booted.daemon.db.pool.fetch(
        "SELECT outcome, reason, metadata::text AS metadata FROM public.attention_ledger"
        " ORDER BY occurred_at, id"
    )


async def _seed_entity(
    booted, facts: list[tuple[str, str]], *, posture: str | None = None
) -> uuid.UUID:
    """Insert a person and their literal facts through the clone's owner."""
    admin = await open_pool(booted.admin_url)
    try:
        entity_id = await admin.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, 'person')"
            " RETURNING id",
            f"Test Contact {uuid.uuid4().hex[:8]}",
        )
        if posture is not None:
            await admin.execute(
                "UPDATE public.entities SET posture = $2 WHERE id = $1", entity_id, posture
            )
        for predicate, value in facts:
            await admin.execute(
                "INSERT INTO relationship.entity_facts"
                " (subject, predicate, object, object_kind, src)"
                " VALUES ($1, $2, $3, 'literal', 'test')",
                entity_id,
                predicate,
                value,
            )
    finally:
        await admin.close()
    return entity_id


@contextmanager
def _known_contact_patch(email: str = "user@example.com") -> Iterator[None]:
    """Context manager that patches identity resolution to return a known owner contact.

    Patches identity resolution to return a unique owner contact so the email
    guard auto-approves without a real DB hit.
    """
    contact = ResolvedContact(
        name="Test Contact",
        roles=["owner"],
        entity_id=uuid.UUID("00000000-0000-0000-0000-eeeeeeeeeeee"),
    )

    async def _mock_resolve(pool: Any, channel_type: str, channel_value: str) -> Any:
        return contact

    with (
        patch("butlers.identity.resolve_contact_by_channel", side_effect=_mock_resolve),
        patch(
            "butlers.identity.resolve_owner_channel_via_definer",
            new=AsyncMock(return_value=(contact, True)),
        ),
    ):
        yield


def _non_owner_contact() -> ResolvedContact:
    """Return a resolved non-owner target for notify approval-boundary tests."""
    return ResolvedContact(
        name="Test Contact",
        roles=["contact"],
        entity_id=uuid.UUID("00000000-0000-0000-0000-dddddddddddd"),
    )


def _make_mock_client(*, is_error: bool = False) -> Any:
    """Create a mock switchboard client."""
    mock_call_result = MagicMock()
    mock_call_result.is_error = is_error
    mock_call_result.data = {"status": "sent"}
    mock_call_result.content = [MagicMock(text='{"status":"sent"}')]

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=mock_call_result)
    return mock_client


class TestNotifyEntityIdResolution:
    """Tasks 7.1+7.2 — entity_id resolves to channel identifier via entity_facts."""

    async def test_entity_id_resolves_and_delivers(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """entity_id=UUID calls resolver; resolved identifier used in delivery payload;
        DB query uses entity_facts path; returns None when not found/error."""
        booted = await _relationship_butler(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)

        # entity_id → resolver called with correct args, result used in delivery
        entity_id = uuid.UUID("00000000-0000-0000-0000-000000000010")
        client = _use_client(monkeypatch, daemon, _make_mock_client())
        mock_resolver = AsyncMock(return_value="contact@example.com")
        with (
            patch.object(daemon, "_resolve_entity_channel_identifier", new=mock_resolver),
            _known_contact_patch("contact@example.com"),
        ):
            result = await notify_fn(channel="email", message="Test", entity_id=entity_id)
        assert result["status"] == "ok"
        # msg_context defaults to None when not provided
        mock_resolver.assert_awaited_once_with(
            entity_id=entity_id, channel="email", msg_context=None
        )
        delivery = client.call_tool.await_args.args[1]["notify_request"]["delivery"]
        assert delivery["recipient"] == "contact@example.com"

        # The resolver reads relationship.entity_facts keyed on the entity, with no
        # public.contacts or contact_info step: only an entity and its fact exist.
        eid = await _seed_entity(booted, [("has-email", "user@example.com")])
        result2 = await daemon._resolve_entity_channel_identifier(entity_id=eid, channel="email")
        assert result2 == "user@example.com"

        # Returns None when the entity has no matching fact
        assert (
            await daemon._resolve_entity_channel_identifier(
                entity_id=await _seed_entity(booted, []), channel="email"
            )
            is None
        )

    async def test_telegram_resolution_uses_handle_prefix_filter(
        self, booted_daemon, tmp_path: Path
    ) -> None:
        """Telegram channel resolution queries has-handle with 'telegram:' prefix filter.

        entity_facts stores telegram_user_id as has-handle with object 'telegram:NUMERIC_ID'.
        The resolver must filter by this prefix to disambiguate from linkedin/twitter handles,
        then strip the prefix and return the numeric delivery ID.
        """
        booted = await _relationship_butler(booted_daemon, tmp_path)
        # A linkedin handle sorts first; only the telegram:-prefixed one may resolve.
        entity_id = await _seed_entity(
            booted, [("has-handle", "linkedin:someone"), ("has-handle", "telegram:210454304")]
        )

        result = await booted.daemon._resolve_entity_channel_identifier(
            entity_id=entity_id, channel="telegram"
        )
        # Prefix stripped: "telegram:210454304" → "210454304"
        assert result == "210454304"

    async def test_telegram_no_has_handle_with_prefix_returns_none(
        self, booted_daemon, tmp_path: Path
    ) -> None:
        """Returns None if entity has no telegram: prefixed has-handle row.

        rel_019 normalised legacy telegram rows to the 'telegram:' prefix in
        production, so the daemon no longer falls back to an unprefixed all-numeric
        match (bu-3nu0x).  A contact with only linkedin/twitter handles (non-prefixed)
        must not be delivered to the wrong platform.
        """
        booted = await _relationship_butler(booted_daemon, tmp_path)
        entity_id = await _seed_entity(
            booted, [("has-handle", "someone-on-linkedin"), ("has-handle", "210454304")]
        )

        result = await booted.daemon._resolve_entity_channel_identifier(
            entity_id=entity_id, channel="telegram"
        )
        assert result is None

    async def test_email_resolution_uses_has_email_predicate(
        self, booted_daemon, tmp_path: Path
    ) -> None:
        """Email channel resolution queries has-email predicate; no prefix filter needed."""
        booted = await _relationship_butler(booted_daemon, tmp_path)
        entity_id = await _seed_entity(
            booted, [("has-handle", "telegram:1"), ("has-email", "someone@example.com")]
        )

        result = await booted.daemon._resolve_entity_channel_identifier(
            entity_id=entity_id, channel="email"
        )
        assert result == "someone@example.com"

    async def test_unknown_channel_returns_none(self, shared_booted_daemon) -> None:
        """Returns None immediately for channels with no known predicate mapping."""
        booted = await shared_booted_daemon()

        result = await booted.daemon._resolve_entity_channel_identifier(
            entity_id=uuid.UUID("00000000-0000-0000-0000-000000000033"),
            channel="fax",  # unmapped channel
        )
        assert result is None

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "bu-rvgzmf.13: non-Relationship butler roles have no USAGE on the "
            "relationship schema, so the resolver swallows the privilege error and "
            "notify(entity_id=...) always takes the missing-identifier path"
        ),
    )
    async def test_a_non_relationship_butler_resolves_an_entity_handle(
        self, booted_daemon, tmp_path: Path
    ) -> None:
        booted = await booted_daemon(
            write_butler_config(tmp_path, "general"),
            chain_schemas={"relationship": "relationship"},
        )
        entity_id = await _seed_entity(booted, [("has-email", "someone@example.com")])

        result = await booted.daemon._resolve_entity_channel_identifier(
            entity_id=entity_id, channel="email"
        )
        assert result == "someone@example.com"


class TestNotifyRecipientPosture:
    """bu-q7vx1q.8: memorial / no_contact recipients are refused before anything is queued."""

    @pytest.mark.parametrize(
        ("posture", "fetch_error", "refused"),
        [
            ("no_contact", None, True),
            ("memorial", None, True),
            ("quiet", None, False),
            ("active", None, False),
            (None, RuntimeError("posture read failed"), True),
        ],
    )
    async def test_posture_gate_precedes_resolution_parking_and_delivery(
        self,
        booted_daemon,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        posture: str | None,
        fetch_error: Exception | None,
        refused: bool,
    ) -> None:
        booted = await _fresh(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())
        parked = _ParkRecorder()
        monkeypatch.setattr("butlers.core.approvals_hooks.park_pending_action", parked)
        if fetch_error is not None:
            monkeypatch.setattr(
                "butlers.core_tools._notifications.fetch_entity_posture",
                AsyncMock(side_effect=fetch_error),
            )
            entity_id = uuid.UUID("00000000-0000-0000-0000-000000000041")
        else:
            entity_id = await _seed_entity(booted, [], posture=posture)
        resolver = AsyncMock(return_value="person@example.com")

        with patch.object(daemon, "_resolve_entity_channel_identifier", new=resolver):
            result = await notify_fn(channel="email", message="Hello", entity_id=entity_id)

        if not refused:
            resolver.assert_awaited_once()
            assert result.get("code") != "recipient_posture"
            return
        assert result["status"] == "error"
        assert result["code"] == "recipient_posture"
        assert "memorial" not in result["error"] and "no_contact" not in result["error"]
        resolver.assert_not_awaited()
        client.call_tool.assert_not_awaited()
        assert parked.calls == []
        ledger = await _ledger_rows(booted)
        assert len(ledger) == 1
        assert ledger[0]["outcome"] == "failed"
        assert ledger[0]["reason"] in {"recipient_posture", "recipient_posture_unreadable"}


class _ParkRecorder:
    """Records what notify() would park; the subject is the hand-off, not the park."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._error = error

    async def __call__(self, _pool: Any, **kwargs: Any) -> Any:
        if self._error is not None:
            raise self._error
        self.calls.append(kwargs)
        return SimpleNamespace(
            action_id=kwargs["action_id"], intent_id=None, duplicate=False, legacy_duplicate=True
        )


class TestNotifyMissingIdentifierAndOwner:
    """Tasks 7.3+7.4 — missing identifier parks; no entity_id uses owner resolution."""

    async def test_missing_identifier_fails_closed_without_approval_parking(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Finance-like no-approvals daemon must not fabricate a parked action.

        Finance exposes ``notify`` but deliberately has no ``[modules.approvals]``
        section.  Once approval hooks are scoped to their owning pools, a missing
        entity channel identifier therefore cannot be persisted in
        ``pending_actions``.  The caller must receive an honest failure instead
        of a generated action id for a row and owner push that do not exist.
        """
        import butlers.core.approvals_hooks as approval_hooks

        booted = await _fresh(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())

        # Keep the daemon in the same optional-approvals state as Finance rather
        # than inheriting another butler's scoped runtime.
        monkeypatch.setattr(approval_hooks, "_approval_hooks_by_pool", {})
        entity_id = uuid.UUID("00000000-0000-0000-0000-000000000031")

        with (
            patch.object(
                daemon, "_resolve_entity_channel_identifier", new=AsyncMock(return_value=None)
            ),
            patch(
                "butlers.core.owner.fetch_owner_entity_id",
                new=AsyncMock(
                    side_effect=lambda _pool, **kwargs: SimpleNamespace(
                        action_id=kwargs["action_id"]
                    )
                ),
            ),
        ):
            result = await notify_fn(
                channel="email",
                message="Hello contact",
                entity_id=entity_id,
                _why="The contact needs this delivery after their channel is configured.",
                _evidence=[],
            )

        assert result["status"] == "error"
        assert result["retryable"] is False
        assert "approval parking is unavailable" in result["error"].lower()
        assert "pending_action_id" not in result
        # No approvals module: the butler has no pending_actions table to write.
        assert await daemon.db.pool.fetchval("SELECT to_regclass('pending_actions')") is None
        client.call_tool.assert_not_awaited()

        failed_ledger_rows = await _ledger_rows(booted)
        assert len(failed_ledger_rows) == 1
        assert failed_ledger_rows[0]["outcome"] == "failed"
        assert failed_ledger_rows[0]["reason"] == "approval_parking_unavailable"
        metadata = json.loads(failed_ledger_rows[0]["metadata"])
        assert metadata == {"entity_id": str(entity_id), "retryable": False}
        assert "Hello contact" not in failed_ledger_rows[0]["metadata"]

    async def test_missing_identifier_parks_and_owner_fallback(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing identifier -> pending_missing_identifier, parked through the shared
        park_pending_action choke point (bu-mda0r) so the owner push is attempted
        exactly the way every other park path attempts it; no entity_id/recipient ->
        owner default resolver called; entity_id wins over recipient.

        The push mechanism itself (whether the owner is actually reachable, quiet
        hours, burst digest, callback-secret handling) is real-Postgres-backed
        coverage owned by tests/integration/test_approval_push_on_park.py; this
        test only proves notify()'s missing-identifier path hands off to that
        one shared helper instead of building its own ad hoc owner alert.
        """
        booted = await _fresh(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        entity_id = uuid.UUID("00000000-0000-0000-0000-000000000030")
        push_runtime = object()  # opaque sentinel; identity-checked below
        monkeypatch.setattr(daemon, "_approval_push_runtime", push_runtime)

        with (
            patch.object(
                daemon, "_resolve_entity_channel_identifier", new=AsyncMock(return_value=None)
            ),
            patch(
                # notify() reaches the park choke point via the core_tools ->
                # core.approvals_hooks indirection (core_tools must never
                # import modules.* directly; bu-mda0r).
                "butlers.core.approvals_hooks.park_pending_action",
                new=AsyncMock(
                    side_effect=lambda _pool, **kwargs: SimpleNamespace(
                        action_id=kwargs["action_id"]
                    )
                ),
            ) as mock_park,
            patch(
                "butlers.core.approvals_hooks.is_approval_parking_available",
                return_value=True,
            ),
        ):
            result = await notify_fn(
                channel="email",
                message="Hello contact",
                entity_id=entity_id,
                _why="The contact needs this delivery after their channel is configured.",
                _evidence=[],
            )
        assert result["status"] == "pending_missing_identifier"
        assert result["entity_id"] == str(entity_id)
        mock_park.assert_awaited_once()
        park_kwargs = mock_park.await_args.kwargs
        assert park_kwargs["tool_name"] == "notify"
        assert park_kwargs["origin_butler"] == daemon.config.name
        assert park_kwargs["approval_push_runtime"] is push_runtime
        assert park_kwargs["tool_args"]["entity_id"] == str(entity_id)

    async def test_missing_identifier_reports_atomic_parking_failure(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed admission never fabricates a pending_missing_identifier row."""
        booted = await _fresh(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        entity_id = uuid.UUID("00000000-0000-0000-0000-000000000032")
        with (
            patch.object(
                daemon, "_resolve_entity_channel_identifier", new=AsyncMock(return_value=None)
            ),
            patch(
                "butlers.core.approvals_hooks.park_pending_action",
                new=AsyncMock(side_effect=RuntimeError("intent insert failed")),
            ),
            patch(
                "butlers.core.approvals_hooks.is_approval_parking_available",
                return_value=True,
            ),
        ):
            result = await notify_fn(
                channel="email",
                message="Hello contact",
                entity_id=entity_id,
                _why="The contact needs this delivery after configuration.",
                _evidence=[],
            )
        assert result["status"] == "error"
        assert result["retryable"] is False
        assert "no pending action was created" in result["error"].lower()
        assert "pending_action_id" not in result

        # No entity_id → default owner resolver called
        client = _use_client(monkeypatch, daemon, _make_mock_client())
        mock_default = AsyncMock(return_value="owner@example.com")
        mock_contact = AsyncMock(return_value="ignored")
        with (
            patch.object(daemon, "_resolve_default_notify_recipient", new=mock_default),
            patch.object(daemon, "_resolve_entity_channel_identifier", new=mock_contact),
            _known_contact_patch(),
        ):
            r3 = await notify_fn(channel="email", message="Hello owner")
        assert r3["status"] == "ok"
        mock_default.assert_awaited_once()
        mock_contact.assert_not_awaited()

        # entity_id wins over explicit recipient
        client = _use_client(monkeypatch, daemon, _make_mock_client())
        with (
            patch.object(
                daemon,
                "_resolve_entity_channel_identifier",
                new=AsyncMock(return_value="contact-resolved@example.com"),
            ),
            _known_contact_patch("contact-resolved@example.com"),
        ):
            r4 = await notify_fn(
                channel="email",
                message="Hello",
                entity_id=uuid.UUID("00000000-0000-0000-0000-000000000040"),
                recipient="explicit@example.com",
            )
        assert r4["status"] == "ok"
        delivery = client.call_tool.await_args.args[1]["notify_request"]["delivery"]
        assert delivery["recipient"] == "contact-resolved@example.com"


@pytest.fixture
def approval_hooks(monkeypatch: pytest.MonkeyPatch):
    """Register the real recipient guards on a booted pool, parking into a recorder.

    ``recorder = approval_hooks(booted)``; the hooks are removed at teardown.
    """
    import butlers.core.approvals_hooks as _hooks
    from butlers.modules.approvals.email_guard import (
        check_email_recipient,
        check_recipient,
    )

    registrations = []

    def register(booted, recorder: _ParkRecorder | None = None) -> _ParkRecorder:
        recorder = recorder or _ParkRecorder()
        monkeypatch.setattr("butlers.modules.approvals.email_guard.park_pending_action", recorder)
        pool = booted.daemon.db.pool
        runtime = _hooks.register_approval_hooks(
            pool,
            email_guard=check_email_recipient,
            recipient_guard=check_recipient,
            park_pending_action=recorder,
        )
        registrations.append((pool, runtime))
        return recorder

    yield register
    for pool, runtime in reversed(registrations):
        _hooks.unregister_approval_hooks(pool, runtime)


class TestNotifyEmailRecipientValidation:
    """Email recipients must be known contacts; entity_id path also validated."""

    async def test_email_validation(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approval_hooks
    ) -> None:
        """Unknown email → pending_approval; known sent; telegram skips; entity_id path validates."""
        booted = await _fresh(booted_daemon, tmp_path)
        daemon = booted.daemon
        approval_hooks(booted)
        notify_fn = await _notify_fn(booted)

        # Unknown email → parked as pending_approval
        _use_client(monkeypatch, daemon, _make_mock_client())
        with patch("butlers.identity.resolve_contact_by_channel", new=AsyncMock(return_value=None)):
            result = await notify_fn(
                channel="email",
                message="Hello stranger",
                recipient="hallucinated@example.com",
                _why="The recipient asked for this update.",
                _evidence=[],
            )
        assert result["status"] == "pending_approval"

        # Known email → delivered
        _use_client(monkeypatch, daemon, _make_mock_client())
        with _known_contact_patch():
            result2 = await notify_fn(
                channel="email", message="Hello known", recipient="known@example.com"
            )
        assert result2["status"] == "ok"

        # entity_id path still validates email
        _use_client(monkeypatch, daemon, _make_mock_client())
        with (
            patch.object(
                daemon,
                "_resolve_entity_channel_identifier",
                new=AsyncMock(return_value="contact-email@example.com"),
            ),
            patch("butlers.identity.resolve_contact_by_channel", new=AsyncMock(return_value=None)),
        ):
            result3 = await notify_fn(
                channel="email",
                message="Hello via entity_id",
                entity_id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
                _why="The recipient asked for this update.",
                _evidence=[],
            )
        assert result3["status"] == "pending_approval"

    async def test_email_admission_failure_is_not_reported_as_pending(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approval_hooks
    ) -> None:
        """The email guard's parking failure remains an explicit notify error."""
        booted = await _fresh(booted_daemon, tmp_path)
        daemon = booted.daemon
        approval_hooks(booted, _ParkRecorder(error=RuntimeError("intent insert failed")))
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())
        with patch(
            "butlers.identity.resolve_contact_by_channel",
            new=AsyncMock(return_value=None),
        ):
            result = await notify_fn(
                channel="email",
                message="Hello stranger",
                recipient="hallucinated@example.com",
                _why="The recipient requested this update.",
                _evidence=[],
            )
        assert result["status"] == "error"
        assert result["retryable"] is False
        assert "no pending action was created" in result["error"].lower()
        assert "pending_action_id" not in result
        client.call_tool.assert_not_awaited()


class TestNotifyDecisionDossierBoundary:
    """notify() must apply the dossier contract before non-owner park/rule paths."""

    async def test_owner_target_remains_exempt_from_required_why(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approval_hooks
    ) -> None:
        """A resolved owner recipient still delivers without a decision dossier."""
        booted = await _fresh(booted_daemon, tmp_path)
        parked = approval_hooks(booted)
        notify_fn = await _notify_fn(booted)
        _use_client(monkeypatch, booted.daemon, _make_mock_client())

        with _known_contact_patch():
            result = await notify_fn(
                channel="telegram",
                message="Owner-only status update",
                recipient="12345",
            )

        assert result["status"] == "ok"
        assert parked.calls == []

    async def test_owner_missing_identifier_remains_exempt_from_required_why(
        self, shared_booted_daemon, monkeypatch: pytest.MonkeyPatch, approval_hooks
    ) -> None:
        """An owner entity still parks for its missing channel without requiring why."""
        entity_id = uuid.UUID("00000000-0000-0000-0000-aaaaaaaaaaaa")
        booted = await shared_booted_daemon()
        daemon = booted.daemon
        parked = approval_hooks(booted)
        notify_fn = await _notify_fn(booted)
        _use_client(monkeypatch, daemon, _make_mock_client())

        with (
            patch.object(
                daemon,
                "_resolve_entity_channel_identifier",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                daemon,
                "_resolve_default_notify_recipient",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.core.owner.fetch_owner_entity_id",
                new=AsyncMock(return_value=entity_id),
            ),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Owner delivery awaiting channel configuration",
                entity_id=entity_id,
            )

        assert result["status"] == "pending_missing_identifier"
        (park,) = parked.calls
        assert (park["why"], park["evidence"], park["blast_radius"], park["reversibility"]) == (
            None,
            [],
            None,
            None,
        )

    @pytest.mark.parametrize(
        ("dossier_kwargs", "expected_code"),
        [
            ({}, "missing_required_dossier_field"),
            ({"_why": "   "}, "invalid_dossier_value"),
        ],
    )
    async def test_non_owner_invalid_why_retries_without_rule_or_persistence(
        self,
        shared_booted_daemon,
        monkeypatch: pytest.MonkeyPatch,
        approval_hooks,
        dossier_kwargs: dict[str, Any],
        expected_code: str,
    ) -> None:
        """Missing or malformed why stops notify() before any approval side effect."""
        booted = await shared_booted_daemon()
        parked = approval_hooks(booted)
        notify_fn = await _notify_fn(booted)
        _use_client(monkeypatch, booted.daemon, _make_mock_client())
        match_rules = AsyncMock(side_effect=AssertionError("rules must not be queried"))

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=_non_owner_contact()),
            ),
            patch("butlers.modules.approvals.rules.match_rules", new=match_rules),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Non-owner update",
                recipient="900800700",
                **dossier_kwargs,
            )

        assert result["status"] == "error"
        assert result["retryable"] is True
        assert result["error"] == {
            "code": expected_code,
            "field": "why",
            "message": (
                "why is required for a gated non-owner action; retry with _why."
                if expected_code == "missing_required_dossier_field"
                else "why must be a non-empty human-readable string."
            ),
            "retryable": True,
        }
        match_rules.assert_not_awaited()
        assert parked.calls == []

    async def test_quiet_hours_reject_non_owner_missing_why_before_deferral(
        self, shared_booted_daemon, approval_hooks
    ) -> None:
        """Quiet-hours storage cannot bypass the non-owner dossier boundary."""
        booted = await shared_booted_daemon()
        parked = approval_hooks(booted)
        notify_fn = await _notify_fn(booted)
        deferred_insert = AsyncMock(return_value=uuid.uuid4())
        match_rules = AsyncMock(side_effect=AssertionError("rules must not be queried"))

        with (
            patch(
                "butlers.core.temporal.delivery_db.get_delivery_preferences",
                new=AsyncMock(return_value={"timezone": "UTC"}),
            ),
            patch(
                "butlers.core.temporal.delivery.should_defer_notification",
                return_value=True,
            ),
            patch(
                "butlers.core.temporal.delivery.compute_deliver_at",
                return_value=datetime.now(UTC),
            ),
            patch(
                "butlers.core.temporal.delivery_db.insert_deferred_notification",
                new=deferred_insert,
            ),
            patch(
                "butlers.core_tools._notifications.record_attention_event",
                new=AsyncMock(),
            ),
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=_non_owner_contact()),
            ),
            patch("butlers.modules.approvals.rules.match_rules", new=match_rules),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Non-owner update",
                recipient="900800700",
                priority="medium",
            )

        assert result["status"] == "error"
        assert result["error"]["code"] == "missing_required_dossier_field"
        assert result["retryable"] is True
        deferred_insert.assert_not_awaited()
        match_rules.assert_not_awaited()
        assert parked.calls == []

    async def test_quiet_hours_preserves_valid_dossier_in_deferred_envelope(
        self, shared_booted_daemon, approval_hooks
    ) -> None:
        """A valid non-owner dossier survives a delayed route.execute delivery."""
        booted = await shared_booted_daemon()
        approval_hooks(booted)
        notify_fn = await _notify_fn(booted)
        deferred_insert = AsyncMock(return_value=uuid.uuid4())
        evidence = [
            {
                "type": "text",
                "ref": "request-900800700",
                "note": "The recipient asked for this update.",
            }
        ]
        standing_rule = MagicMock(id=uuid.uuid4())
        match_rules = AsyncMock(return_value=standing_rule)
        claim_rule_use = AsyncMock(return_value=True)

        with (
            patch(
                "butlers.core.temporal.delivery_db.get_delivery_preferences",
                new=AsyncMock(return_value={"timezone": "UTC"}),
            ),
            patch(
                "butlers.core.temporal.delivery.should_defer_notification",
                return_value=True,
            ),
            patch(
                "butlers.core.temporal.delivery.compute_deliver_at",
                return_value=datetime.now(UTC),
            ),
            patch(
                "butlers.core.temporal.delivery_db.insert_deferred_notification",
                new=deferred_insert,
            ),
            patch(
                "butlers.core_tools._notifications.record_attention_event",
                new=AsyncMock(),
            ),
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=_non_owner_contact()),
            ),
            patch("butlers.modules.approvals.rules.match_rules", new=match_rules),
            patch(
                "butlers.modules.approvals.rules.claim_rule_use",
                new=claim_rule_use,
            ),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Non-owner update",
                recipient="900800700",
                priority="medium",
                _why="The recipient asked for this update.",
                _evidence=evidence,
                _blast_radius="contact",
                _reversibility="compensable",
            )

        assert result["status"] == "deferred"
        match_rules.assert_awaited_once()
        claim_rule_use.assert_awaited_once()
        envelope = deferred_insert.await_args.kwargs["envelope"]
        assert envelope["delivery"]["recipient"] == "900800700"
        assert envelope["decision_dossier"] == {
            "why": "The recipient asked for this update.",
            "evidence": evidence,
            "blast_radius": "contact",
            "reversibility": "compensable",
        }

    async def test_non_owner_missing_identifier_requires_dossier_before_park(
        self, shared_booted_daemon, monkeypatch: pytest.MonkeyPatch, approval_hooks
    ) -> None:
        """A missing channel identifier cannot bypass the non-owner dossier gate."""
        booted = await shared_booted_daemon()
        daemon = booted.daemon
        parked = approval_hooks(booted)
        notify_fn = await _notify_fn(booted)
        _use_client(monkeypatch, daemon, _make_mock_client())

        with patch.object(
            daemon,
            "_resolve_entity_channel_identifier",
            new=AsyncMock(return_value=None),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Awaiting a target channel",
                entity_id=uuid.UUID("00000000-0000-0000-0000-bbbbbbbbbbbb"),
            )

        assert result["status"] == "error"
        assert result["error"]["code"] == "missing_required_dossier_field"
        assert parked.calls == []

    async def test_non_owner_valid_dossier_is_persisted_on_park(
        self, shared_booted_daemon, monkeypatch: pytest.MonkeyPatch, approval_hooks
    ) -> None:
        """A typed dossier survives notify()'s recipient guard into the parked action."""
        booted = await shared_booted_daemon()
        parked = approval_hooks(booted)
        notify_fn = await _notify_fn(booted)
        _use_client(monkeypatch, booted.daemon, _make_mock_client())
        evidence = [
            {
                "type": "text",
                "ref": "request-900800700",
                "note": "The recipient asked for this update.",
            }
        ]

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=_non_owner_contact()),
            ),
            patch("butlers.modules.approvals.rules.match_rules", new=AsyncMock(return_value=None)),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Non-owner update",
                recipient="900800700",
                _why="The recipient asked for this update.",
                _evidence=evidence,
                _blast_radius="contact",
                _reversibility="compensable",
            )

        assert result["status"] == "pending_approval"
        (park,) = parked.calls
        assert (park["why"], park["evidence"], park["blast_radius"], park["reversibility"]) == (
            "The recipient asked for this update.",
            evidence,
            "contact",
            "compensable",
        )


class TestNotifyChannelResolution:
    """Group 2 (bu-upbit) — optional channel + preferred-channel resolution wiring.

    notify() resolves the outbound channel when the caller omits it:
      - forced channel always wins (preference resolver is NOT consulted)
      - omitted + entity_id → resolve_outbound_channel decides the channel
      - omitted + no entity_id → telegram default (back-compat)
    """

    async def test_forced_channel_wins_resolver_not_consulted(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit channel arg bypasses preferred-channel resolution entirely."""
        booted = await _fresh(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())

        entity_id = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
        mock_resolve = AsyncMock(return_value="email")  # would pick email if asked
        with (
            patch("butlers.identity.resolve_outbound_channel", new=mock_resolve),
            patch.object(
                daemon,
                "_resolve_entity_channel_identifier",
                new=AsyncMock(return_value="210454304"),
            ),
        ):
            result = await notify_fn(channel="telegram", message="Forced", entity_id=entity_id)
        assert result["status"] == "ok"
        # Forced channel → resolver never called.
        mock_resolve.assert_not_awaited()
        delivery = client.call_tool.await_args.args[1]["notify_request"]["delivery"]
        assert delivery["channel"] == "telegram"

    async def test_omitted_channel_with_entity_id_resolves_preference(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """channel=None + entity_id → resolve_outbound_channel picks the channel."""
        booted = await _fresh(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())

        entity_id = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
        mock_resolve = AsyncMock(return_value="telegram")
        mock_identifier = AsyncMock(return_value="210454304")
        with (
            patch("butlers.identity.resolve_outbound_channel", new=mock_resolve),
            patch.object(daemon, "_resolve_entity_channel_identifier", new=mock_identifier),
        ):
            result = await notify_fn(message="Hi", entity_id=entity_id)
        assert result["status"] == "ok"
        mock_resolve.assert_awaited_once()
        # Resolver called with the contact and notify's deliverable set.
        _, kwargs = mock_resolve.await_args
        assert mock_resolve.await_args.args[1] == entity_id
        assert kwargs["deliverable_channels"] == {"telegram", "email"}
        delivery = client.call_tool.await_args.args[1]["notify_request"]["delivery"]
        assert delivery["channel"] == "telegram"
        # The contact-channel identifier resolver was driven by the chosen channel.
        mock_identifier.assert_awaited_once_with(
            entity_id=entity_id, channel="telegram", msg_context=None
        )

    async def test_omitted_channel_no_entity_id_defaults_telegram(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """channel=None + no entity_id → telegram default; resolver not consulted."""
        booted = await _fresh(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())

        mock_resolve = AsyncMock(return_value="email")
        with (
            patch("butlers.identity.resolve_outbound_channel", new=mock_resolve),
            patch.object(
                daemon,
                "_resolve_default_notify_recipient",
                new=AsyncMock(return_value="210454304"),
            ),
        ):
            result = await notify_fn(message="Owner ping")
        assert result["status"] == "ok"
        # No entity_id → preference resolution is not attempted.
        mock_resolve.assert_not_awaited()
        delivery = client.call_tool.await_args.args[1]["notify_request"]["delivery"]
        assert delivery["channel"] == "telegram"

    async def test_resolver_none_falls_back_to_telegram_default(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """channel=None + entity_id but resolver returns None → telegram default."""
        booted = await _fresh(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())

        entity_id = uuid.UUID("00000000-0000-0000-0000-0000000000a3")
        with (
            patch(
                "butlers.identity.resolve_outbound_channel",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                daemon,
                "_resolve_entity_channel_identifier",
                new=AsyncMock(return_value="210454304"),
            ),
        ):
            result = await notify_fn(message="Hi", entity_id=entity_id)
        assert result["status"] == "ok"
        delivery = client.call_tool.await_args.args[1]["notify_request"]["delivery"]
        assert delivery["channel"] == "telegram"
