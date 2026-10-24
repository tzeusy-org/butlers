"""Private Chronicler reduced-material staging; never narration authority.

No endpoint, model turn, native writer, verified catalog, creator or admission is
provided. An empty Git declaration intentionally authorizes no capability.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from uuid import UUID, uuid5
from weakref import WeakKeyDictionary
from zoneinfo import ZoneInfo

from asyncpg import Pool

from butlers.core.goal_admission import digest
from butlers.core.obligation_admission import (
    DarkSubmission,
    SourceUnavailable,
    prepare_dark_submission,
    run_source_transaction,
)
from butlers.core.obligation_sources import (
    DefinitionRevision,
    ObligationSourceRepository,
    SourceConflict,
)
from butlers.core.obligations import ObligationDeclaration

JOB = "chronicler_narrate_daily"
KIND = "chronicler_narration_v1"
PREPARATION_SECONDS = 5
_SCHEDULE_FIELDS = "id,name,cron,dispatch_mode,prompt,job_name,job_args,enabled,timezone,source"
_OWNERS = WeakKeyDictionary()
type _RegistrationReader = Callable[[], tuple[ObligationDeclaration, dict[str, object]]]
_registration_reader: _RegistrationReader | None = None


class _ChroniclerPreparationOwner:
    """Retain incomplete work/release; never renew its original caller deadline."""

    def __init__(self):
        # Healthy operations may overlap; only incomplete cancelled/expired
        # workers quarantine this pool until their own release settles.
        self._pending = {}
        self._quarantined = set()
        self._closed = False

    async def _settle(self, task, deadline):
        cancelled = False
        while not task.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait({task}, timeout=remaining)
            except asyncio.CancelledError:
                cancelled = True
                if not task.done():
                    self._quarantined.add(task)
                # The first cancellation already initiated transaction unwind.
                # Repeated caller cancellation must not interrupt pool release.
                if not task.cancelling():
                    task.cancel()
        return cancelled

    async def run(self, operation):
        if self._closed or self._quarantined:
            raise SourceUnavailable("chronicler_preparation_unavailable")
        deadline = asyncio.get_running_loop().time() + PREPARATION_SECONDS
        task = asyncio.create_task(operation(), name="chronicler-source-preparation")
        self._pending[task] = deadline

        def finished(done):
            self._pending.pop(done, None)
            self._quarantined.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        cancelled = await self._settle(task, deadline)
        if cancelled or not task.done():
            if not task.done():
                self._quarantined.add(task)
            if not task.done() and not task.cancelling():
                task.cancel()
            cancelled |= await self._settle(task, deadline)
            if cancelled:
                raise asyncio.CancelledError
            raise SourceUnavailable("chronicler_preparation_unconfirmed")
        # run_source_transaction returns only after confirmed COMMIT and release.
        return task.result()

    async def aclose(self):
        self._closed = True
        cancelled = False
        for task, deadline in tuple(self._pending.items()):
            if not task.done() and not task.cancelling():
                task.cancel()
            cancelled |= await self._settle(task, deadline)
        # Done callbacks may not have run yet; observe their final exceptions.
        for task in tuple(self._pending):
            if task.done():
                self._pending.pop(task, None)
                self._quarantined.discard(task)
                if not task.cancelled():
                    task.exception()
        if self._pending:
            raise RuntimeError("chronicler_preparation_cleanup_incomplete")
        if cancelled:
            raise asyncio.CancelledError


def chronicler_preparation_owner(pool, *, create=False):
    """Fixed server lifecycle lookup; OFF/partial startup creates no owner or IO."""
    if pool is None:
        return None
    # Native asyncpg Pool is slotted without weakrefs. Its initialized queue is
    # unique to that physical pool and lives through close. Weak-key the queue,
    # never the pool: the registry cannot keep a discarded pool graph alive.
    # Pending workers retain their acquisition/pool, hence that SAME queue and
    # owner, through transaction and release settlement.
    key = pool._queue if isinstance(pool, Pool) else pool
    if key is None:
        if create:
            raise SourceUnavailable("chronicler_pool_uninitialized")
        return None
    try:
        owner = _OWNERS.get(key)
    except TypeError:
        if create:
            raise
        # Partial-startup collaborators without an actual pool have no owner.
        return None
    if owner is None and create:
        owner = _OWNERS[key] = _ChroniclerPreparationOwner()
    return owner


class _ReleaseOwnedPool:
    """Keep the actual acquisition's release owned until its holder settles."""

    def __init__(self, pool):
        self.pool = pool

    @asynccontextmanager
    async def acquire(self):
        acquisition = self.pool.acquire()
        connection = await acquisition.__aenter__()
        try:
            yield connection
        finally:
            # asyncpg shields holder reset internally. Retain the WHOLE release
            # await so cancellation cannot finish our worker before that reset.
            release = asyncio.create_task(
                acquisition.__aexit__(*sys.exc_info()), name="chronicler-source-release"
            )
            cancelled = False
            while not release.done():
                try:
                    await asyncio.shield(release)
                except asyncio.CancelledError:
                    cancelled = True
            release.result()
            if cancelled:
                raise asyncio.CancelledError


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
        raise SourceConflict("chronicler_material_invalid") from None


