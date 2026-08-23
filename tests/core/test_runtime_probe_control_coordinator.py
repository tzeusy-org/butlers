"""The Switchboard-owned runtime-probe coordinator.

REQ-core-credentials-002 fixes the ordering the coordinator must obey ---
verify, then commit a receipt, then admission, then lookup, then launch, then
persist --- and REQ-database-security-008 makes the receipt the thing that
makes a capability single-use.  REQ-dashboard-model-settings-001 fixes what the
launch must look like (same runtime environment as new daemon work, no domain
MCP tools, no routed provenance) and what a probe is allowed to write.

Most of the value here is negative.  Every rejected path is asserted to have
touched *nothing*: no receipt, no catalog read, no runtime launch, and above
all no verification write, because a probe that persists a failure on a busy
or timed-out request would corrupt exactly the evidence it exists to produce.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

import butlers.core.runtime_probe_control.capability as cap
import butlers.core.runtime_probe_control.coordinator as coord
from butlers.core.runtime_probe_control.keys import (
    VerifierKeyring,
    VerifierSnapshot,
    parse_signer_document,
    parse_verifier_keyring_document,
)
from butlers.testing.runtime_probe_control import (
    current_entry,
    keyring_document,
    signer_document,
    synthetic_keypair,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
_KID = "probe-2026-05a"
_ENTRY_ID = UUID("a1b2c3d4-5566-4788-99aa-bbccddeeff01")


def _encode(document: object) -> bytes:
    return json.dumps(document).encode("utf-8")


@pytest.fixture
def signer():
    seed, public_key = synthetic_keypair()
    parsed = parse_signer_document(
        _encode(signer_document(seed, kid=_KID, sign_from=_NOW - timedelta(days=1)))
    )
    return parsed, public_key


@pytest.fixture
def keyring(signer) -> VerifierKeyring:
    _, public_key = signer
    return parse_verifier_keyring_document(
        _encode(
            keyring_document(
                current_entry(public_key, kid=_KID, sign_from=_NOW - timedelta(days=1))
            )
        )
    )


def _sign(signer_key, **overrides) -> str:
    kwargs: dict[str, Any] = {"caller": "dashboard", "catalog_entry_id": _ENTRY_ID, "now": _NOW}
    kwargs.update(overrides)
    return cap.sign_capability(signer_key, **kwargs)


# ---------------------------------------------------------------------------
# Test doubles
#
# Each double records what it was asked to do into one shared ``trace`` list,
# so a test can assert both *that* something happened and *in what order* ---
# the receipt-before-everything rule is an ordering claim, not a count.
# ---------------------------------------------------------------------------


class _Pool:
    """A catalog the coordinator can read, and a log of every statement."""

    def __init__(self, rows: dict[UUID, dict[str, Any]], trace: list[str]) -> None:
        self._rows = rows
        self._trace = trace
        self.statements: list[str] = []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.statements.append(sql)
        self._trace.append("lookup")
        return self._rows.get(args[0])


class _Receipts:
    def __init__(self, trace: list[str], *, claimed: bool = True) -> None:
        self._trace = trace
        self._claimed = claimed
        self.claims: list[tuple[bytes, str, datetime]] = []

    async def claim(self, *, nonce: bytes, kid: str, expires_at: datetime) -> bool:
        self._trace.append("claim")
        self.claims.append((nonce, kid, expires_at))
        return self._claimed


class _Persistence:
    def __init__(self, trace: list[str]) -> None:
        self._trace = trace
        self.records: list[dict[str, Any]] = []

    async def record(
        self,
        *,
        catalog_entry_id: UUID,
        ok: bool,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> bool:
        self._trace.append("persist")
        self.records.append(
            {
                "catalog_entry_id": catalog_entry_id,
                "ok": ok,
                "latency_ms": latency_ms,
                "error": error,
            }
        )
        return True


class _Adapter:
    """A runtime adapter that records its invocation instead of spawning one."""

    def __init__(self, trace: list[str], behaviour: Any) -> None:
        self._trace = trace
        self._behaviour = behaviour
        self.invocations: list[dict[str, Any]] = []

    async def invoke(self, **kwargs: Any) -> tuple[str | None, list[dict], dict | None]:
        self._trace.append("launch")
        self.invocations.append(kwargs)
        if callable(self._behaviour):
            return await self._behaviour(**kwargs)
        return self._behaviour


def _catalog_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": _ENTRY_ID,
        "runtime_type": "claude",
        "model_id": "claude-sonnet-4",
        "extra_args": ["--flag"],
    }
    row.update(overrides)
    return row


class _Harness:
    """Everything a coordinator needs, plus the trace that proves the ordering."""

    def __init__(
        self,
        keyring: VerifierKeyring | None,
        *,
        rows: dict[UUID, dict[str, Any]] | None = None,
        behaviour: Any = ("OK", [], None),
        claimed: bool = True,
        codex_auth_authority: Any = None,
    ) -> None:
        self.trace: list[str] = []
        self.pool = _Pool(rows if rows is not None else {_ENTRY_ID: _catalog_row()}, self.trace)
        self.receipts = _Receipts(self.trace, claimed=claimed)
        self.persistence = _Persistence(self.trace)
        self.adapter = _Adapter(self.trace, behaviour)
        self.adapter_requests: list[dict[str, Any]] = []
        snapshot = VerifierSnapshot(
            keyring=keyring,
            unavailable_reason=None if keyring else "verifier keyring is not mounted",
        )

        async def _build_adapter(pool: Any, runtime_type: str, model_id: str, **kwargs: Any) -> Any:
            self.adapter_requests.append(
                {"pool": pool, "runtime_type": runtime_type, "model_id": model_id, **kwargs}
            )
            return self.adapter

        self.coordinator = coord.RuntimeProbeCoordinator(
            self.pool,
            verifier=lambda: snapshot,
            receipts=self.receipts,
            persistence=self.persistence,
            adapter_factory=_build_adapter,
            codex_auth_authority=codex_auth_authority,
        )

    async def run(self, capability: str | None, *, now: datetime = _NOW) -> coord.ProbeResult:
        return await self.coordinator.run(capability, now=now)


# ---------------------------------------------------------------------------
# The happy path: what a probe actually launches
# ---------------------------------------------------------------------------


async def test_probe_launches_the_resolved_entry_as_new_daemon_work(signer, keyring):
    """Criterion 3: the coordinator reproduces a daemon invocation's runtime."""
    signer_key, _ = signer
    harness = _Harness(keyring)

    result = await harness.run(_sign(signer_key))

    assert result.status is coord.ProbeStatus.COMPLETED
    assert result.ok is True

    (invocation,) = harness.adapter.invocations
    assert invocation["model"] == "claude-sonnet-4"
    assert invocation["runtime_args"] == ["--flag"]
    assert invocation["timeout"] == coord.PROBE_TIMEOUT_S == 30
    assert invocation["max_turns"] == 1
    # No domain MCP tools: the probe exercises the runtime, not the butler.
    assert invocation["mcp_servers"] == {}
    # The same shared runtime home a new daemon invocation would resolve.
    assert invocation["env"] == dict(os.environ)


