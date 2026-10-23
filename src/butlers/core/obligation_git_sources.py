"""Server-held Git schedule material and owning full-set reconciliation.

A local final registry and a held runtime policy describe source material. They
are NOT an authenticated fleet catalog, current DB-policy proof, D7 creator
binding or an admission-mode selector. Startup captures this material without
source IO while OFF; admission must independently obtain its verified evidence
and catalog. No model/public MCP method accepts these objects.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from uuid import uuid4

from butlers.config import ButlerConfig, obligation_config_material_digest
from butlers.core.goal_admission import digest
from butlers.core.obligation_admission import run_source_transaction
from butlers.core.obligation_schedule_sources import (
    SCHEDULE_FIELDS,
    GitScheduleDefinition,
    compile_git_schedule_definition,
    stage_git_schedule_definition,
)
from butlers.core.obligation_sources import SourceConflict
from butlers.core.obligations import DeclarationError, project_loaded_schedule
from butlers.core.runtime_config import RuntimeConfig
from butlers.core.tool_catalog import ToolCatalog

if TYPE_CHECKING:
    from butlers.core.obligation_sources import DefinitionRevision


@dataclass(frozen=True)
class LoadedGitSources:
    owner_domain: str
    source_sha256: str
    config_digest: str
    context_json: str
    definitions: tuple[GitScheduleDefinition, ...]

    @property
    def digest(self) -> str:
        return digest(
            {
                "source": self.source_sha256,
                "config": self.config_digest,
                "context": json.loads(self.context_json),
                "definitions": [item.material_digest for item in self.definitions],
            }
        )


def capture_loaded_git_sources(
    config: ButlerConfig, runtime: RuntimeConfig, registry: ToolCatalog
) -> LoadedGitSources:
    """Copy the actual startup inputs; never reread/reparse TOML or query DB.

    Held policy can include legacy defaults or stale-cache fallback. Its scope
    is deliberately explicit; neither timestamps nor a config hash upgrade it
    to fresh persisted policy. Local registration is likewise not fleet proof.
    """
    if (
        type(config) is not ButlerConfig
        or type(runtime) is not RuntimeConfig
        or type(registry) is not ToolCatalog
        or runtime.butler_name != config.name
        or any(item.owner_domain != config.name for item in registry.descriptors)
    ):
        raise DeclarationError("source_owner_mismatch")
    if (
        type(config.source_sha256) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", config.source_sha256)
        or config.source_material_sha256 is None
    ):
        raise DeclarationError("loaded_source_unavailable")
    if obligation_config_material_digest(config) != config.source_material_sha256:
        raise DeclarationError("loaded_source_changed")
    context = {
        "version": 1,
        "config_sha256": config.source_sha256,
        "typed_config_digest": config.source_material_sha256,
        "catalog_scope": "local_registration",
        "registry_generation": registry.generation_digest,
        "classification_complete": registry.classification_complete,
        "runtime_policy_scope": "held_effective",
        "runtime_policy": {
            "core_groups": runtime.core_groups,
            "core_groups_narrowing_reason_digest": digest(runtime.core_groups_narrowing_reason),
            "catalog_read_sensitivity": runtime.catalog_read_sensitivity,
            "tool_exposure_policy": runtime.tool_exposure_policy,
        },
    }
    context_json = json.dumps(context, sort_keys=True, separators=(",", ":"), allow_nan=False)
    definitions = []
    seen = set()
    for spec in config.schedules:
        projection = project_loaded_schedule(spec)
        if spec.name in seen:
            raise DeclarationError("duplicate_git_schedule")
        seen.add(spec.name)
        row = dict.fromkeys(SCHEDULE_FIELDS)
        row.update(dict(projection.fields))
        row.update(
            enabled=True,
            complexity=row["complexity"] or "workhorse",
            timezone=row["timezone"] or "UTC",
        )
        definitions.append(
            compile_git_schedule_definition(
                owner_domain=config.name,
                normalized_schedule=row,
                declaration=projection.declaration.declaration,
                context_material=context if registry.classification_complete else None,
            )
        )
    return LoadedGitSources(
        config.name,
        config.source_sha256,
        config.source_material_sha256,
        context_json,
        tuple(definitions),
    )


async def reconcile_loaded_git_sources(
    pool, snapshot: LoadedGitSources
) -> tuple[DefinitionRevision, ...]:
    """Reconcile a complete held config in ONE owning source transaction.

    This internal dark adapter does not select/enable an admission mode. The
    actual startup path only captures its inputs and performs zero B2 IO while
    OFF. No caller/model hash authenticates this snapshot. A trusted lifecycle
    selector must own the full config before registering reconciliation.

    Reconciliation reads current committed/staged rows; it is NOT historical
    command replay. After an unconfirmed commit the caller must explicitly
    reconcile/read back this full desired state, never infer success from a
    hash. Selected prepared-command replay retains its separate exact contract.
    """
    if type(snapshot) is not LoadedGitSources:
        raise SourceConflict("loaded_source_required")
    names = [item.schedule["name"] for item in snapshot.definitions]
    if len(names) != len(set(names)) or any(
        item.owner_domain != snapshot.owner_domain for item in snapshot.definitions
    ):
        raise SourceConflict("git_sync_set_mismatch")

    async def reconcile(repository):
        connection = repository.connection
        # Owning guard and full-inventory lock precede discovery. This lock is
        # never used to claim a runtime creator or a cross-domain schema.
        await repository.lock_git_inventory()
        table = f'"{repository.owner_domain}".scheduled_tasks'
        rows = await connection.fetch(
            f"SELECT id,name,source FROM {table} WHERE source='toml' ORDER BY name,id"
        )
        by_name = {row["name"]: row for row in rows}
        if len(by_name) != len(rows):
            raise SourceConflict("duplicate_git_schedule")
        identities = await connection.fetch(
            "SELECT id FROM obligation_definitions WHERE origin='git' "
            "AND family IN ('schedule','deadline') AND state<>'deleted' ORDER BY id"
        )
        source_ids = {row["id"] for row in rows} | {row["id"] for row in identities}
        # All command locks precede ALL source locks, including removals.
        commands = {name: uuid4() for name in sorted(set(names) | set(by_name))}
        for command in sorted(commands.values(), key=lambda value: value.int):
            await repository.read_command(command)
        for source in sorted(source_ids, key=lambda value: value.int):
            await repository.lock_source(source)
        current = {source: await repository.read(source) for source in source_ids}
        # A dangling active Git definition is an integrity error, not proof of
        # deletion. A real full-config removal below requires its actual row.
        row_ids = {row["id"] for row in rows}
        if any(row["id"] not in row_ids for row in identities):
            raise SourceConflict("git_schedule_row_missing")
        results = []
        for definition in sorted(snapshot.definitions, key=lambda item: item.schedule["name"]):
            name = definition.schedule["name"]
            row = by_name.get(name)
            source = row["id"] if row else None
            old = current.get(source)
            if source is not None:
                locked = await connection.fetchrow(
                    f"SELECT id,name,source FROM {table} WHERE id=$1 FOR UPDATE", source
                )
                if locked is None or locked["source"] != "toml" or locked["name"] != name:
                    raise SourceConflict("git_inventory_changed")
            if old is not None and (old.origin != "git" or old.state == "deleted"):
                raise SourceConflict("creation_binding_immutable")
            if not definition.available and old is None:
                # Missing declarations/context and deterministic jobs cannot
                # create an active obligation or delete an ordinary job row.
                continue
            result = await stage_git_schedule_definition(
                repository,
                command_id=commands[name],
                definition=definition,
                source_id=source,
                expected_revision=old.revision if old else None,
            )
            results.append(result.definition)
        for name in sorted(set(by_name) - set(names)):
            row = by_name[name]
            old = current[row["id"]]
            if old is not None and old.origin != "git":
                raise SourceConflict("creation_binding_immutable")
            # Full inventory, unlike selected sync, explicitly owns removal of
            # stale Git rows. Preserve every db/runtime row and its provenance.
            locked = await connection.fetchrow(
                f"SELECT id,name,source FROM {table} WHERE id=$1 FOR UPDATE", row["id"]
            )
            if locked is None or locked["source"] != "toml" or locked["name"] != name:
                raise SourceConflict("git_schedule_required")
            await connection.execute(f"DELETE FROM {table} WHERE id=$1", row["id"])
            if old is not None:
                removed = replace(old, generation=old.generation + 1, state="deleted")
                results.append(
                    await repository.revise(commands[name], removed, expected_revision=old.revision)
                )
        return tuple(results)

    return await run_source_transaction(pool, snapshot.owner_domain, reconcile)
