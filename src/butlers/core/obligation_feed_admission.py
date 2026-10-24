"""Private accepted-feed admission; no Home recording, runner or D7 authority.

Lifecycle fixes the Home declaration and Switchboard registration. The ingest
hook offers only a committed canonical UUID, never an envelope or trusted DTO.
All source reads use GoalAdmission's passed, already-ledger-locked connection.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import date, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from butlers.config import load_config, obligation_config_material_digest
from butlers.core.catalog_reader import PublishedCatalogProvider
from butlers.core.goal_admission import (
    Evidence,
    EvidenceRefused,
    GoalAdmission,
    Obligation,
    SourceRef,
    digest,
)
from butlers.core.tool_catalog import ToolCatalog

logger = logging.getLogger(__name__)
ADMISSION_SECONDS = 5.0
_HOME = Path(__file__).resolve().parents[3] / "roster" / "home"


def _canonical(value):
    def encode(item):
        if isinstance(item, UUID):
            return str(item)
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError

    try:
        text = json.dumps(
            value, default=encode, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        if len(text.encode()) > 1048576:
            raise ValueError
        return json.loads(text)
    except (TypeError, ValueError, RecursionError):
        raise EvidenceRefused("feed_material_unavailable") from None


def _uuid(value):
    # Actual asyncpg UUID is a genuine subclass; strings/duck objects are not rows.
    if not isinstance(value, UUID):
        raise EvidenceRefused("feed_identity_unavailable")
    return UUID(bytes=value.bytes)


class _AcceptedFeed(PublishedCatalogProvider):
    """One invocation's source reader and cached SAME-connection fleet projection."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.connection = self.view = None

    async def load(self, connection, source=None):
        if source is None:
            if connection is not self.connection or self.view is None:
                raise EvidenceRefused("catalog_unavailable")
            return self.view
        runtime = self.runtime
        if source.kind != "message":
            raise EvidenceRefused("feed_identity_unavailable")
        try:
            request = UUID(source.id)
        except (ValueError, TypeError, AttributeError):
            raise EvidenceRefused("feed_identity_unavailable") from None
        guard = await connection.fetchrow(
            "SELECT current_schema() AS schema,current_user AS role,EXISTS("
            "SELECT 1 FROM pg_class WHERE relnamespace=pg_my_temp_schema() "
            "AND relname=ANY($1::text[])) AS shadowed",
            ["message_inbox", "ingestion_events", "connector_registry"],
        )
        if guard is None or dict(guard) != {
            "schema": "switchboard",
            "role": "butler_switchboard_rw",
            "shadowed": False,
        }:
            raise EvidenceRefused("feed_source_unavailable")
        # Fixed order after admission ledger locks: inbox, canonical event, registry.
        rows = await connection.fetch(
            "SELECT id,received_at,request_context,raw_payload,normalized_text,attachments,"
            "lifecycle_state,schema_version FROM switchboard.message_inbox "
            "WHERE id=$1 ORDER BY received_at FOR SHARE",
            request,
        )
        event = await connection.fetchrow(
            "SELECT * FROM public.ingestion_events WHERE id=$1 FOR SHARE",
            request,
        )
        if (
            len(rows) != 1
            or not event
            or _uuid(rows[0]["id"]) != request
            or _uuid(event["id"]) != request
        ):
            raise EvidenceRefused("feed_source_unavailable")
        inbox = dict(rows[0])
        event = dict(event)
        raw, context = inbox["raw_payload"], inbox["request_context"]
        if type(raw) is not dict or type(context) is not dict:
            raise EvidenceRefused("feed_material_unavailable")
        src, evt, payload, control = (raw.get(k) for k in ("source", "event", "payload", "control"))
        if any(type(x) is not dict for x in (src, evt, payload, control)):
            raise EvidenceRefused("feed_material_unavailable")
        if (
            inbox["schema_version"] != "message_inbox.v2"
            or inbox["received_at"] != event["received_at"]
            or context.get("request_id") != str(request)
            or context.get("received_at") != inbox["received_at"].isoformat()
            or context.get("dedupe_key") != event["dedupe_key"]
            or context.get("dedupe_strategy") != event["dedupe_strategy"]
            or not event["dedupe_key"]
            or src
            != {
                "channel": event["source_channel"],
                "provider": event["source_provider"],
                "endpoint_identity": event["source_endpoint_identity"],
            }
            or context.get("source_channel") != event["source_channel"]
            or context.get("source_endpoint_identity") != event["source_endpoint_identity"]
            or context.get("source_sender_identity") != event["source_sender_identity"]
            or type(raw.get("sender")) is not dict
            or raw["sender"].get("identity") != event["source_sender_identity"]
            or evt.get("external_event_id") != event["external_event_id"]
            or payload.get("normalized_text") != inbox["normalized_text"]
            or control.get("ingestion_tier") != event["ingestion_tier"]
            or context.get("ingestion_tier") != event["ingestion_tier"]
            or control.get("policy_tier") != event["policy_tier"]
            or context.get("triage_decision") != event["triage_decision"]
            or context.get("triage_target") != event["triage_target"]
        ):
            raise EvidenceRefused("feed_identity_unavailable")
        # Channel/provider mapping is deliberately closed to this owning feature.
        # Other registered feeds retain a durable accepted-record-only disposition.
        channel, provider, endpoint = (
            event[k] for k in ("source_channel", "source_provider", "source_endpoint_identity")
        )
        if (
            not all(type(x) is str and x for x in (channel, provider, endpoint))
            or channel != provider
        ):
            raise EvidenceRefused("feed_identity_unavailable")
        registered = await connection.fetchrow(
            "SELECT connector_type,endpoint_identity,capabilities,deleted_at,archived_at "
            "FROM switchboard.connector_registry "
            "WHERE connector_type=$1 AND endpoint_identity=$2 FOR SHARE",
            channel,
            endpoint,
        )
        if (
            not registered
            or registered["connector_type"] != channel
            or registered["endpoint_identity"] != endpoint
            or registered["deleted_at"] is not None
            or registered["archived_at"] is not None
            or type(registered["capabilities"]) is not dict
            or registered["capabilities"].get("channel_kind") != "feed"
        ):
            raise EvidenceRefused("feed_registry_unavailable")
        partition = f"feed:{channel}:{endpoint}"
        if len(partition) > 256:
            raise EvidenceRefused("feed_identity_unavailable")
        record_only = Evidence("feed", partition, outside_content=True, record_only=True)
        if (
            channel != "home_assistant"
            or inbox["lifecycle_state"] not in {"accepted", "processing", "completed", "failed"}
            or event["ingestion_tier"] != "full"
            or event["triage_decision"] != "pass_through"
            or control.get("bearer_scrub_failed")
            or control.get("quarantined")
            or not inbox["normalized_text"]
            or payload.get("raw") is None
        ):
            return record_only
        spec = runtime.declaration()
        if spec is None:
            return record_only
        snapshot = await self.snapshot(connection)
        if snapshot.registration("switchboard").catalog != runtime.registry:
            raise EvidenceRefused("catalog_unavailable")
        view = self.project(snapshot)
        allowed = {c.id: c for c in view.capabilities}
        grants = spec.obligation.declaration.capabilities
        if not grants:
            raise EvidenceRefused("feed_ceiling_unavailable")
        for grant in grants:
            cap = allowed.get(grant.capability)
            if (
                cap is None
                or cap.owner_domain != "home"
                or not cap.presentable
                or cap.owner_authority
                or cap.effect_class not in {"read", "record", "communicate"}
                or (cap.effect_class == "communicate" and not cap.owner_default)
                or set(dict(grant.resources)) - set(cap.resource_params)
            ):
                raise EvidenceRefused("feed_ceiling_unavailable")
        # Complete accepted sanitized material and actual loaded declaration are
        # durable via contract_digest; references alone do not certify content.
        material = _canonical(
            {
                "inbox": inbox,
                "event": event,
                "registry": dict(registered),
                "declaration": spec.model_dump(mode="json"),
                "home_file": runtime.home.source_sha256,
                "home_material": runtime.home_digest,
                "switchboard_material": runtime.switchboard_digest,
            }
        )
        self.connection, self.view = connection, view
        return Evidence(
            "feed",
            partition,
            outside_content=True,
            obligation=Obligation("home", digest(material), grants),
        )