async def test_adapter_is_built_from_the_catalog_entry_and_explicit_codex_authority(
    signer, keyring
):
    """Criterion 3: authority is passed explicitly, never inferred from the pool."""
    signer_key, _ = signer
    authority = object()
    harness = _Harness(keyring, codex_auth_authority=authority)

    await harness.run(_sign(signer_key))

    (request,) = harness.adapter_requests
    assert request["runtime_type"] == "claude"
    assert request["model_id"] == "claude-sonnet-4"
    assert request["pool"] is harness.pool
    assert request["codex_auth_authority"] is authority


async def test_opencode_entry_is_launched_with_the_canonical_model_id(signer, keyring):
    """Criterion 3: the canonical id goes to the adapter, which owns the mapping.

    Mapping canonical to execution identifiers here would produce a second,
    drifting copy of ``canonical_to_execution_model``.  The probe passes the
    catalog's canonical id exactly as daemon work does, so the adapter's own
    mapper is the only mapper.
    """
    signer_key, _ = signer
    entry = _catalog_row(runtime_type="opencode", model_id="anthropic/claude-sonnet-4")
    harness = _Harness(keyring, rows={_ENTRY_ID: entry})

    await harness.run(_sign(signer_key))

    (invocation,) = harness.adapter.invocations
    assert invocation["model"] == "anthropic/claude-sonnet-4"