def _bind_chronicler_registration_reader(reader: _RegistrationReader) -> _RegistrationReader:
    """Private server assembly hook; binding performs no source/config or DB IO."""
    global _registration_reader
    _registration_reader = reader
    return reader


def _registration():
    """Re-read through the real resolver held by server assembly, never a DTO."""
    if _registration_reader is None:
        raise SourceConflict("chronicler_registration_unavailable")
    try:
        return _registration_reader()
    except SourceConflict:
        raise
    except (StopIteration, KeyError, TypeError, ValueError, OSError):
        raise SourceConflict("chronicler_registration_unavailable") from None


def configured_chronicler_preparer(pool, job_args):
    """Server-only selector: OFF has zero source/config/catalog IO.

    Job args cannot select/enable staging. This independent environment switch
    does not enable narration or admission and is never returned to a model.
    """
    if os.environ.get("BUTLERS_CHRONICLER_DARK_PREPARATION") != "1":
        return None
    return ChroniclerPreparation(pool, json.loads(_json(job_args)))


def narration_material(
    *, local_date, timezone, rollup_rows, flag_rows, top_episode_titles, user_message, system_prompt
):
    """Capture the actual reduced input; exclude only output narrative fields."""
    from butlers.chronicler.narration import (
        _local_day_bounds_utc,
        build_narration_prompt,
        should_skip_narration,
    )

    if type(local_date) is not date or type(timezone) is not str:
        raise SourceConflict("chronicler_day_invalid")
    start, end = _local_day_bounds_utc(local_date, ZoneInfo(timezone))
    if should_skip_narration(rollup_rows, flag_rows)[0]:
        raise SourceConflict("chronicler_material_skipped")
    rows = []
    for group in (rollup_rows, flag_rows):
        values = []
        for row in group:
            if row.local_date != local_date or not isinstance(row.id, UUID):
                raise SourceConflict("chronicler_row_invalid")
            value = asdict(row)
            for field in ("narrative", "computed_at", "created_at"):
                value.pop(field, None)
            values.append(value)
        rows.append(values)
    if user_message != build_narration_prompt(
        local_date=local_date,
        rollup_rows=rollup_rows,
        flag_rows=flag_rows,
        top_episode_titles=top_episode_titles,
    ):
        raise SourceConflict("chronicler_prompt_changed")
    return json.loads(
        _json(
            {
                "local_date": local_date,
                "timezone": timezone,
                "day_start_utc": start,
                "day_end_utc": end,
                "rollups": rows[0],
                "flags": rows[1],
                "top_episode_titles": top_episode_titles,
                "user_message": user_message,
                "system_prompt": system_prompt,
            }
        )
    )


