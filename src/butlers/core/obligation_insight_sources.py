"""Private Insight selected-content preparation, never admission or execution.

The real daily/urgent collector supplies its actual selection. The Switchboard
role locks/rereads candidate contents and persists complete material in the same
owning source transaction. Prepared-action/premise decorations are observations,
not authorization or a promise that a different domain's state stayed unchanged.
No endpoint, verified Evidence, creator, current catalog or runner is provided.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from butlers.config import load_config, obligation_config_material_digest
from butlers.core.goal_admission import digest
from butlers.core.obligation_admission import (
    SourceUnavailable,
    prepare_dark_submission,
    run_source_transaction,
)
from butlers.core.obligation_sources import DefinitionRevision, SourceConflict

JOBS = frozenset({"insight_delivery_cycle", "insight_urgent_subcycle"})
COLUMNS = (
    "id",
    "origin_butler",
    "priority",
    "category",
    "dedup_key",
    "cooldown_days",
    "expires_at",
    "message",
    "channel",
    "metadata",
    "prepared_action_id",
    "created_at",
    "premise",
)


def _json(value):
    def encode(item):
        if isinstance(item, (datetime, date)):
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
        raise SourceConflict("insight_material_invalid") from None


def _identity(job_name, candidates):
    if job_name not in JOBS or not candidates:
        raise SourceConflict("insight_source_invalid")
    try:
        ids = sorted(str(UUID(str(c["id"]))) for c in candidates)
        if len(ids) != len(set(ids)):
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise SourceConflict("insight_source_invalid") from None
    # IDs identify a selected-set occurrence, NEVER its content revision.
    return uuid5(NAMESPACE_URL, "butlers:insight-selected:" + digest([job_name, ids]))


def _validate_material(job_name, raw, selected, context):
    from butlers.tools.switchboard.insight.broker import (
        _cluster_candidates,
        _cluster_synthesis_prompt,
        _decorate_candidate_with_door,
        _stamp_unverified,
    )

    try:
        if (
            any(set(c) != set(COLUMNS) for c in raw)
            or set(context)
            != {
                "urgent_only",
                "daily_hold_mode",
                "selection_reference",
                "settings",
                "effective_budget",
                "category_budget_reasons",
                "premise_verdicts",
                "prepared_action_statuses",
                "clusters",
            }
            or context["urgent_only"] is not (job_name == "insight_urgent_subcycle")
            or context["daily_hold_mode"] is not (job_name == "insight_delivery_cycle")
        ):
            raise ValueError
        reference = datetime.fromisoformat(context["selection_reference"])
        if reference.tzinfo is None or any(
            datetime.fromisoformat(c["expires_at"]) <= reference for c in raw
        ):
            raise ValueError
        expected = []
        for candidate in raw:
            # Parse only the complete internal captured row; no external lookup,
            # grant or fresh prepared-action authority is asserted by this copy.
            row = dict(candidate, created_at=datetime.fromisoformat(candidate["created_at"]))
            if row["prepared_action_id"] is not None:
                row = _decorate_candidate_with_door(
                    row, context["prepared_action_statuses"].get(row["prepared_action_id"])
                )
            if row["premise"] is not None and context["premise_verdicts"].get(row["id"]) is None:
                row = _stamp_unverified(row)
            expected.append(row)
        if _json(expected) != _json(selected):
            raise ValueError
        clusters = [
            {
                "ids": [str(c["id"]) for c in cluster],
                "prompt": _cluster_synthesis_prompt(cluster) if len(cluster) > 1 else None,
            }
            for cluster in _cluster_candidates(selected)
        ]
        if clusters != context["clusters"]:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise SourceConflict("insight_selection_invalid") from None


def configured_insight_preparer(pool, job_name):
    """Server-only staging selector. Job args/HTTP/model cannot enable this.

    OFF performs no config/source DB IO. Loaded TOML is local source material,
    not a current published catalog; its real STAFFER notify refusal remains.
    """
    if os.environ.get("BUTLERS_INSIGHT_DARK_PREPARATION") != "1":
        return None
    try:
        config = load_config(Path(__file__).resolve().parents[3] / "roster" / "switchboard")
        specs = [s for s in config.schedules if s.job_name == job_name and s.dispatch_mode == "job"]
        if (
            config.name != "switchboard"
            or job_name not in JOBS
            or len(specs) != 1
            or not specs[0].obligation
        ):
            raise SourceConflict("insight_declaration_unavailable")
        return InsightPreparation(
            pool,
            job_name,
            specs[0].obligation.declaration,
            config.source_sha256,
            obligation_config_material_digest(config),
        )
    except Exception:

        async def unavailable(*_):
            return "unavailable"

        return unavailable


class InsightPreparation:
    def __init__(self, pool, job_name, declaration, source_sha, config_digest):
        self.pool, self.job_name, self.declaration = pool, job_name, declaration
        self.source_sha, self.config_digest = source_sha, config_digest

    async def __call__(self, raw, selected, context):
        # Canonical copies remove aliases to collector-owned mutable objects.
        raw, selected, context = (json.loads(_json(v)) for v in (raw, selected, context))
        _validate_material(self.job_name, raw, selected, context)
        source_id = _identity(self.job_name, raw)
        payload = {
            "kind": "insight_selected_v1",
            "job_name": self.job_name,
            "source_sha": self.source_sha,
            "config_digest": self.config_digest,
            "raw": raw,
            "selected": selected,
            "context": context,
            "clusters": context["clusters"],
            "admission": "unverified_unavailable",
        }
        text = _json(payload)
        value = DefinitionRevision(
            "switchboard",
            "model_job",
            source_id,
            1,
            digest(payload),
            self.declaration,
            "git",
            payload_json=text,
        )
        command, token = uuid5(source_id, "prepare"), uuid5(source_id, "claim")

        async def write(repository):
            await repository.lock_git_inventory()
            await repository.read_command(command)
            await repository.lock_source(source_id)
            connection = repository.connection
            # Qualified public source plus exact own role avoids temp shadowing.
            rows = await connection.fetch(
                "SELECT id, origin_butler, priority, category, dedup_key, cooldown_days, "
                "expires_at, message, channel, metadata, prepared_action_id, created_at, premise "
                "FROM public.insight_candidates WHERE id=ANY($1::uuid[]) AND status='pending' "
                "ORDER BY id FOR UPDATE",
                [UUID(c["id"]) for c in raw],
            )
            current = {str(c["id"]): dict(c) for c in rows}
            if len(current) != len(raw) or _json([current.get(c["id"]) for c in raw]) != _json(raw):
                raise SourceConflict("insight_selection_changed")
            old = await repository.read(source_id)
            if old is not None and old != value:
                raise SourceConflict("insight_material_changed")
            if old is None:
                await repository.revise(command, value, expected_revision=None)
            occurrence = await repository.capture(source_id, value.revision, slot="selected-set")
            if not await repository.claim(source_id, occurrence, token):
                raise SourceConflict("insight_claim_changed")
            return await prepare_dark_submission(repository, source_id, occurrence, token)

        try:
            async with asyncio.timeout(5):
                await run_source_transaction(self.pool, "switchboard", write, require_fresh=True)
        except SourceUnavailable:
            # Existing source helper deliberately reports cancellation as unknown.
            # The collector must still preserve its caller's cancellation.
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError from None
            raise
        return "staged_unverified"


async def reconcile_selected_source(repository, value, snapshot, command):
    """Verify this exact fixed producer subtype; refuse every unknown/dangling source.

    Full Git reconciliation may preserve a still-current selected-set source or
    tombstone it when its actual loaded config/candidates disappear or change.
    This is not a marker-only exception to schedule inventory integrity.
    """
    from dataclasses import replace

    from butlers.core.obligation_git_sources import _registered_model_job_handler

    try:
        payload = json.loads(value.payload_json)
        if (
            set(payload)
            != {
                "kind",
                "job_name",
                "source_sha",
                "config_digest",
                "raw",
                "selected",
                "context",
                "clusters",
                "admission",
            }
            or payload["kind"] != "insight_selected_v1"
            or value.owner_domain != "switchboard"
            or snapshot.owner_domain != "switchboard"
            or value.family != "model_job"
            or value.origin != "git"
            or value.creator is not None
            or value.source_id != _identity(payload["job_name"], payload["raw"])
            or value.generation != 1
            or value.state != "active"
            or any(set(c) != set(COLUMNS) for c in payload["raw"])
            or [c["id"] for c in payload["raw"]] != [c["id"] for c in payload["selected"]]
            or payload["clusters"] != payload["context"]["clusters"]
            or payload["admission"] != "unverified_unavailable"
        ):
            raise ValueError
    except (TypeError, ValueError, KeyError):
        raise SourceConflict("git_schedule_row_missing") from None
    _validate_material(payload["job_name"], payload["raw"], payload["selected"], payload["context"])
    definitions = [d for d in snapshot.definitions if d.schedule["job_name"] == payload["job_name"]]
    # Recheck the actual registration even when the source's TOML was removed.
    handler = _registered_model_job_handler("switchboard", payload["job_name"])
    if handler is None:
        raise SourceConflict("model_job_registration_changed")
    if len(definitions) > 1:
        raise SourceConflict("git_sync_set_mismatch")
    same_config = (
        payload["source_sha"] == snapshot.source_sha256
        and payload["config_digest"] == snapshot.config_digest
    )
    if definitions and same_config and definitions[0].declaration != value.declaration:
        raise SourceConflict("insight_declaration_changed")
    current_config = bool(
        definitions
        and definitions[0].job_handler is handler
        and definitions[0].declaration == value.declaration
        and definitions[0].schedule["enabled"]
        and payload["source_sha"] == snapshot.source_sha256
        and payload["config_digest"] == snapshot.config_digest
    )
    rows = await repository.connection.fetch(
        "SELECT id, origin_butler, priority, category, dedup_key, cooldown_days, "
        "expires_at, message, channel, metadata, prepared_action_id, created_at, premise "
        "FROM public.insight_candidates WHERE id=ANY($1::uuid[]) AND status='pending' "
        "ORDER BY id FOR UPDATE",
        [UUID(c["id"]) for c in payload["raw"]],
    )
    by_id = {str(c["id"]): dict(c) for c in rows}
    material_current = _json([by_id.get(c["id"]) for c in payload["raw"]]) == _json(payload["raw"])
    if not current_config or not material_current:
        await repository.revise(
            command, replace(value, generation=2, state="deleted"), expected_revision=value.revision
        )