async def test_receipt_commits_before_lookup_launch_and_persistence(signer, keyring):
    """Criterion 1/REQ-database-security-008: ordering, not merely presence."""
    signer_key, _ = signer
    harness = _Harness(keyring)

    await harness.run(_sign(signer_key))

    assert harness.trace == ["claim", "lookup", "launch", "persist"]


async def test_receipt_records_the_digest_inputs_and_the_capability_expiry(signer, keyring):
    signer_key, _ = signer
    harness = _Harness(keyring)
    compact = _sign(signer_key)

    await harness.run(compact)

    ((nonce, kid, expires_at),) = harness.receipts.claims
    assert len(nonce) == cap.NONCE_BYTES
    assert kid == _KID
    assert expires_at == _NOW + cap.DEFAULT_LIFETIME


async def test_successful_probe_records_evidence_and_clears_the_error(signer, keyring):
    """Criterion 6: success updates verification evidence."""
    signer_key, _ = signer
    harness = _Harness(keyring)

    result = await harness.run(_sign(signer_key))

    (record,) = harness.persistence.records
    assert record["catalog_entry_id"] == _ENTRY_ID
    assert record["ok"] is True
    assert record["error"] is None
    assert isinstance(record["latency_ms"], int)
    assert result.latency_ms == record["latency_ms"]


async def test_provider_failure_records_a_failed_probe(signer, keyring):
    """A provider that answers badly is a real verification failure, not an outage."""
    signer_key, _ = signer

    async def _fail(**_kwargs: Any) -> tuple[str | None, list[dict], dict | None]:
        raise RuntimeError("provider said no")

    harness = _Harness(keyring, behaviour=_fail)

    result = await harness.run(_sign(signer_key))

    assert result.status is coord.ProbeStatus.COMPLETED
    assert result.ok is False
    (record,) = harness.persistence.records
    assert record["ok"] is False


async def test_empty_response_is_a_failed_probe(signer, keyring):
    signer_key, _ = signer
    harness = _Harness(keyring, behaviour=("   ", [], None))

    result = await harness.run(_sign(signer_key))

    assert result.status is coord.ProbeStatus.COMPLETED
    assert result.ok is False
    assert harness.persistence.records[0]["ok"] is False


async def test_probe_writes_nothing_but_the_receipt_and_the_verification_row(signer, keyring):
    """Criterion 6: no dispatch attempt, routed provenance, breaker reset, or session.

    The coordinator's only statement against the pool is the catalog read; every
    other write it is capable of goes through the two injected narrow surfaces.
    Enumerated by inspecting every statement the pool saw, not by grepping for
    table names the coordinator might have spelled differently.
    """
    signer_key, _ = signer
    harness = _Harness(keyring)

    await harness.run(_sign(signer_key))

    assert len(harness.pool.statements) == 1
    statement = harness.pool.statements[0].lower()
    assert "select" in statement
    assert "model_catalog" in statement
    for forbidden in (
        "insert",
        "update",
        "delete",
        "model_dispatch_attempts",
        "breaker",
        "session",
    ):
        assert forbidden not in statement


