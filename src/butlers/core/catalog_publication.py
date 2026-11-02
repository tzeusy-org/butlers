"""Role-owned, private D4 definition publication; never a model tool or grant.

Only public descriptor metadata crosses domains here. The database role is the
publisher identity. Administrative membership and activation are not runtime
writable. Admission/broker readers perform no network calls under their locks.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import fields
from uuid import UUID, uuid4

import asyncpg

from butlers.core.goal_admission import EvidenceRefused
from butlers.core.tool_catalog import (
    AUTHORIZATION_FIELDS,
    RecipientParameters,
    ToolCatalog,
    ToolDescriptor,
    _freeze,
    validate_authorization_catalog,
)
from butlers.core.tool_presentation_inventory import STORE_WRITERS

LEASE_SECONDS = 90
RENEW_SECONDS = 30
TRANSACTION_SECONDS = 5


def plain(value):
    if isinstance(value, RecipientParameters):
        return plain(value.as_mapping())
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [plain(item) for item in value]
    return value


def canonical(value) -> bytes:
    return json.dumps(
        plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def generation(descriptors) -> str:
    # Identical to build_tool_catalog's definition projection, including ALL
    # authorization metadata. Raw schema/description are bound by their digests.
    return digest(
        [
            {
                key: value
                for key, value in item.items()
                if key not in {"input_schema", "description"}
            }
            for item in descriptors
        ]
    )


def publication(catalog: ToolCatalog, domain: str) -> dict:
    """Validate the actual immutable final registry, not a caller's raw hash."""
    if type(catalog) is not ToolCatalog or not catalog.classification_complete or not catalog:
        raise EvidenceRefused("catalog_unavailable")
    try:
        validate_authorization_catalog(catalog, store_writers=STORE_WRITERS)
    except ValueError:
        raise EvidenceRefused("catalog_unavailable") from None
    items = [
        {field.name: plain(getattr(item, field.name)) for field in fields(ToolDescriptor)}
        for item in sorted(catalog.descriptors, key=lambda item: item.canonical_name)
    ]
    if any(item["llm_presentable"] and item["owner_domain"] != domain for item in items):
        raise EvidenceRefused("catalog_unavailable")
    if catalog.generation_digest != generation(items):
        raise EvidenceRefused("catalog_unavailable")
    return {
        "version": 1,
        "domain": domain,
        "generation_digest": catalog.generation_digest,
        "descriptors": items,
    }


def decode_publication(value: object, domain: str) -> ToolCatalog:
    """Recompute every definition and digest, rejecting unknown/partial shapes."""
    try:
        if (
            type(value) is not dict
            or set(value) != {"version", "domain", "generation_digest", "descriptors"}
            or type(value["version"]) is not int
            or value["version"] != 1
            or value["domain"] != domain
            or type(value["descriptors"]) is not list
        ):
            raise ValueError
        decoded = []
        expected = {field.name for field in fields(ToolDescriptor)}
        for item in value["descriptors"]:
            if type(item) is not dict or set(item) != expected:
                raise ValueError
            if (
                type(item["description"]) is not str
                or type(item["input_schema"]) is not dict
                or type(item["llm_presentable"]) is not bool
                or type(item["classification_complete"]) is not bool
                or digest(item["input_schema"]) != item["input_schema_digest"]
                or digest(item["description"]) != item["description_digest"]
            ):
                raise ValueError
            kwargs = dict(item)
            for key in ("resource_params", "safety_args", "read_stores", "write_stores"):
                if kwargs[key] is not None:
                    if type(kwargs[key]) is not list:
                        raise ValueError
                    kwargs[key] = tuple(kwargs[key])
            for key in ("input_schema", "approval_policy", "arg_sensitivities", "selection_bound"):
                kwargs[key] = _freeze(kwargs[key])
            if kwargs["recipient_params"] is not None:
                recipient = dict(kwargs["recipient_params"])
                recipient["argument_names"] = tuple(recipient["argument_names"])
                kwargs["recipient_params"] = RecipientParameters(**recipient)
            decoded.append(ToolDescriptor(**kwargs))
        catalog = ToolCatalog(tuple(decoded), True, value["generation_digest"])
        if publication(catalog, domain) != value:
            raise ValueError
        return catalog
    except (TypeError, ValueError, KeyError, AttributeError):
        raise EvidenceRefused("catalog_unavailable") from None


