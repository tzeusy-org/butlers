"""Relationship-owned channel projection for custody COMMIT currentness.

This private constructor callback reads canonical facts on the actual guarded
domain connection. Neither a resolver DTO nor a channel selector authenticates
a sender. All canonical mapping writers must publish in their same transaction;
this module alone does not establish that integration or its SQL proof.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

import asyncpg
from opentelemetry.instrumentation.utils import suppress_instrumentation

from butlers.core.custody_admission import CustodyAdmission, CustodyWriter
from butlers.core.custody_native import (
    _installed_publishers,
)
from butlers.core.custody_native import (
    native_channel_mutation as native_channel_mutation,
)
from butlers.core.custody_native import (
    owning_binding_publisher as _native_binding_publisher,
)
from butlers.core.custody_source import CustodyError, digest, utc_timestamp
from butlers.identity import (
    _TELEGRAM_PREFIX_CHANNEL_TYPES,
    _owner_channel_authorization_candidates,
    canonical_identity_channel_type,
    channel_value_for_storage,
    normalize_email_sender,
    resolve_contacts_by_channel_bulk,
)

_OBSERVATION_LIFETIME = timedelta(minutes=5)
_MUTATION_ORIGIN_LIMIT = 2048
# asyncpg.Pool is not weak-referenceable. Actual runtime shutdown unregisters
# this allocation before closing its pool; a failed startup uses the same path.
# Registry lives in the dependency-light native hook used by operator commands.


def install_binding_publisher(pool: asyncpg.Pool, publisher: CustodyChannelBindings) -> None:
    """Private constructor allocation after genuine runtime enrollment.

    Native owning writers resolve by their actual pool object, never by an
    actor, role, schema string or request field. This registry supplies only a
    publisher callback; it is not request authentication or COMMIT permission.
    """
    if (
        type(publisher) is not CustodyChannelBindings
        or publisher._admission._pool is not pool
        or not publisher._admission._ready
    ):
        raise CustodyError("refused")
    current = _installed_publishers.get(pool)
    if current is not None and current is not publisher:
        raise CustodyError("conflict")
    _installed_publishers[pool] = publisher


def remove_binding_publisher(pool: asyncpg.Pool, publisher: CustodyChannelBindings) -> None:
    """Remove only after real pool closure; shutdown is refusal-only until then.

    Unit transport doubles have no live native pool/lifecycle authority. They
    retain explicit test teardown, which supplies no production shutdown proof.
    """
    if isinstance(pool, asyncpg.Pool) and not pool._closed:
        return
    if _installed_publishers.get(pool) is publisher:
        del _installed_publishers[pool]


def owning_binding_publisher(pool: asyncpg.Pool) -> CustodyChannelBindings | None:
    """Native code's fixed actual-pool lookup; never a model-facing operation."""
    return _native_binding_publisher(pool)


async def native_entity_write(
    pool: asyncpg.Pool,
    entity_ids: Iterable[uuid.UUID],
    method: str,
    statement: str,
    *values: Any,
    _fact_id: uuid.UUID | None = None,
) -> Any:
    """Native single-statement entity mutation, including its SAME publication.

    Method and SQL are fixed by native code, never an HTTP model. Existing
    unconfigured callers preserve their pool operation; configured callers
    acquire before control/entity locks and acknowledge the outer COMMIT before
    returning. A publication failure rolls the entity update back too.
    """
    if method not in {"execute", "fetchrow"}:
        raise CustodyError("invalid")
    publisher = owning_binding_publisher(pool)
    if publisher is None:
        return await getattr(pool, method)(statement, *values)
    async with pool.acquire() as connection:
        async with publisher._admission.bound_writer(connection) as writer:
            subjects = list(entity_ids)
            if _fact_id is not None:
                # The selected hash/id was resolved before this transaction.
                # Under the FIRST control lock, read its actual current subject
                # before entity locks; never lend the selected entity's old
                # callback to a fact moved by a concurrent merge.
                if type(_fact_id) is not uuid.UUID:
                    raise CustodyError("invalid")
                current_subject = await connection.fetchval(
                    "SELECT subject FROM relationship.entity_facts WHERE id=$1", _fact_id
                )
                if current_subject not in subjects:
                    raise CustodyError("conflict")
            async with publisher.bound_mutation(writer, subjects):
                result = await getattr(writer._connection, method)(statement, *values)
    return result