# ---------------------------------------------------------------------------
# Rejections: nothing may happen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mangle",
    [
        pytest.param(lambda compact: None, id="absent"),
        pytest.param(lambda compact: "", id="empty"),
        pytest.param(lambda compact: compact + "x", id="tampered-signature"),
        pytest.param(lambda compact: compact.split(".", 1)[1], id="truncated"),
    ],
)
async def test_unauthorized_capability_touches_nothing(signer, keyring, mangle):
    """Criterion 1 and 7: rejection precedes receipt, lookup, launch, persistence."""
    signer_key, _ = signer
    harness = _Harness(keyring)

    result = await harness.run(mangle(_sign(signer_key)))

    assert result.status is coord.ProbeStatus.UNAUTHORIZED
    assert result.ok is None
    assert harness.trace == []
    assert harness.persistence.records == []


async def test_expired_capability_is_unauthorized_and_touches_nothing(signer, keyring):
    signer_key, _ = signer
    harness = _Harness(keyring)

    result = await harness.run(_sign(signer_key), now=_NOW + timedelta(minutes=5))

    assert result.status is coord.ProbeStatus.UNAUTHORIZED
    assert harness.trace == []


async def test_capability_for_another_entry_probes_that_entry_only(signer, keyring):
    """The catalog entry is taken from the signed claim, never from the request body."""
    signer_key, _ = signer
    other = uuid4()
    harness = _Harness(keyring, rows={other: _catalog_row(id=other, model_id="other-model")})

    result = await harness.run(_sign(signer_key, catalog_entry_id=other))

    assert result.status is coord.ProbeStatus.COMPLETED
    assert harness.persistence.records[0]["catalog_entry_id"] == other


async def test_replayed_capability_leaves_verification_history_untouched(signer, keyring):
    """Criterion 2 and 7: a lost receipt race ends the request with no side effect."""
    signer_key, _ = signer
    harness = _Harness(keyring, claimed=False)

    result = await harness.run(_sign(signer_key))

    assert result.status is coord.ProbeStatus.REPLAY
    assert harness.trace == ["claim"]
    assert harness.persistence.records == []


async def test_absent_verifier_keyring_is_fail_closed_unavailable(signer):
    """Criterion 9: with no verifier mount the deployed path refuses everything.

    It must refuse *before* verification, so a mountless deployment cannot be
    probed for which capabilities would otherwise have been well-formed.
    """
    signer_key, _ = signer
    harness = _Harness(None)

    result = await harness.run(_sign(signer_key))

    assert result.status is coord.ProbeStatus.UNAVAILABLE
    assert harness.trace == []
    assert harness.persistence.records == []


async def test_unknown_catalog_entry_is_unavailable_and_persists_nothing(signer, keyring):
    """Criterion 7: an entry that vanished is an outage, not a model failure."""
    signer_key, _ = signer
    harness = _Harness(keyring, rows={})

    result = await harness.run(_sign(signer_key))

    assert result.status is coord.ProbeStatus.UNAVAILABLE
    assert harness.trace == ["claim", "lookup"]
    assert harness.persistence.records == []


async def test_codex_entry_without_explicit_authority_is_unavailable(signer, keyring):
    """Criterion 3 and 7: missing authority is infrastructure absence, not failure.

    Persisting ``ok=false`` here would let a missing credential mount evict
    Codex from routing --- the exact confusion verify-all already avoids.
    """
    signer_key, _ = signer
    harness = _Harness(keyring, rows={_ENTRY_ID: _catalog_row(runtime_type="codex")})

    result = await harness.run(_sign(signer_key))

    assert result.status is coord.ProbeStatus.UNAVAILABLE
    assert harness.persistence.records == []
    assert harness.adapter.invocations == []


async def test_codex_entry_without_system_global_authority_is_unavailable(signer, keyring):
    signer_key, _ = signer

    class _Authority:
        has_system_global_authority = False

    harness = _Harness(
        keyring,
        rows={_ENTRY_ID: _catalog_row(runtime_type="codex")},
        codex_auth_authority=_Authority(),
    )

    result = await harness.run(_sign(signer_key))

    assert result.status is coord.ProbeStatus.UNAVAILABLE
    assert harness.persistence.records == []