def authorization_digest(catalog: ToolCatalog) -> str:
    return digest(
        [
            {key: plain(getattr(item, key)) for key in AUTHORIZATION_FIELDS}
            for item in catalog.descriptors
        ]
    )


async def verify_role(connection, domain: str) -> None:
    role = await connection.fetchrow(
        "SELECT current_user AS name, rolsuper, rolbypassrls FROM pg_roles "
        "WHERE rolname=current_user"
    )
    if (
        role is None
        or role["name"] != f"butler_{domain}_rw"
        or role["rolsuper"]
        or role["rolbypassrls"]
    ):
        raise EvidenceRefused("catalog_unavailable")
    if await connection.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname=ANY($1::text[]) "
        "AND pg_has_role(current_user,c.relowner,'MEMBER'))",
        [
            "capability_catalog_control",
            "capability_catalog_members",
            "capability_catalog_current",
            "capability_catalog_history",
        ],
    ):
        raise EvidenceRefused("catalog_unavailable")
    # Match core275's maintenance contract: ordinary backups use the stable
    # database owner; runtimes must remain non-owners subject to enabled RLS.
    # Inspect actual catalog rows so missing/retargeted/disabled tables refuse.
    tables = (
        "capability_catalog_control",
        "capability_catalog_members",
        "capability_catalog_current",
        "capability_catalog_history",
        "capability_catalog_installations",
    )
    posture = await connection.fetch(
        "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,"
        "c.relowner=d.datdba AS database_owned,"
        "pg_has_role(current_user,d.datdba,'MEMBER') AS owner_member "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "CROSS JOIN pg_database d WHERE n.nspname='public' AND c.relkind='r' "
        "AND d.datname=current_database() AND c.relname=ANY($1::text[])",
        tables,
    )
    if len(posture) != len(tables) or {row["relname"] for row in posture} != set(tables):
        raise EvidenceRefused("catalog_unavailable")
    if any(
        row["relrowsecurity"] is not True
        or row["relforcerowsecurity"] is not False
        or row["database_owned"] is not True
        or row["owner_member"] is not False
        for row in posture
    ):
        raise EvidenceRefused("catalog_unavailable")


class CatalogPublisher:
    """One daemon boot's CAS-fenced publication/lease; no caller authority flag."""

    def __init__(self, pool, domain: str):
        self.pool, self.domain = pool, domain
        self.instance = uuid4()
        self.revision: UUID | None = None
        self.generation_digest: str | None = None
        self._publication_pending = False

    async def publish(self, catalog: ToolCatalog) -> UUID:
        if self._publication_pending:
            raise EvidenceRefused("catalog_unavailable")
        body = publication(catalog, self.domain)
        expected_revision = self.revision
        revision = uuid4()
        # A commit-response loss never becomes a success or blind new revision.
        # resolve() is the exact read-only reconciliation path for this ID.
        self._publication_pending = True
        self.revision = revision
        self.generation_digest = catalog.generation_digest
        try:
            async with asyncio.timeout(TRANSACTION_SECONDS):
                async with self.pool.acquire() as connection, connection.transaction():
                    await verify_role(connection, self.domain)
                    await connection.execute(
                        "SELECT public.publish_capability_catalog($1,$2,$3,$4::jsonb,$5,$6,$7,$8)",
                        self.domain,
                        self.instance,
                        revision,
                        body,
                        digest(body),
                        authorization_digest(catalog),
                        LEASE_SECONDS,
                        expected_revision,
                    )
        except asyncpg.CheckViolationError:
            raise EvidenceRefused("catalog_unavailable") from None
        self._publication_pending = False
        return revision

    async def resolve(self, connection) -> bool:
        await verify_role(connection, self.domain)
        resolved = await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM public.capability_catalog_current "
            "WHERE domain=$1 AND instance_id=$2 AND revision_id=$3 AND active "
            "AND expires_at>clock_timestamp())",
            self.domain,
            self.instance,
            self.revision,
        )
        if resolved is True:
            self._publication_pending = False
        return resolved

    async def renew(self) -> None:
        async with asyncio.timeout(TRANSACTION_SECONDS):
            async with self.pool.acquire() as connection, connection.transaction():
                await verify_role(connection, self.domain)
                changed = await connection.fetchval(
                    "SELECT public.renew_capability_catalog($1,$2,$3,$4)",
                    self.domain,
                    self.instance,
                    self.revision,
                    LEASE_SECONDS,
                )
                if changed is not True:
                    raise EvidenceRefused("catalog_unavailable")

    async def retire(self) -> None:
        # Retire this boot, including its last committed revision after a failed
        # replacement. The instance fence protects every successor boot.
        async with asyncio.timeout(TRANSACTION_SECONDS):
            async with self.pool.acquire() as connection, connection.transaction():
                await verify_role(connection, self.domain)
                await connection.execute(
                    "UPDATE public.capability_catalog_current SET active=false, "
                    "updated_at=clock_timestamp() WHERE domain=$1 AND instance_id=$2",
                    self.domain,
                    self.instance,
                )


