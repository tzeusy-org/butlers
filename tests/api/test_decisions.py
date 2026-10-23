"""Tests for GET /api/decisions (bu-ckkpz.2, Dashboard Decisions lane).

The endpoint is a thin read-only wrapper around
``butlers.jobs.decision_review.compute_decision_digest`` (bu-ckkpz.4) -- no
database pool involved, so these tests drive it purely through the beads
export JSONL fixture, mirroring ``tests/jobs/test_decision_review.py``'s own
fixture helpers.

Covers:
- Degraded envelope: a missing/stale/unreadable export never renders a
  fabricated all-clear -- `data: []` + `meta.decisions_available: false`.
- A genuine zero (export readable, no decision-marked beads) is a real
  all-clear: `data: []` + `meta.decisions_available: true`.
- Open decision beads serialize oldest-first with `age_hours` derived from
  `created_at`.
- A decision blocking a P1 bug/deploy for >48h carries the escalation fields;
  a non-escalated decision does not.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from butlers.api.deps import get_db

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _write_export(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _decision(id_, *, title="DECISION REQUIRED (owner): pick one", created_days_ago=10, **kw):
    # bu-uo37y: the runtime title-marker fallback is retired, so a decision bead
    # is classified solely by the `decision` label. Default fixtures to a labeled
    # decision; pass labels=[] via **kw for an intentionally unlabeled bead.
    return {
        "id": id_,
        "title": title,
        "status": "open",
        "priority": 1,
        "issue_type": "task",
        "created_at": _iso(_NOW - timedelta(days=created_days_ago)),
        "dependencies": [],
        "labels": ["decision"],
        **kw,
    }


def _blocker(id_, *, title, issue_type, priority, blocks_id, edge_age_hours, status="open"):
    return {
        "id": id_,
        "title": title,
        "status": status,
        "priority": priority,
        "issue_type": issue_type,
        "created_at": _iso(_NOW - timedelta(days=30)),
        "dependencies": [
            {
                "issue_id": id_,
                "depends_on_id": blocks_id,
                "type": "blocks",
                "created_at": _iso(_NOW - timedelta(hours=edge_age_hours)),
            }
        ],
    }


async def _get_decisions(app, export_path: Path):
    """GET /api/decisions with the digest pinned to a fixture export + fixed clock.

    Patches the router's own ``compute_decision_digest`` binding (not the
    live-clock default) so the endpoint's decision detection / escalation
    logic is exercised for real -- only the export path and "now" are fixed.
    """
    from butlers.jobs.decision_review import compute_decision_digest as real_compute

    with patch("butlers.api.routers.decisions.compute_decision_digest") as mock_digest:
        mock_digest.side_effect = lambda: real_compute(export_path, now=_NOW)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get("/api/decisions")


# ---------------------------------------------------------------------------
# Degraded envelope -- never a fabricated all-clear
# ---------------------------------------------------------------------------


async def test_list_decisions_flags_unavailable_when_export_missing(app, tmp_path):
    resp = await _get_decisions(app, tmp_path / "does-not-exist.jsonl")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["decisions_available"] is False
    assert body["meta"]["unavailable_reason"] == "export_missing"
    # bu-hmdqz.6: no file was ever stat'd, so there is no known age to report.
    assert body["meta"].get("export_as_of") is None


async def test_list_decisions_preserves_export_as_of_when_export_is_unparseable(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    export.write_text("{not valid json\n")
    export_mtime = _NOW.timestamp()
    os.utime(export, (export_mtime, export_mtime))

    resp = await _get_decisions(app, export)

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["decisions_available"] is False
    assert body["meta"]["unavailable_reason"].startswith("export_read_error:")
    export_as_of = datetime.fromisoformat(body["meta"]["export_as_of"].replace("Z", "+00:00"))
    assert export_as_of == _NOW


async def test_list_decisions_genuine_zero_is_available(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [{"id": "bu-x", "title": "Ordinary task", "status": "open"}])

    resp = await _get_decisions(app, export)

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["decisions_available"] is True
    # bu-hmdqz.6: meta.export_as_of carries the export file's own mtime so
    # the frontend can render an honest "as of" plaque.
    assert body["meta"].get("export_as_of") is not None


# ---------------------------------------------------------------------------
# Open decisions -- oldest first, age_hours derived
# ---------------------------------------------------------------------------


async def test_list_decisions_returns_oldest_first_with_age_hours(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-newer", title="DECISION REQUIRED (owner): newer", created_days_ago=2),
            _decision("bu-older", title="DECISION REQUIRED (owner): older", created_days_ago=10),
        ],
    )

    resp = await _get_decisions(app, export)

    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["decisions_available"] is True
    ids = [row["id"] for row in body["data"]]
    assert ids == ["bu-older", "bu-newer"]
    # 10 days ago -> 240h
    assert body["data"][0]["age_hours"] == pytest.approx(240, abs=1)
    assert body["data"][0]["escalated"] is False
    assert body["data"][0]["escalated_blocked_id"] is None


async def test_list_decisions_projects_valid_structured_context(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    due_at = _NOW + timedelta(days=3)
    _write_export(
        export,
        [
            _decision(
                "bu-context",
                description="Pick the owner-visible recovery posture.",
                metadata={
                    "decision": {
                        "options": ["Keep paused", "Resume safely"],
                        "default": "Keep paused",
                    }
                },
                due_at=_iso(due_at),
            )
        ],
    )

    resp = await _get_decisions(app, export)

    assert resp.status_code == 200
    row = resp.json()["data"][0]
    assert row["description"] == "Pick the owner-visible recovery posture."
    assert row["options"] == ["Keep paused", "Resume safely"]
    assert row["default"] == "Keep paused"
    assert datetime.fromisoformat(row["due_at"].replace("Z", "+00:00")) == due_at
    assert row["structured_details_available"] is True
    assert row["structured_details_unavailable_reason"] is None


async def test_list_decisions_names_malformed_structured_metadata(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    due_at = _NOW + timedelta(days=3)
    _write_export(
        export,
        [
            _decision(
                "bu-malformed",
                description="The deadline is still source evidence.",
                metadata={"decision": {"options": ["Same", "Same"], "default": "Same"}},
                due_at=_iso(due_at),
            )
        ],
    )

    resp = await _get_decisions(app, export)

    assert resp.status_code == 200
    row = resp.json()["data"][0]
    assert row["description"] == "The deadline is still source evidence."
    assert datetime.fromisoformat(row["due_at"].replace("Z", "+00:00")) == due_at
    assert row["options"] is None
    assert row["default"] is None
    assert row["structured_details_available"] is False
    assert row["structured_details_unavailable_reason"] == "metadata_malformed"


# ---------------------------------------------------------------------------
# Escalation fields
# ---------------------------------------------------------------------------


async def test_list_decisions_carries_escalation_fields_past_48h(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-v4ipc", created_days_ago=10),
            _blocker(
                "bu-wzbu9",
                title="Silent message loss",
                issue_type="bug",
                priority=1,
                blocks_id="bu-v4ipc",
                edge_age_hours=72,
            ),
        ],
    )

    resp = await _get_decisions(app, export)

    assert resp.status_code == 200
    body = resp.json()
    row = body["data"][0]
    assert row["id"] == "bu-v4ipc"
    assert row["escalated"] is True
    assert row["escalated_blocked_id"] == "bu-wzbu9"
    assert row["escalated_blocked_title"] == "Silent message loss"
    assert row["escalated_blocked_kind"] == "p1_bug"
    assert row["escalated_block_hours"] == pytest.approx(72, abs=1)


async def test_list_decisions_not_escalated_under_48h_threshold(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-v4ipc", created_days_ago=10),
            _blocker(
                "bu-wzbu9",
                title="Silent message loss",
                issue_type="bug",
                priority=1,
                blocks_id="bu-v4ipc",
                edge_age_hours=10,
            ),
        ],
    )

    resp = await _get_decisions(app, export)

    assert resp.status_code == 200
    row = resp.json()["data"][0]
    assert row["escalated"] is False
    assert row["escalated_blocked_id"] is None


# ---------------------------------------------------------------------------
# Recorded intents (bu-ckkpz.3) -- best-effort, never degrading the digest
# ---------------------------------------------------------------------------


def _intent_row(bead_id: str, **overrides):
    from butlers.core.decision_desk import DecisionIntent

    fields = {
        "id": uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        "bead_id": bead_id,
        "option": "Keep paused",
        "source": "telegram",
        "actor": "owner@telegram",
        "prompt_id": None,
        "status": "failed",
        "attempts": 1,
        "failure_reason": "bead_not_open",
        "last_error": None,
        "created_at": _NOW,
        "claimed_at": _NOW,
        "finished_at": _NOW,
        "updated_at": _NOW,
        **overrides,
    }
    return DecisionIntent(**fields)


class _SwitchboardOnly:
    def pool(self, butler_name: str):
        assert butler_name == "switchboard"
        return object()


async def test_list_decisions_attaches_recorded_intents(app, tmp_path):
    from butlers.api.routers import decisions as decisions_router

    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [_decision("bu-a"), _decision("bu-b", created_days_ago=2)])
    app.dependency_overrides[get_db] = _SwitchboardOnly
    latest = AsyncMock(return_value={"bu-a": _intent_row("bu-a")})

    with patch.object(decisions_router, "latest_intents", latest):
        resp = await _get_decisions(app, export)

    assert resp.status_code == 200
    body = resp.json()
    assert "sources_degraded" not in body["meta"]
    assert latest.await_args.args[1] == ["bu-a", "bu-b"]
    first, second = body["data"]
    assert first["intent"]["status"] == "failed"
    assert first["intent"]["failure_reason"] == "bead_not_open"
    assert first["intent"]["option"] == "Keep paused"
    assert second["intent"] is None


async def test_list_decisions_degrades_only_the_intent_field(app, tmp_path):
    from butlers.api.routers import decisions as decisions_router

    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [_decision("bu-a")])
    app.dependency_overrides[get_db] = _SwitchboardOnly

    with patch.object(
        decisions_router, "latest_intents", AsyncMock(side_effect=OSError("db down"))
    ):
        resp = await _get_decisions(app, export)

    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["decisions_available"] is True
    assert body["meta"]["sources_degraded"] == ["decision_intents"]
    assert [row["id"] for row in body["data"]] == ["bu-a"]
    assert body["data"][0]["intent"] is None


@pytest.mark.parametrize(
    ("reason", "kind", "status"),
    [
        ("decisions_unavailable", "unavailable", 503),
        ("option_not_offered", "invalid", 422),
        ("intent_conflict:applied", "conflict", 409),
    ],
)
async def test_record_intent_maps_refusals_to_status_codes(app, reason, kind, status):
    from butlers.api.routers import decisions as decisions_router
    from butlers.core.decision_desk import DecisionIntentError

    app.dependency_overrides[get_db] = _SwitchboardOnly
    refusal = AsyncMock(side_effect=DecisionIntentError(reason, kind=kind))
    with (
        patch.object(decisions_router, "record_decision_intent", refusal),
        patch.object(decisions_router, "compute_decision_digest"),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/decisions/bu-a/intent", json={"option": "Hold"})

    assert resp.status_code == status
    assert resp.json()["detail"] == reason
    assert refusal.await_args.kwargs["source"] == "dashboard"
    assert refusal.await_args.kwargs["actor"] == "owner@dashboard"


async def test_record_intent_without_switchboard_db_is_503(app):

    class _NoSwitchboard:
        def pool(self, butler_name: str):
            raise KeyError(butler_name)

    app.dependency_overrides[get_db] = _NoSwitchboard
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/decisions/bu-a/intent", json={"option": "Hold"})

    assert resp.status_code == 503
    assert resp.json()["detail"] == "decision_intents_unavailable"


async def test_digest_forwards_reopening_evidence_and_projects_superseded_history(app, tmp_path):
    from butlers.api.routers import decisions as decisions_router

    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [_decision("bu-a", updated_at=_iso(_NOW))])
    app.dependency_overrides[get_db] = _SwitchboardOnly
    latest = AsyncMock(
        return_value={"bu-a": _intent_row("bu-a", status="superseded", failure_reason=None)}
    )
    with patch.object(decisions_router, "latest_intents", latest):
        response = await _get_decisions(app, export)

    assert response.status_code == 200
    assert response.json()["data"][0]["intent"]["status"] == "superseded"
    digest = latest.await_args.kwargs["digest"]
    assert digest.available and digest.open_decisions[0].id == "bu-a"
    assert digest.open_decisions[0].updated_at == _NOW
    assert digest.export_as_of is not None


# The persistence boundary is synthetic; routing, OwnerAuth and HMAC execute.
_CALLBACK_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")
_CALLBACK_SECRET = "synthetic-decision-signing-private"
_CALLBACK_CREDENTIAL = "synthetic-decision-connector-private"


def _callback_token(index=0, *, prompt_id=_CALLBACK_ID, created_at=_NOW, secret=_CALLBACK_SECRET):
    from butlers.core.decision_callbacks import mint_decision_callback_token

    return mint_decision_callback_token(
        prompt_id=prompt_id, option_index=index, created_at=created_at, secret=secret
    )


def _callback_app(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from butlers.api.app import create_app
    from butlers.api.routers import decisions
    from butlers.core import decision_desk
    from butlers.credential_store import CredentialStore
    from tests.api.auth_helpers import _DomainOwnerState
    from tests.decision_desk_helpers import make_bead, make_digest

    @asynccontextmanager
    async def transaction():
        yield

    row = dict(
        id=_CALLBACK_ID,
        bead_id="bu-test1",
        options=["Ship it", "Hold"],
        default_option="Hold",
        created_at=_NOW,
        delivery_outcome="delivered",
        delivered_at=_NOW,
    )
    connection = SimpleNamespace(fetchrow=AsyncMock(return_value=row), transaction=transaction)
    credential_row = AsyncMock(return_value={"secret_value": _CALLBACK_SECRET})

    @asynccontextmanager
    async def shared_acquire():
        yield SimpleNamespace(fetchrow=credential_row)

    shared = SimpleNamespace(acquire=shared_acquire)
    db = SimpleNamespace(pool=lambda name: connection, credential_shared_pool=lambda: shared)
    app = create_app(api_key="synthetic-owner-key")
    app.state.owner_auth_service = _DomainOwnerState("synthetic-owner-key")
    app.state.approval_callback_connector_token = _CALLBACK_CREDENTIAL
    app.dependency_overrides[get_db] = lambda: db
    resolve = AsyncMock()
    real_resolve = CredentialStore.resolve

    async def observed_resolve(store, key, *, env_fallback=False):
        assert store.pool is shared
        await resolve(key, env_fallback=env_fallback)
        return await real_resolve(store, key, env_fallback=env_fallback)

    monkeypatch.setattr(CredentialStore, "resolve", observed_resolve)
    monkeypatch.setattr(decisions, "compute_decision_digest", lambda: make_digest(make_bead()))
    reconcile = AsyncMock()
    recorded = AsyncMock(
        return_value=decision_desk.RecordResult(
            _intent_row(
                "bu-test1",
                option="Ship it",
                status="pending",
                failure_reason=None,
                actor="owner@telegram",
                prompt_id=_CALLBACK_ID,
            ),
            True,
        )
    )
    monkeypatch.setattr(decision_desk, "_reconcile_reopened", reconcile)
    monkeypatch.setattr(decision_desk, "_record_locked", recorded)
    return SimpleNamespace(
        app=app,
        db=db,
        shared=shared,
        connection=connection,
        resolve=resolve,
        credential_row=credential_row,
        reconcile=reconcile,
        recorded=recorded,
    )


def _callback_headers():
    from butlers.core.approval_callbacks import APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER

    return {
        APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: _CALLBACK_CREDENTIAL,
        "X-Butlers-Decision-Actor": "owner@telegram",
    }


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "null",
        "empty",
        "non-string",
        "malformed",
        "oversize",
        "unencodable",
        "wrong-prefix",
        "forged-signature",
        "wrong-secret",
        "wrong-prompt",
        "wrong-index",
        "old-created-at",
    ],
)
async def test_choose_rejects_unverified_token_before_recording(monkeypatch, case):
    fixture = _callback_app(monkeypatch)
    valid = _callback_token()
    tokens = {
        "null": None,
        "empty": "",
        "non-string": 7,
        "malformed": "dsk1:bad",
        "oversize": "x" * 65,
        "unencodable": "dsk1:\ud800",
        "wrong-prefix": valid.replace("dsk1:", "apr1:", 1),
        "forged-signature": valid[:-16] + ("0" * 16 if valid[-16:] != "0" * 16 else "1" * 16),
        "wrong-secret": _callback_token(secret="different-synthetic-secret"),
        "wrong-prompt": _callback_token(
            prompt_id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        ),
        "wrong-index": _callback_token(1),
        "old-created-at": _callback_token(created_at=_NOW - timedelta(microseconds=1)),
    }
    body = {"option_index": 0}
    if case != "missing":
        body["token"] = tokens[case]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fixture.app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/decisions/prompts/{_CALLBACK_ID}/choose",
            content=json.dumps(body),
            headers={**_callback_headers(), "Content-Type": "application/json"},
        )
        assert response.status_code == 403
        assert response.json() == {"detail": "invalid_callback_token"}
        fixture.reconcile.assert_not_awaited()
        fixture.recorded.assert_not_awaited()
        assert _CALLBACK_SECRET not in response.text and valid not in response.text
        positive = await client.post(
            f"/api/decisions/prompts/{_CALLBACK_ID}/choose",
            json={"option_index": 0, "token": valid},
            headers=_callback_headers(),
        )
    assert positive.status_code == 200
    assert positive.json()["meta"]["created"] is True
    assert fixture.recorded.await_args.kwargs["actor"] == "owner@telegram"
    assert fixture.recorded.await_args.kwargs["option"] == "Ship it"

    if case == "missing":
        from butlers.core.decision_desk import DecisionIntentError

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fixture.app), base_url="http://test"
        ) as client:
            unoffered = await client.post(
                f"/api/decisions/prompts/{_CALLBACK_ID}/choose",
                json={"option_index": 15, "token": _callback_token(15)},
                headers=_callback_headers(),
            )
            assert unoffered.status_code == 422
            assert unoffered.json()["detail"] == "option_not_offered"
            fixture.recorded.side_effect = DecisionIntentError("options_changed", kind="conflict")
            changed = await client.post(
                f"/api/decisions/prompts/{_CALLBACK_ID}/choose",
                json={"option_index": 0, "token": valid},
                headers=_callback_headers(),
            )
            assert changed.status_code == 409 and changed.json()["detail"] == "options_changed"
            fixture.connection.fetchrow.return_value = None
            missing = await client.post(
                f"/api/decisions/prompts/{_CALLBACK_ID}/choose",
                json={"option_index": 0, "token": valid},
                headers=_callback_headers(),
            )
            assert missing.status_code == 404 and missing.json()["detail"] == "prompt_not_found"


@pytest.mark.parametrize("case", ["missing-pool", "missing-secret", "lookup-error"])
async def test_choose_secret_unavailable_is_fail_closed(monkeypatch, case):
    from butlers.core.approval_callbacks import APPROVAL_CALLBACK_SECRET_KEY

    fixture = _callback_app(monkeypatch)
    monkeypatch.setenv(APPROVAL_CALLBACK_SECRET_KEY, "environment-must-not-rescue-this")
    if case == "missing-pool":

        def unavailable():
            raise KeyError("synthetic-private-pool-error")

        fixture.db.credential_shared_pool = unavailable
    outcomes = [None, ""] if case == "missing-secret" else [RuntimeError("synthetic-private-error")]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fixture.app), base_url="http://test"
    ) as client:
        for outcome in outcomes:
            fixture.credential_row.side_effect = outcome if isinstance(outcome, Exception) else None
            fixture.credential_row.return_value = (
                None if outcome is None else {"secret_value": outcome}
            )
            response = await client.post(
                f"/api/decisions/prompts/{_CALLBACK_ID}/choose",
                json={"option_index": 0, "token": _callback_token()},
                headers=_callback_headers(),
            )
            assert response.status_code == 503
            assert response.json() == {"detail": "callback_verification_unavailable"}
            fixture.reconcile.assert_not_awaited()
            fixture.recorded.assert_not_awaited()
    if case != "missing-pool":
        fixture.resolve.assert_awaited_with(APPROVAL_CALLBACK_SECRET_KEY, env_fallback=False)