async def test_codex_entry_with_system_global_authority_runs(signer, keyring):
    signer_key, _ = signer

    class _Authority:
        has_system_global_authority = True

    harness = _Harness(
        keyring,
        rows={_ENTRY_ID: _catalog_row(runtime_type="codex")},
        codex_auth_authority=_Authority(),
    )

    result = await harness.run(_sign(signer_key))

    assert result.status is coord.ProbeStatus.COMPLETED
    assert (
        harness.adapter_requests[0]["codex_auth_authority"] is harness.coordinator._codex_authority
    )


async def test_timeout_reports_timeout_and_persists_nothing(signer, keyring):
    """Criterion 7: a probe that never finished says nothing about the model."""
    signer_key, _ = signer

    async def _hang(**_kwargs: Any) -> tuple[str | None, list[dict], dict | None]:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    harness = _Harness(keyring, behaviour=_hang)
    harness.coordinator._timeout_s = 0.01

    result = await harness.run(_sign(signer_key))

    assert result.status is coord.ProbeStatus.TIMEOUT
    assert result.ok is None
    assert harness.persistence.records == []
    assert harness.trace == ["claim", "lookup", "launch"]


# ---------------------------------------------------------------------------
# Admission control
# ---------------------------------------------------------------------------


def _gated_behaviour(release: asyncio.Event, entered: asyncio.Semaphore):
    async def _wait(**_kwargs: Any) -> tuple[str | None, list[dict], dict | None]:
        entered.release()
        await release.wait()
        return "OK", [], None

    return _wait


async def test_second_probe_of_the_same_entry_is_busy(signer, keyring):
    """Criterion 3: per-catalog-entry concurrency is one, and it fails fast."""
    signer_key, _ = signer
    release = asyncio.Event()
    entered = asyncio.Semaphore(0)
    harness = _Harness(keyring, behaviour=_gated_behaviour(release, entered))

    first = asyncio.create_task(harness.run(_sign(signer_key)))
    await entered.acquire()
    try:
        second = await harness.run(_sign(signer_key))
    finally:
        release.set()

    assert second.status is coord.ProbeStatus.BUSY
    assert second.ok is None
    assert (await first).status is coord.ProbeStatus.COMPLETED
    # The rejected request persisted nothing: one probe ran, one row was written.
    assert len(harness.persistence.records) == 1


async def test_busy_rejection_still_consumes_its_capability(signer, keyring):
    """The receipt precedes admission, so a busy request cannot retry its nonce.

    If admission ran first, a caller could hammer a busy entry and keep one
    capability alive across the whole attempt --- which is exactly the
    single-use property the receipt exists to provide.
    """
    signer_key, _ = signer
    release = asyncio.Event()
    entered = asyncio.Semaphore(0)
    harness = _Harness(keyring, behaviour=_gated_behaviour(release, entered))

    first = asyncio.create_task(harness.run(_sign(signer_key)))
    await entered.acquire()
    try:
        await harness.run(_sign(signer_key))
    finally:
        release.set()
    await first

    assert len(harness.receipts.claims) == 2


async def test_a_freed_entry_slot_admits_the_next_probe(signer, keyring):
    signer_key, _ = signer
    harness = _Harness(keyring)

    assert (await harness.run(_sign(signer_key))).status is coord.ProbeStatus.COMPLETED
    assert (await harness.run(_sign(signer_key))).status is coord.ProbeStatus.COMPLETED


def test_declared_limits_match_the_specified_numbers():
    """Criterion 3 names the numbers, so pin them rather than the symbols.

    The concurrency tests below read the constants back, which proves the
    coordinator honours whatever they say --- not that they still say what the
    bead specified.
    """
    assert coord.GLOBAL_CONCURRENCY == 8
    assert coord.PER_ENTRY_CONCURRENCY == 1
    assert coord.PROBE_TIMEOUT_S == 30