async def start_catalog_publication(daemon, pool) -> None:
    """Final startup hook. Missing migration/OFF performs no publication/task I/O."""
    if getattr(daemon, "_catalog_publication", None) is not None:
        raise EvidenceRefused("catalog_unavailable")
    async with asyncio.timeout(TRANSACTION_SECONDS):
        exists = await pool.fetchval("SELECT to_regclass('public.capability_catalog_control')")
        if exists is None:
            return
        enabled = await pool.fetchval(
            "SELECT enabled FROM public.capability_catalog_control WHERE singleton"
        )
    if enabled is not True:
        return
    if getattr(daemon, "_tool_registration_failures", None) or any(
        status.status != "active" for status in daemon._module_statuses.values()
    ):
        raise EvidenceRefused("catalog_unavailable")
    owner = CatalogPublisher(pool, daemon.config.name)
    # Retain the operation identity before I/O so partial startup / a lost
    # commit reply can be reconciled or retired by the actual lifecycle owner.
    daemon._catalog_publication = owner
    daemon._catalog_publication_close_task = None
    daemon._catalog_publication_failed = False
    await owner.publish(daemon.tool_catalog)

    async def renew():
        # Stop renewing immediately on failure. The server-clock lease expires;
        # an old process cannot renew a replacement boot's pointer.
        try:
            while True:
                await asyncio.sleep(RENEW_SECONDS)
                if daemon.tool_catalog.generation_digest != owner.generation_digest:
                    raise EvidenceRefused("catalog_unavailable")
                await owner.renew()
        except asyncio.CancelledError:
            raise
        except Exception:
            # The failed renewal never extends freshness. If retirement is
            # unavailable too, the finite server-clock lease still expires.
            daemon._catalog_publication_failed = True
            try:
                await owner.retire()
            except Exception:
                pass  # Unavailable, not a successful retirement; no raw diagnostics.

    daemon._catalog_publication_task = asyncio.create_task(
        renew(), name=f"catalog-renew-{daemon.config.name}"
    )


async def close_catalog_publication(daemon) -> None:
    """Retain one bounded cleanup task across repeated external cancellation.

    Slot clearing follows successful task settlement AND durable retirement;
    cancellation/error never means closed. No repeated call resets the bound.
    """
    owner = getattr(daemon, "_catalog_publication", None)
    worker = getattr(daemon, "_catalog_publication_task", None)
    if owner is None and worker is None:
        return
    cleanup = getattr(daemon, "_catalog_publication_close_task", None)
    if cleanup is None:

        async def settle():
            async with asyncio.timeout(TRANSACTION_SECONDS):
                if worker is not None:
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)
                if owner is not None:
                    await owner.retire()
                daemon._catalog_publication_task = None
                daemon._catalog_publication = None

        cleanup = asyncio.create_task(settle(), name="catalog-publication-close")
        daemon._catalog_publication_close_task = cleanup
    interrupted = False
    try:
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                interrupted = True
        cleanup.result()
    finally:
        if interrupted:
            raise asyncio.CancelledError