async def _current_material(connection, local_date, timezone):
    from butlers.chronicler.narration import (
        _SYSTEM_PROMPT,
        _fetch_top_episode_titles,
        _local_day_bounds_utc,
        build_narration_prompt,
    )
    from butlers.chronicler.storage import list_daily_rollup_flags, list_daily_rollups

    start, end = _local_day_bounds_utc(local_date, ZoneInfo(timezone))
    if await connection.fetchval("SELECT clock_timestamp()") < end:
        raise SourceConflict("chronicler_day_not_closed")
    rollups = await list_daily_rollups(connection, local_date=local_date)
    flags = await list_daily_rollup_flags(connection, local_date=local_date)
    titles = await _fetch_top_episode_titles(connection, day_start_utc=start, day_end_utc=end)
    return narration_material(
        local_date=local_date,
        timezone=timezone,
        rollup_rows=rollups,
        flag_rows=flags,
        top_episode_titles=titles,
        user_message=build_narration_prompt(
            local_date=local_date, rollup_rows=rollups, flag_rows=flags, top_episode_titles=titles
        ),
        system_prompt=_SYSTEM_PROMPT,
    )


async def _locked_source(repository, *, expected_row=None):
    # Repository inventory/command/source locks MUST already be held. Fixed own
    # tables, including view dependencies, block writers/phantoms through COMMIT;
    # no FOR UPDATE of an aggregate view or cross-domain lock is substituted.
    await repository._guard()
    connection = repository.connection
    shadow = await connection.fetchval(
        "SELECT EXISTS(SELECT 1 FROM pg_class WHERE relnamespace=pg_my_temp_schema() "
        "AND relname=ANY($1::text[]))",
        [
            "daily_rollups",
            "daily_rollup_flags",
            "v_episodes_corrected",
            "episodes",
            "overrides",
            "episode_entities",
        ],
    )
    if shadow is not False:
        raise SourceConflict("chronicler_material_shadowed")
    rows = await connection.fetch(
        f"SELECT {_SCHEDULE_FIELDS} FROM chronicler.scheduled_tasks WHERE name=$1 FOR UPDATE", JOB
    )
    if len(rows) != 1:
        raise SourceConflict("chronicler_schedule_missing")
    row = dict(rows[0])
    if (
        not isinstance(row["id"], UUID)
        or row["dispatch_mode"] != "job"
        or row["job_name"] != JOB
        or row["prompt"] is not None
        or row["enabled"] is not True
        or row["source"] not in {"db", "toml"}
    ):
        raise SourceConflict("chronicler_schedule_changed")
    # Validate native UUID subclasses only at this typed DB boundary.
    row["id"] = UUID(str(row["id"]))
    if expected_row is not None and json.loads(_json(row)) != expected_row:
        raise SourceConflict("chronicler_schedule_changed")
    await connection.execute(
        "LOCK TABLE chronicler.daily_rollups, chronicler.daily_rollup_flags, "
        "chronicler.episodes, chronicler.overrides, chronicler.episode_entities IN SHARE MODE"
    )
    return row


