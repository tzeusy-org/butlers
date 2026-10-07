"""Switchboard-owned source-copy floor for a proven native no-dispatch branch.

The fixed registry lookup reads the owning Chronicler plan over MCP. A caller
UUID only locates that plan; it never supplies source rows, cutoff or verdict.
Routed, busy, errored, legacy or ambiguous cases are refused, not declared empty.
No peer SQL, model grant, new role or raw-content exception is exposed.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import asyncpg

from butlers.connectors.mcp_client import CachedMCPClient
from butlers.connectors.owntracks_forgetting import FrozenRaw, frozen_manifest
from butlers.location_retention import content_digest, logical_digest


class CopyFloorUnavailable(RuntimeError):
    """Closed absence or lifecycle mismatch; never carry a raw row/error text."""


async def read_source_receipt(pool: asyncpg.Pool, decision_id: UUID) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM location_retention_copy_receipts WHERE decision_id=$1",
            decision_id,
        )
    if row is None:
        raise CopyFloorUnavailable("copy_receipt_unknown")
    return {**dict(row), "manifest_digest": row["manifest_digest"].hex()}


async def _registered_plan(pool: asyncpg.Pool, decision_id: UUID) -> dict:
    from butlers.tools.switchboard.registry.registry import resolve_routing_target

    source, _ = await resolve_routing_target(pool, "chronicler")
    if source is None:
        raise CopyFloorUnavailable("registered_source_unavailable")
    endpoint = source["endpoint_url"]
    url = urlsplit(endpoint)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or any(
            (
                url.username,
                url.password,
                url.query,
                url.fragment,
            )
        )
    ):
        raise CopyFloorUnavailable("registered_source_unavailable")
    client = CachedMCPClient(endpoint, client_name="location-source-holder")
    try:
        return await client.call_tool(
            "chronicler_location_retention_status",
            {"decision_id": str(decision_id)},
        )
    except Exception:
        raise CopyFloorUnavailable("registered_source_unavailable") from None
    finally:
        await client.aclose()


async def forget_skipped_source(pool: asyncpg.Pool, decision_id: UUID) -> dict:
    """Frozen plan read before locks; source redaction/floors/receipt one own TX.

    The terminal native skip path commits without an LLM or target dispatch.
    Any other path still requires its complete owning copy cohort and is held.
    Missing source rows never count as proof of a completed purge.
    """
    plan = await _registered_plan(pool, decision_id)
    try:
        rows = tuple(FrozenRaw.model_validate(row) for row in plan["rows"])
        if (
            not 1 <= len(rows) <= 256
            or len({row.accepted_request_id for row in rows}) != len(rows)
            or len({row.raw_id for row in rows}) != len(rows)
            or UUID(str(plan["decision_id"])) != decision_id
        ):
            raise ValueError
        manifest = frozen_manifest(
            decision_id,
            plan["policy_version"],
            datetime.fromisoformat(str(plan["cutoff"])),
            rows,
        )
        if manifest.hex() != plan["manifest_digest"] or plan["state"] not in {
            "holder_pending",
            "ready",
            "raw_unknown",
            "complete",
        }:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise CopyFloorUnavailable("source_plan_mismatch") from None
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout='2s'")
            await conn.execute("SET LOCAL statement_timeout='5s'")
            # Match the canonical ingest dedup mutex. Lookup is source-owned;
            # the request id is not accepted as content or origin authority.
            source_rows = []
            for frozen in sorted(rows, key=lambda row: str(row.accepted_request_id)):
                source = await conn.fetchrow(
                    "SELECT * FROM switchboard.message_inbox WHERE id=$1",
                    frozen.accepted_request_id,
                )
                if source is None:
                    raise CopyFloorUnavailable("accepted_source_unknown")
                key = (source["request_context"] or {}).get("dedupe_key")
                if not isinstance(key, str) or not key:
                    raise CopyFloorUnavailable("accepted_source_unknown")
                source_rows.append((key, frozen))
            for key in sorted({key for key, _ in source_rows}):
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", key)
            prior = await conn.fetchrow(
                "SELECT * FROM location_retention_copy_receipts WHERE decision_id=$1",
                decision_id,
            )
            if prior is not None:
                if prior["manifest_digest"] != manifest or prior["forgotten_count"] != len(rows):
                    raise CopyFloorUnavailable("copy_receipt_mismatch")
            else:
                locked = await conn.fetch(
                    "SELECT * FROM switchboard.message_inbox WHERE id=ANY($1::uuid[]) "
                    "ORDER BY id,received_at FOR UPDATE",
                    [row.accepted_request_id for row in rows],
                )
                by_id = {row["id"]: row for row in locked}
                if len(locked) != len(rows) or len(by_id) != len(rows):
                    raise CopyFloorUnavailable("accepted_source_unknown")
                for key, frozen in source_rows:
                    stored = by_id[frozen.accepted_request_id]
                    raw = stored["raw_payload"] or {}
                    context = stored["request_context"] or {}
                    payload = raw.get("payload") or {}
                    native_terminal = stored["decomposition_output"] or {}
                    outcomes = stored["dispatch_outcomes"] or {}
                    if (
                        context.get("dedupe_key") != key
                        or raw.get("source", {}).get("provider") != "owntracks"
                        or stored["lifecycle_state"] != "skipped"
                        or stored["final_state_at"] is None
                        or stored["response_summary"] != "Policy bypass: skip"
                        or native_terminal.get("policy_bypass") is not True
                        or native_terminal.get("triage_decision") != "skip"
                        or set(outcomes) != {"request_id"}
                        or str(outcomes["request_id"]) != str(frozen.accepted_request_id)
                        or content_digest({"raw": payload.get("raw")}).hex()
                        != frozen.accepted_payload_digest
                        or content_digest({"text": stored["normalized_text"]}).hex()
                        != frozen.accepted_normalized_digest
                    ):
                        raise CopyFloorUnavailable("source_copy_cohort_pending")
                    if await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM sessions WHERE request_id=$1)",
                        frozen.accepted_request_id,
                    ):
                        raise CopyFloorUnavailable("source_copy_cohort_pending")
                receipt_id = uuid4()
                await conn.execute(
                    """INSERT INTO location_retention_copy_receipts
                       (decision_id,manifest_digest,receipt_id,source_kind,forgotten_count)
                       VALUES($1,$2,$3,'switchboard_skipped',$4)""",
                    decision_id,
                    manifest,
                    receipt_id,
                    len(rows),
                )
                for key, frozen in source_rows:
                    await conn.execute(
                        """INSERT INTO location_retention_source_floors
                           (dedupe_digest,request_id,decision_id,logical_source_digest)
                           VALUES($1,$2,$3,$4)""",
                        logical_digest(key),
                        frozen.accepted_request_id,
                        decision_id,
                        bytes.fromhex(frozen.logical_source_digest),
                    )
                    await conn.execute(
                        """UPDATE switchboard.message_inbox
                           SET raw_payload=jsonb_build_object('retention', 'forgotten'),
                               normalized_text='[OwnTracks exact evidence forgotten]',
                               attachments='[]',processing_metadata='{}',
                               updated_at=clock_timestamp() WHERE id=$1""",
                        frozen.accepted_request_id,
                    )
    return await read_source_receipt(pool, decision_id)