@asynccontextmanager
async def native_account_entity_mutation(
    pool: asyncpg.Pool,
    connection: asyncpg.Connection,
    provider: str,
    account_id: uuid.UUID,
    entity_id: uuid.UUID | None = None,
) -> AsyncIterator[asyncpg.Connection]:
    """Fixed native destructive account path, before its transaction/locks.

    A companion deletion cascades canonical identity facts. The configured API
    shared pool has its own zero-operation writer enrollment; it must publish
    those removals on this SAME connection, not invoke a peer after COMMIT.
    The first control lock precedes actual account relookup and entity locks.
    Soft disconnect and ordinary credential reads never enter this boundary.
    """
    if provider not in {"google", "steam"}:
        raise CustodyError("invalid")
    publisher = owning_binding_publisher(pool)
    if publisher is None:
        yield connection
        return
    async with publisher._admission.bound_writer(connection) as writer:
        current_entity = await connection.fetchval(
            f"SELECT entity_id FROM public.{provider}_accounts WHERE id=$1", account_id
        )
        if entity_id is not None and current_entity != entity_id:
            raise CustodyError("conflict")
        # Let the native owning registry retain its genuine not-found error.
        # A missing account has no existing entity to lock or republish.
        async with publisher.bound_mutation(
            writer, [] if current_entity is None else [current_entity]
        ):
            yield writer._connection


@asynccontextmanager
async def native_google_companion_creation(
    pool: asyncpg.Pool, connection: asyncpg.Connection, email: str | None
) -> AsyncIterator[asyncpg.Connection]:
    """Cover the actual Google companion UPSERT that replaces existing roles.

    Its fixed native canonical name is a selector, not proof. Read the existing
    active unique entity under FIRST control, then snapshot/publish the actual
    old/new facts on this transaction. A newly reserved companion has no old
    channel facts. No credential value is read or captured by this hook.
    """
    publisher = owning_binding_publisher(pool)
    if publisher is None:
        yield connection
        return
    async with publisher._admission.bound_writer(connection) as writer:
        subjects = []
        if email is not None:
            subjects = [
                row["id"]
                for row in await connection.fetch(
                    "SELECT id FROM public.entities WHERE canonical_name=$1 "
                    "AND entity_type='other' "
                    "AND metadata->>'merged_into' IS NULL AND metadata->>'deleted_at' IS NULL",
                    "google-account:" + email,
                )
            ]
        async with publisher.bound_mutation(writer, subjects):
            yield writer._connection


# Match the adopted core233 owner-channel ambiguity query, including its
# bounded phone suffix fallback. A stricter snapshot query could miss a foreign
# competing identity and falsely mint an owner for an ambiguous channel.
_LIVE_BINDING_ROWS = """
    SELECT DISTINCT ef.id,ef.subject,ef.predicate,ef.object,e.created_at,e.roles,
           COALESCE(ef."primary",false) AS is_primary,
           COALESCE((e.metadata->>'unidentified')='true',false) AS unidentified
    FROM relationship.entity_facts ef JOIN public.entities e ON e.id=ef.subject
    WHERE ef.object_kind='literal' AND ef.validity='active'
      AND e.metadata->>'merged_into' IS NULL AND e.metadata->>'deleted_at' IS NULL
      AND EXISTS(SELECT FROM pg_catalog.unnest($1::text[],$2::text[]) w(predicate,value)
        WHERE (ef.predicate=w.predicate AND pg_catalog.lower(pg_catalog.btrim(ef.object))=w.value)
          OR (w.predicate='phone-digits' AND ef.predicate='has-phone'
              AND w.value ~ '^[0-9]+$' AND pg_catalog.length(w.value)>=8
              AND pg_catalog.length(pg_catalog.regexp_replace(ef.object,'\\D','','g'))>=8
              AND pg_catalog.abs(pg_catalog.length(
                    pg_catalog.regexp_replace(ef.object,'\\D','','g'))-pg_catalog.length(w.value))<=2
              AND (pg_catalog.regexp_replace(ef.object,'\\D','','g') LIKE '%'||w.value
                OR w.value LIKE '%'||pg_catalog.regexp_replace(ef.object,'\\D','','g'))))
    ORDER BY ef.subject,ef.id
"""