class ChroniclerPreparation:
    def __init__(self, pool, job_args):
        self.pool, self.job_args = pool, job_args
        self.owner = chronicler_preparation_owner(pool, create=True)

    async def __call__(self, **material):
        await self.prepare_committed(**material)
        return "staged_unverified"

    async def prepare_committed(self, **material):
        if self.owner._closed or self.owner._quarantined:
            raise SourceUnavailable("chronicler_preparation_unavailable")
        # Freeze day/material BEFORE any suspension. Reconciliation uses this
        # captured input, never a new wall clock or fresh identity on retry.
        captured = narration_material(**material)
        declaration, registration = _registration()
        local_date = date.fromisoformat(captured["local_date"])
        timezone = captured["timezone"]
        # A day-level source is deterministic BEFORE discovering its row ID;
        # row identity itself is additionally part of the full revision payload.
        from uuid import NAMESPACE_URL

        source_id = uuid5(
            NAMESPACE_URL, "butlers:chronicler:" + _json([JOB, captured["local_date"], timezone])
        )
        command, token = uuid5(source_id, "prepare"), uuid5(source_id, "claim")

        async def write(repository):
            await repository.lock_git_inventory()
            await repository.read_command(command)
            await repository.lock_source(source_id)
            row = await _locked_source(repository)
            if (row["job_args"] or {}) != (self.job_args or {}) or (self.job_args or {}).get(
                "timezone", "Asia/Singapore"
            ) != timezone:
                raise SourceConflict("chronicler_job_args_changed")
            current = await _current_material(repository.connection, local_date, timezone)
            if current != captured or _registration() != (declaration, registration):
                raise SourceConflict("chronicler_material_changed")
            payload = {
                "kind": KIND,
                "job_name": JOB,
                "registration": registration,
                "schedule": row,
                "material": current,
                "admission": "unverified_unavailable",
            }
            payload = json.loads(_json(payload))
            value = DefinitionRevision(
                "chronicler",
                "model_job",
                source_id,
                1,
                digest(payload),
                declaration,
                "git",
                payload_json=_json(payload),
            )
            old = await repository.read(source_id)
            if old is not None and old != value:
                raise SourceConflict("chronicler_material_changed")
            if old is None:
                await repository.revise(command, value, expected_revision=None)
            occurrence = await repository.capture(
                source_id, value.revision, slot=captured["local_date"]
            )
            if not await repository.claim(source_id, occurrence, token):
                raise SourceConflict("chronicler_claim_changed")
            return await prepare_dark_submission(repository, source_id, occurrence, token)

        async def operation():
            return await run_source_transaction(
                _ReleaseOwnedPool(self.pool), "chronicler", write, require_fresh=True
            )

        return await self.owner.run(operation)


async def chronicler_material_current(repository, value, snapshot=None):
    """Validate only this exact subtype, under the PASSED owning connection."""
    from uuid import NAMESPACE_URL

    try:
        payload = json.loads(value.payload_json)
        material = payload["material"]
        declaration, registration = _registration()
        expected_id = uuid5(
            NAMESPACE_URL,
            "butlers:chronicler:" + _json([JOB, material["local_date"], material["timezone"]]),
        )
        if (
            set(payload)
            != {"kind", "job_name", "registration", "schedule", "material", "admission"}
            or payload["kind"] != KIND
            or payload["job_name"] != JOB
            or payload["admission"] != "unverified_unavailable"
            or value.owner_domain != "chronicler"
            or repository.owner_domain != "chronicler"
            or value.family != "model_job"
            or value.origin != "git"
            or value.creator is not None
            or value.generation != 1
            or value.state != "active"
            or value.source_id != expected_id
            or value.declaration != declaration
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise SourceConflict("git_schedule_row_missing") from None
    if snapshot is not None and (
        snapshot.owner_domain != "chronicler"
        or snapshot.source_sha256 != registration["source_sha"]
        or snapshot.config_digest != registration["config_digest"]
    ):
        raise SourceConflict("chronicler_owner_changed")
    await _locked_source(repository, expected_row=payload["schedule"])
    return payload["registration"] == registration and material == await _current_material(
        repository.connection, date.fromisoformat(material["local_date"]), material["timezone"]
    )


async def reconcile_chronicler_source(repository, value, snapshot, command):
    if not await chronicler_material_current(repository, value, snapshot):
        await repository.revise(
            command, replace(value, generation=2, state="deleted"), expected_revision=value.revision
        )


@dataclass(frozen=True)
class ChroniclerRuntime:
    owner_domain = "chronicler"

    async def read_submission_in_transaction(self, connection, submitted):
        if type(submitted) is not DarkSubmission:
            raise SourceConflict("chronicler_submission_invalid")
        repository = ObligationSourceRepository(connection, self.owner_domain)
        await repository.lock_git_inventory()
        await repository.lock_source(submitted.definition.source_id)
        current = await repository.read(submitted.definition.source_id)
        if current != submitted.definition or not await chronicler_material_current(
            repository, current
        ):
            raise SourceConflict("chronicler_material_changed")
        result = await prepare_dark_submission(
            repository, current.source_id, submitted.occurrence_id, submitted.claim_token
        )
        if result != submitted:
            raise SourceConflict("chronicler_submission_changed")
        return result