class PrivateFeedAdmission:
    """Fast offer, one retained operation, and one original absolute deadline.

    Scheduling is never a successful admission. Expired/resistant work stays
    owned and blocks new work until terminal; shutdown reports incompleteness.
    """

    def __init__(self, pool, daemon, registry, home):
        self.pool, self.daemon, self.registry, self.home = pool, daemon, registry, home
        self.switchboard = daemon.config
        self.switchboard_digest = obligation_config_material_digest(daemon.config)
        self.home_digest = obligation_config_material_digest(home)
        self._pending, self._watchers = {}, {}
        self._closed = False

    def declaration(self):
        if (
            self.daemon.config is not self.switchboard
            or self.daemon._tool_catalog != self.registry
            or obligation_config_material_digest(self.switchboard) != self.switchboard_digest
            or obligation_config_material_digest(self.home) != self.home_digest
            or self.home.source_material_sha256 != self.home_digest
            or hashlib.sha256((_HOME / "butler.toml").read_bytes()).hexdigest()
            != self.home.source_sha256
        ):
            raise EvidenceRefused("feed_declaration_unavailable")
        specs = [
            s for s in self.home.subscriptions if s.kind == "feed" and s.source == "home_assistant"
        ]
        if not specs:
            return None
        if len(specs) != 1 or specs[0].obligation.status != "valid":
            raise EvidenceRefused("feed_declaration_unavailable")
        return specs[0]

    async def _settle(self, task, deadline):
        interrupted = False
        while not task.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait({task}, timeout=remaining)
            except asyncio.CancelledError:
                interrupted = True
                task.cancel()
        return interrupted

    async def _watch(self, task, deadline):
        await self._settle(task, deadline)
        if not task.done():
            task.cancel()
            logger.warning("Private feed admission outcome unknown; operation still owned")

    def offer(self, request_id):
        if self._closed or self._pending:
            logger.warning("Private feed admission unavailable")
            return
        try:
            request_id = _uuid(request_id)
        except EvidenceRefused:
            logger.warning("Private feed admission unavailable")
            return
        reader = _AcceptedFeed(self)
        task = asyncio.create_task(
            GoalAdmission(self.pool, reader, reader).admit_submission(
                SourceRef("message", str(request_id)),
                uuid5(NAMESPACE_URL, f"butlers:feed:{request_id}"),
            ),
            name="private-feed-admission",
        )
        deadline = asyncio.get_running_loop().time() + ADMISSION_SECONDS
        self._pending[task] = deadline

        def finished(done):
            self._pending.pop(done, None)
            if not done.cancelled() and done.exception() is not None:
                logger.warning("Private feed admission outcome unknown")

        task.add_done_callback(finished)
        watcher = asyncio.create_task(
            self._watch(task, deadline), name="private-feed-admission-bound"
        )
        self._watchers[watcher] = deadline
        watcher.add_done_callback(lambda done: self._watchers.pop(done, None))

    async def aclose(self):
        self._closed = True
        interrupted = False
        for task, deadline in tuple(self._pending.items()):
            task.cancel()
            interrupted |= await self._settle(task, deadline)
        for task in tuple(self._pending):
            if task.done():
                self._pending.pop(task, None)
        # Watchers never outlive their operation's original bound, including
        # repeated caller cancellation; retain them until terminal as well.
        for watcher, deadline in tuple(self._watchers.items()):
            interrupted |= await self._settle(watcher, deadline)
        if self._pending or any(not w.done() for w in self._watchers):
            raise RuntimeError("private_feed_admission_cleanup_incomplete")
        if interrupted:
            raise asyncio.CancelledError


def start_feed_admission(pool, daemon, registry, *, mode="off"):
    """Lifecycle-only factory; OFF precedes all Home/file/catalog/database IO."""
    if mode == "off":
        return None
    if (
        mode != "dark"
        or daemon.config.name != "switchboard"
        or daemon.config.db_schema != "switchboard"
        or type(registry) is not ToolCatalog
        or daemon._tool_catalog != registry
        or not registry.classification_complete
    ):
        raise ValueError("private_feed_admission_binding_unavailable")
    home = load_config(_HOME)
    if home.name != "home" or home.db_schema != "home":
        raise ValueError("private_feed_admission_binding_unavailable")
    return PrivateFeedAdmission(pool, daemon, registry, home)