def _canonical_origin(channel_type: str, channel_value: str) -> tuple[str, str]:
    if not _owner_channel_authorization_candidates(channel_type, channel_value):
        raise CustodyError("invalid")
    channel = canonical_identity_channel_type(channel_type)
    if channel == "email":
        value = normalize_email_sender(channel_value)
    elif channel in _TELEGRAM_PREFIX_CHANNEL_TYPES:
        channel = "telegram"
        value = (
            channel_value_for_storage(channel, channel_value.strip())
            .removeprefix("telegram:")
            .lower()
        )
    else:
        # The shared shape validator admitted only an individual WhatsApp JID.
        user, service = channel_value.strip().lower().split("@")
        value = user.split(":", 1)[0] + "@" + service
    return channel, value


def channel_origin_digest(channel_type: str, channel_value: str) -> str:
    """Linkable typed selector; never authority or an anonymity guarantee."""
    channel, value = _canonical_origin(channel_type, channel_value)
    return digest({"kind": "owner-channel.v1", "channel": channel, "value": value})


def origins_affected_by_facts(rows: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """Derive the complete typed resolver universe of actual old/new facts.

    This is a selector calculation, not owner evidence. A phone can match a
    WhatsApp sender with up to two extra or missing leading digits under the
    adopted resolver; updating a competing foreign phone must invalidate those
    same origins too. No mutable state index or caller's selected subset can
    supply completeness. The owning writer reads the rows itself.
    """
    origins: set[tuple[str, str]] = set()
    for row in rows:
        value = row["object"]
        if not isinstance(value, str):
            continue
        value = value.strip()
        candidates: list[tuple[str, str]] = []
        if row["predicate"] == "has-email":
            candidates.append(("email", value))
        elif row["predicate"] == "has-handle":
            candidates.extend((("telegram", value), ("whatsapp_jid", value)))
        elif row["predicate"] == "has-phone":
            digits = re.sub(r"\D", "", value)
            if len(digits) >= 8:
                variants = {digits}
                for extra in (1, 2):
                    if len(digits) - extra >= 8:
                        variants.add(digits[extra:])
                    variants.update(f"{prefix:0{extra}d}{digits}" for prefix in range(10**extra))
                candidates.extend(("whatsapp_jid", item + "@s.whatsapp.net") for item in variants)
        for channel, identifier in candidates:
            if _owner_channel_authorization_candidates(channel, identifier):
                origins.add(_canonical_origin(channel, identifier))
        if len(origins) > _MUTATION_ORIGIN_LIMIT:
            raise CustodyError("unavailable")
    return sorted(origins, key=lambda item: channel_origin_digest(*item))


class CustodyChannelBindings:
    """Fixed owning producer; never exposed as a model tool or public DTO."""

    def __init__(self, admission: CustodyAdmission) -> None:
        if admission.profile.actor != "relationship":
            raise CustodyError("refused")
        self._admission = admission

    @asynccontextmanager
    async def native_mutation(
        self, connection: asyncpg.Connection, entity_ids: Iterable[uuid.UUID]
    ) -> AsyncIterator[CustodyWriter]:
        """Reuse a genuine control-first writer or establish it before locks.

        An arbitrary already-open native transaction cannot be repaired by a
        late savepoint or pool verdict. Its caller must move this outer adapter
        before starting that transaction. No source/authority is inferred here.
        """
        writer = self._admission._writer_objects.get(id(connection))
        if writer is not None:
            async with self.bound_mutation(writer, entity_ids):
                yield writer
        else:
            async with self.mutation(connection, entity_ids) as writer:
                yield writer

    async def observe_channels(self, channel_type: str, channel_values: list[str]) -> dict:
        """Owning resolver callback; publish before returning its existing DTO.

        Installed only by Relationship's fixed constructor at its registered
        identity_resolve_channels tool. Inputs remain selectors, never source
        grants. Callers run this MCP step OUTSIDE their business transaction;
        no cross-process I/O is allowed while custody control locks are held.
        The reply exposes neither source/birth/version nor verifier authority.
        """
        if (
            type(channel_values) is not list
            or not 1 <= len(channel_values) <= 256
            or any(type(value) is not str for value in channel_values)
        ):
            raise CustodyError("invalid")
        pairs = [(channel_type, value) for value in channel_values]
        # Validate the complete requested universe before any owning mutation.
        origins = {_canonical_origin(*pair) for pair in pairs}
        try:
            async with self._admission.writer() as writer:
                for channel, value in sorted(
                    origins, key=lambda item: channel_origin_digest(*item)
                ):
                    await self.publish_current(writer, channel, value)
                with suppress_instrumentation():
                    resolved = await resolve_contacts_by_channel_bulk(
                        writer._connection, pairs, raise_on_error=True
                    )
                result = {}
                for pair, contact in resolved.items():
                    result[pair[1]] = (
                        None
                        if contact is None
                        else {
                            "name": contact.name,
                            "roles": list(contact.roles),
                            "entity_id": None
                            if contact.entity_id is None
                            else str(contact.entity_id),
                            "is_unidentified": contact.is_unidentified,
                        }
                    )
            # The current projection and resolver read share a committed owning
            # transaction. Its reply is still only a locator for source capture.
            return result
        except CustodyError:
            raise
        except Exception:
            # The existing strict resolver intentionally supplies content-free
            # failures; do not retain provider/raw-identity error details here.
            raise CustodyError("unavailable") from None

    @asynccontextmanager
    async def mutation(
        self, connection: asyncpg.Connection, entity_ids: Iterable[uuid.UUID]
    ) -> AsyncIterator[CustodyWriter]:
        """Wrap a native writer BEFORE it starts any domain transaction/locks.

        The fixed native writer supplies its actual complete subject/merge
        batch, including new server-reserved entities. Old rows are captured
        before destructive deletion; new rows are read after the write. Native
        functions receive writer._connection and use savepoints if needed.
        This callback does not itself wire or prove any native caller.
        """
        async with self._admission.bound_writer(connection) as writer:
            async with self.bound_mutation(writer, entity_ids):
                yield writer

    @asynccontextmanager
    async def bound_mutation(
        self, writer: CustodyWriter, entity_ids: Iterable[uuid.UUID]
    ) -> AsyncIterator[CustodyWriter]:
        """Reuse an admitted SAME transaction; never open another checkout.

        An outer registered guard can already own the writer and its first
        auth/control lock. The fixed native writer must pass that exact object,
        not construct one from a connection or from a model/context payload.
        Failure while publishing rolls back through the owning outer context.
        """
        connection = writer._connection
        self._require_writer(writer)
        supplied = list(entity_ids)
        if any(type(subject) is not uuid.UUID for subject in supplied):
            raise CustodyError("invalid")
        subjects = sorted(set(supplied))
        try:
            with suppress_instrumentation():
                await connection.fetch(
                    "SELECT id FROM public.entities WHERE id=ANY($1::uuid[]) "
                    "ORDER BY id FOR UPDATE",
                    subjects,
                )
                before = await self._read_subject_channels(connection, subjects)
                # Check representability before exposing the mutation.
                origins_affected_by_facts(before)
            yield writer
            self._require_writer(writer)
            with suppress_instrumentation():
                after = await self._read_subject_channels(connection, subjects)
                origins = origins_affected_by_facts([*before, *after])
                for channel, value in origins:
                    await self.publish_current(writer, channel, value)
        except asyncpg.PostgresError as exc:
            code = "unknown" if exc.sqlstate and exc.sqlstate.startswith("08") else "unavailable"
            raise CustodyError(code) from None
        except (OSError, asyncpg.InterfaceError):
            raise CustodyError("unknown") from None

    def _require_writer(self, writer: CustodyWriter) -> None:
        # Check object identity, not only the connection's membership: a
        # caller-created wrapper around a live connection is not the bound
        # capability yielded by the constructor-owned admission context.
        if not self._admission.owns_writer(writer):
            raise CustodyError("refused")

    @staticmethod
    async def _read_subject_channels(connection: asyncpg.Connection, subjects: list) -> list:
        return await connection.fetch(
            "SELECT predicate,object FROM relationship.entity_facts "
            "WHERE subject=ANY($1::uuid[]) AND object_kind='literal' "
            "AND validity='active' AND predicate=ANY($2::text[]) ORDER BY subject,id",
            subjects,
            ["has-email", "has-handle", "has-phone"],
        )

    async def publish_current(
        self, writer: CustodyWriter, channel_type: str, channel_value: str
    ) -> dict:
        """Read/publish current or explicitly unbound state before SAME COMMIT.

        A fresh genuine owning observation may renew an expired projection with
        a new source revision. Replaying an existing accepted report MUST reuse
        its frozen association; it cannot call this to refresh that old report.
        The callback has no caller-supplied owner, fact body, version or expiry.
        """
        connection = writer._connection
        self._require_writer(writer)
        channel, value = _canonical_origin(channel_type, channel_value)
        origin = channel_origin_digest(channel, value)
        candidates = _owner_channel_authorization_candidates(channel, value)
        pairs = [value.split(":", 1) for value in candidates]
        try:
            with suppress_instrumentation():
                rows = await connection.fetch(
                    _LIVE_BINDING_ROWS,
                    [p[0] for p in pairs],
                    [p[1] for p in pairs],
                )
                # Global custody control was locked by connection_finish FIRST.
                # Owning fact writers must enter that same boundary too; the
                # entity lock additionally orders owner-role/delete changes.
                entity_ids = sorted({row["subject"] for row in rows})
                if entity_ids:
                    await connection.fetch(
                        "SELECT id FROM public.entities WHERE id=ANY($1::uuid[]) "
                        "ORDER BY id FOR UPDATE",
                        entity_ids,
                    )
                    # Read roles/lifetime again after any entity-lock wait.
                    return await self._publish_locked(writer, origin, pairs)
                return await self._publish_locked(writer, origin, pairs)
        except asyncpg.PostgresError as exc:
            code = "unknown" if exc.sqlstate and exc.sqlstate.startswith("08") else "unavailable"
            raise CustodyError(code) from None
        except (OSError, asyncpg.InterfaceError):
            raise CustodyError("unknown") from None

    async def _publish_locked(self, writer: CustodyWriter, origin: str, pairs: list) -> dict:
        connection = writer._connection
        # The second read captures the state after entity locking. Empty or
        # ambiguous mappings publish an explicit authority-removing version.
        rows = await connection.fetch(
            _LIVE_BINDING_ROWS,
            [p[0] for p in pairs],
            [p[1] for p in pairs],
        )
        entities = {row["subject"] for row in rows}
        owner = None
        if len(entities) == 1 and all(
            "owner" in (row["roles"] or []) and not row["unidentified"] for row in rows
        ):
            owner = str(next(iter(entities)))
        snapshot = [
            {
                "fact_id": str(row["id"]),
                "subject": str(row["subject"]),
                "fact_digest": digest({"predicate": row["predicate"], "value": row["object"]}),
                "entity_created_at": utc_timestamp(row["created_at"]),
                "roles": sorted(set(row["roles"] or [])),
                "unidentified": row["unidentified"],
                "is_primary": row["is_primary"],
            }
            for row in rows
        ]
        content = digest({"origin_digest": origin, "facts": snapshot, "owner": owner})
        now = await connection.fetchval("SELECT clock_timestamp()")
        key = "custody-origin:" + origin
        previous = await connection.fetchrow(
            "SELECT value,version FROM relationship.state WHERE key=$1 FOR UPDATE", key
        )
        version = previous["version"] if previous is not None else 0

        def observed_projection(revision: int, expires: str) -> dict:
            return {
                "locator": "identity:" + origin,
                "revision": revision,
                "content_digest": content,
                "origin_digest": origin,
                "owner_entity_id": owner,
                "issuer_target": None,
                "target_set": [],
                "target_set_version": revision,
                "expires_at": expires,
                "intent": "identity_binding",
            }

        # The ordinary state API can write arbitrary values. That storage is a
        # version/dedup hint, never a trusted source projection. Reconstruct ALL
        # authority fields from this read, and reuse only an exact bounded match.
        saved = previous["value"] if previous is not None else None
        projection = None
        if isinstance(saved, dict) and isinstance(saved.get("expires_at"), str):
            try:
                expires = datetime.fromisoformat(saved["expires_at"].replace("Z", "+00:00"))
                if now < expires <= now + _OBSERVATION_LIFETIME:
                    expected = observed_projection(version, utc_timestamp(expires))
                    if saved == expected:
                        projection = expected
            except (ValueError, TypeError, CustodyError):
                pass
        if projection is None:
            # A genuine new canonical read/version is distinct from replaying an
            # old admitted report. Never copy the current version into that report.
            version += 1
            projection = observed_projection(version, utc_timestamp(now + _OBSERVATION_LIFETIME))
            await connection.execute(
                """
                INSERT INTO relationship.state(key,value,version,updated_at)
                VALUES($1,$2::jsonb,$3,clock_timestamp())
                ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,
                    version=EXCLUDED.version,updated_at=EXCLUDED.updated_at
                """,
                key,
                projection,
                version,
            )
        return await writer.register_source("domain_evidence", projection)
