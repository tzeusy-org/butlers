"""Private durable domain-wake preparation, without admission or cause authority.

Only the daemon's existing registered receive closure calls this held adapter.
Unstamped event content is always exposed. A returned value is committed dark
material, never Evidence/Grant/creator authority or a promise of future dispatch.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from butlers.config import obligation_config_material_digest
from butlers.core.domain_event_wake import _build_wake_task_prompt, task_name_for
from butlers.core.goal_admission import digest
from butlers.core.obligation_admission import (
    DomainDarkSubmission,
    prepare_dark_submission,
    run_source_transaction,
)
from butlers.core.obligation_catalog import build_obligation_catalog
from butlers.core.obligation_schedule_sources import SCHEDULE_FIELDS
from butlers.core.obligation_sources import DefinitionRevision, SourceConflict

logger = logging.getLogger(__name__)
PREPARATION_SECONDS = 5.0


def _json(value):
    def encode(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, UUID):
            return str(item)
        raise TypeError

    try:
        text = json.dumps(
            value, default=encode, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        if len(text.encode()) > 131072:
            raise ValueError
        return text
    except (TypeError, ValueError, RecursionError):
        raise SourceConflict("domain_material_invalid") from None


def _database_row(row, *identities, nullable=()):
    """Copy actual UUID columns into the repository's exact stdlib value type.

    asyncpg's UUID is a genuine UUID subclass. Strings and UUID-shaped objects
    from a collaborator are not database identities and must still refuse.
    """
    if row is None:
        return None
    result = dict(row)
    for name in identities:
        value = result[name]
        if value is None and name in nullable:
            continue
        if not isinstance(value, UUID):
            raise SourceConflict("domain_database_identity_invalid")
        result[name] = UUID(bytes=value.bytes)
    return result


class DomainWakeRuntime:
    """One retained finite operation owner; incomplete settlement quarantines it."""

    def __init__(self, pool, daemon, runtime, registry):
        self.pool, self.daemon = pool, daemon
        self.config, self.policy, self.registry = daemon.config, runtime, registry
        self.owner_domain = self.config.name
        self.config_digest = obligation_config_material_digest(self.config)
        self.context = json.loads(
            _json(
                {
                    "config_sha256": self.config.source_sha256,
                    "typed_config_digest": self.config_digest,
                    "registry_generation": registry.generation_digest,
                    "runtime_policy_scope": "held_effective",
                    "runtime_policy": {
                        "core_groups": runtime.core_groups,
                        "catalog_read_sensitivity": runtime.catalog_read_sensitivity,
                        "tool_exposure_policy": runtime.tool_exposure_policy,
                    },
                }
            )
        )
        self._pending = {}
        self._closed = False

    def _declaration(self, event_type):
        if (
            self.daemon.config is not self.config
            or self.daemon._tool_catalog != self.registry
            or self.config.source_material_sha256 != self.config_digest
            or obligation_config_material_digest(self.config) != self.config_digest
            or self.policy.butler_name != self.owner_domain
        ):
            raise SourceConflict("domain_registration_changed")
        matches = [
            s for s in self.config.subscriptions if s.kind == "domain" and s.source == event_type
        ]
        if len(matches) != 1 or matches[0].obligation.status != "valid":
            raise SourceConflict("domain_declaration_unavailable")
        spec = matches[0]
        resources = dict(
            build_obligation_catalog({self.owner_domain: self.registry}).resource_parameters
        )
        for grant in spec.obligation.declaration.capabilities:
            if grant.capability not in resources or set(dict(grant.resources)) - set(
                resources[grant.capability]
            ):
                raise SourceConflict("domain_declaration_unavailable")
        return spec

    async def _material(self, repository, task_id, event_id, event_type, source_butler, payload):
        """One explicit lock order: source, event, subscription, delivery, task.

        The owning inventory/command/source locks are acquired by the caller
        first. Public bus rows are the normative shared bus, never another
        domain's tables; exact own role/schema/shadow guards precede them.
        """
        c = repository.connection
        event = await c.fetchrow(
            """SELECT id,event_type,source_butler,payload,occurred_at,created_at
               FROM public.domain_events WHERE id=$1 FOR SHARE""",
            event_id,
        )
        subscription = await c.fetchrow(
            """SELECT id,subscriber_butler,event_type,active,created_at,updated_at
               FROM public.butler_subscriptions
               WHERE subscriber_butler=$1 AND event_type=$2 FOR SHARE""",
            self.owner_domain,
            event_type,
        )
        delivery = await c.fetchrow(
            """SELECT id,event_id,subscriber_butler,status,task_id,task_name
               FROM public.domain_event_deliveries
               WHERE event_id=$1 AND subscriber_butler=$2 FOR SHARE""",
            event_id,
            self.owner_domain,
        )
        event = _database_row(event, "id")
        subscription = _database_row(subscription, "id")
        delivery = _database_row(delivery, "id", "event_id", "task_id", nullable=("task_id",))
        if event is not None:
            event = dict(event)
            stored_payload = event["payload"]
            # The existing bus writer serializes JSON before passing it to
            # pools with a JSONB codec. Accept only its bounded object form;
            # malformed/scalar content cannot become a matching wake input.
            if type(stored_payload) is str:
                try:
                    if len(stored_payload.encode()) > 131072:
                        raise ValueError
                    event["payload"] = json.loads(stored_payload)
                except (ValueError, RecursionError):
                    raise SourceConflict("domain_event_mismatch") from None
            if type(event["payload"]) is not dict:
                raise SourceConflict("domain_event_mismatch")
            event["payload_storage_digest"] = digest(stored_payload)
        if (
            not event
            or event["id"] != event_id
            or event["event_type"] != event_type
            or event["source_butler"] != source_butler
            or _json(event["payload"]) != _json(payload)
        ):
            raise SourceConflict("domain_event_mismatch")
        if (
            not subscription
            or subscription["active"] is not True
            or subscription["subscriber_butler"] != self.owner_domain
            or subscription["event_type"] != event_type
        ):
            raise SourceConflict("domain_subscription_inactive")
        name = task_name_for(event_id, self.owner_domain)
        if (
            not delivery
            or delivery["event_id"] != event_id
            or delivery["subscriber_butler"] != self.owner_domain
            or delivery["status"] not in {"pending", "delivered"}
            or delivery["task_id"] not in {None, task_id}
            or delivery["task_name"] not in {None, name}
        ):
            raise SourceConflict("domain_delivery_mismatch")
        task = await c.fetchrow(
            f"SELECT id,source,{','.join(SCHEDULE_FIELDS)} "
            f'FROM "{self.owner_domain}".scheduled_tasks WHERE id=$1 FOR UPDATE',
            task_id,
        )
        task = _database_row(task, "id")
        prompt = _build_wake_task_prompt(
            event_id=event_id,
            event_type=event_type,
            source_butler=source_butler,
            subscriber_butler=self.owner_domain,
            payload=payload,
        )
        if task is None or not isinstance(task["until_at"], datetime):
            raise SourceConflict("domain_task_mismatch")
        target = task["until_at"] - timedelta(minutes=1)
        expected = dict.fromkeys(SCHEDULE_FIELDS)
        expected.update(
            name=name,
            cron=f"{target.minute} {target.hour} {target.day} {target.month} *",
            dispatch_mode="prompt",
            prompt=prompt,
            complexity="workhorse",
            enabled=True,
            timezone="UTC",
            continuity=False,
            task_type="cron",
            until_at=task["until_at"],
        )
        if (
            task["id"] != task_id
            or task["source"] != "db"
            or {key: task[key] for key in SCHEDULE_FIELDS} != expected
        ):
            raise SourceConflict("domain_task_mismatch")
        return {
            "event": dict(event),
            "subscription": dict(subscription),
            "delivery_id": str(delivery["id"]),
            "task": dict(task),
            "context": self.context,
        }

    async def _consume(self, task_id, *, event_id, event_type, source_butler, payload, read=False):
        spec = self._declaration(event_type)
        try:
            task_id, event_id = UUID(str(task_id)), UUID(str(event_id))
        except (TypeError, ValueError):
            raise SourceConflict("domain_identity_invalid") from None
        source_id = uuid5(NAMESPACE_URL, f"butlers:domain:{self.owner_domain}:{spec.name}")
        static = json.loads(
            _json({"subscription": spec.model_dump(mode="json"), "context": self.context})
        )
        material_digest = digest(static)
        command = uuid5(source_id, material_digest)
        token = uuid5(source_id, str(event_id))

        async def consume(repo):
            await repo.lock_git_inventory()
            await repo.read_command(command)
            await repo.lock_source(source_id)
            old = await repo.read(source_id)
            # Refuse held registration/config drift again inside the owning TX.
            self._declaration(event_type)
            material = await self._material(
                repo, task_id, event_id, event_type, source_butler, payload
            )
            if read and (old is None or old.material_digest != material_digest):
                raise SourceConflict("source_not_current")
            if old is None or old.material_digest != material_digest:
                value = DefinitionRevision(
                    self.owner_domain,
                    "subscription",
                    source_id,
                    old.generation + 1 if old else 1,
                    material_digest,
                    spec.obligation.declaration,
                    "git",
                    payload_json=_json(static),
                )
                value = await repo.revise(
                    command, value, expected_revision=old.revision if old else None
                )
            else:
                value = old
            if value.state != "active":
                raise SourceConflict("source_not_current")
            reference = {
                "event_id": event_id,
                "subscription_id": material["subscription"]["id"],
                "delivery_id": UUID(material["delivery_id"]),
                "task_id": task_id,
                "material_digest": digest(json.loads(_json(material))),
            }
            ref = value.source.event(event_id, feed=False)
            if read:
                row = await repo.connection.fetchrow(
                    "SELECT * FROM obligation_occurrences WHERE source_key=$1 FOR UPDATE", ref.key
                )
                if row is None:
                    raise SourceConflict("domain_capture_missing")
                occurrence = row["id"]
                await repo.check_event_reference(
                    source_id,
                    value.revision,
                    occurrence,
                    {"occurrence_id": str(occurrence)} | {k: str(v) for k, v in reference.items()},
                )
            else:
                occurrence = await repo.capture(
                    source_id, value.revision, event_id=event_id, event_reference=reference
                )
                if not await repo.claim(source_id, occurrence, token):
                    raise SourceConflict("claim_not_current")
            dark = await prepare_dark_submission(repo, source_id, occurrence, token)
            self._declaration(event_type)
            return DomainDarkSubmission(dark, _json(material))

        return await run_source_transaction(
            self.pool, self.owner_domain, consume, require_fresh=True
        )

    async def read_committed(self, task_id, **event):
        """Reread full current durable material; a DTO never substitutes for this."""
        return await self._operate(task_id, **event, read=True)

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

    async def prepare(self, task_id, **event):
        try:
            return await self._operate(task_id, **event)
        except Exception:
            logger.warning("Domain dark preparation unavailable")
            return None

    async def _operate(self, task_id, **event):
        if self._closed or self._pending:
            raise SourceConflict("domain_preparation_unavailable")
        task = asyncio.create_task(self._consume(task_id, **event), name="domain-dark-preparation")
        deadline = asyncio.get_running_loop().time() + PREPARATION_SECONDS
        self._pending[task] = deadline

        def done(completed):
            self._pending.pop(completed, None)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(done)
        interrupted = False
        try:
            await asyncio.wait({task}, timeout=max(0, deadline - asyncio.get_running_loop().time()))
        except asyncio.CancelledError:
            interrupted = True
        if interrupted or not task.done():
            task.cancel()
            interrupted |= await self._settle(task, deadline)
            if interrupted:
                raise asyncio.CancelledError
            from butlers.core.obligation_admission import SourceUnavailable

            raise SourceUnavailable("domain_preparation_unconfirmed")
        return task.result()

    async def aclose(self):
        self._closed = True
        interrupted = False
        for task, deadline in tuple(self._pending.items()):
            task.cancel()
            interrupted |= await self._settle(task, deadline)
        if any(not task.done() for task in self._pending):
            raise RuntimeError("domain_preparation_cleanup_incomplete")
        if interrupted:
            raise asyncio.CancelledError


def start_domain_sources(pool, daemon, runtime, registry, *, mode="off"):
    """Lifecycle-only held binding. OFF returns before inspecting any material."""
    if mode == "off":
        return None
    if mode != "dark":
        raise SourceConflict("invalid_domain_source_mode")
    from butlers.core.obligation_git_sources import capture_loaded_git_sources

    # The existing capture validates actual loader provenance, owner and held
    # policy/final registry. It performs no bus/source IO or fleet certification.
    capture_loaded_git_sources(daemon.config, runtime, registry)
    return DomainWakeRuntime(pool, daemon, runtime, registry)