async def test_global_concurrency_is_capped_at_eight(signer, keyring):
    """Criterion 3: the ninth distinct entry is refused while eight are in flight."""
    signer_key, _ = signer
    release = asyncio.Event()
    entered = asyncio.Semaphore(0)
    entries = [uuid4() for _ in range(coord.GLOBAL_CONCURRENCY + 1)]
    rows = {entry: _catalog_row(id=entry) for entry in entries}
    harness = _Harness(keyring, rows=rows, behaviour=_gated_behaviour(release, entered))

    running = [
        asyncio.create_task(harness.run(_sign(signer_key, catalog_entry_id=entry)))
        for entry in entries[: coord.GLOBAL_CONCURRENCY]
    ]
    for _ in range(coord.GLOBAL_CONCURRENCY):
        await entered.acquire()
    try:
        overflow = await harness.run(_sign(signer_key, catalog_entry_id=entries[-1]))
    finally:
        release.set()

    assert overflow.status is coord.ProbeStatus.BUSY
    assert overflow.ok is None
    assert harness.persistence.records == []
    for task in running:
        assert (await task).status is coord.ProbeStatus.COMPLETED
    assert len(harness.persistence.records) == coord.GLOBAL_CONCURRENCY


async def test_entry_slots_are_released_after_a_failing_probe(signer, keyring):
    """A crashed probe must not wedge its entry against every later request."""
    signer_key, _ = signer

    async def _fail(**_kwargs: Any) -> tuple[str | None, list[dict], dict | None]:
        raise RuntimeError("provider said no")

    harness = _Harness(keyring, behaviour=_fail)

    assert (await harness.run(_sign(signer_key))).ok is False
    assert (await harness.run(_sign(signer_key))).ok is False


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


async def test_probe_result_discloses_no_capability_material(signer, keyring):
    """Criterion 8: the result a caller may log carries nothing sensitive.

    Asserted by absence: the capability's own bytes are never reproduced here,
    only searched for.
    """
    signer_key, _ = signer
    compact = _sign(signer_key)
    harness = _Harness(keyring)

    result = await harness.run(compact)

    rendered = f"{result!r} {result}"
    for segment in compact.split("."):
        assert segment not in rendered
    assert _KID not in rendered


async def test_the_runtime_prompt_is_fixed_and_independent_of_the_capability(signer, keyring):
    """Criterion 8: nothing capability-derived reaches the runtime.

    The prompt and system prompt are module constants, and the caller has no
    way to influence them --- so a capability cannot smuggle text into a model
    context, and a probe transcript cannot leak the capability that authorised
    it.
    """
    signer_key, _ = signer
    compact = _sign(signer_key)
    harness = _Harness(keyring)

    await harness.run(compact)

    (invocation,) = harness.adapter.invocations
    assert invocation["prompt"] == coord.PROBE_PROMPT
    assert invocation["system_prompt"] == coord.PROBE_SYSTEM_PROMPT

    rendered = json.dumps({key: repr(value) for key, value in invocation.items()})
    for segment in compact.split("."):
        assert segment not in rendered
    assert _KID not in rendered


@pytest.mark.parametrize(
    "mangle",
    [
        pytest.param(lambda compact: compact + "x", id="tampered-signature"),
        pytest.param(lambda compact: compact, id="accepted"),
    ],
)
async def test_coordinator_logs_disclose_no_capability_material(signer, keyring, caplog, mangle):
    """Criterion 8: rejection diagnostics name a category, never the evidence."""
    signer_key, _ = signer
    compact = _sign(signer_key)
    harness = _Harness(keyring)

    with caplog.at_level("DEBUG"):
        await harness.run(mangle(compact))

    for segment in compact.split("."):
        assert segment not in caplog.text
    assert _KID not in caplog.text


async def test_replay_and_busy_logs_disclose_no_capability_material(signer, keyring, caplog):
    signer_key, _ = signer
    compact = _sign(signer_key)
    harness = _Harness(keyring, claimed=False)

    with caplog.at_level("DEBUG"):
        assert (await harness.run(compact)).status is coord.ProbeStatus.REPLAY

    for segment in compact.split("."):
        assert segment not in caplog.text
    assert _KID not in caplog.text
