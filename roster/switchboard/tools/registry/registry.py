"""Butler registry — registration, listing, and discovery."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

ELIGIBILITY_ACTIVE = "active"
ELIGIBILITY_STALE = "stale"
ELIGIBILITY_QUARANTINED = "quarantined"
_ELIGIBILITY_STATES = frozenset(
    {
        ELIGIBILITY_ACTIVE,
        ELIGIBILITY_STALE,
        ELIGIBILITY_QUARANTINED,
    }
)

DEFAULT_LIVENESS_TTL_SECONDS = 300
DEFAULT_ROUTE_CONTRACT_VERSION = 1


def _normalize_string_list(raw: Any) -> list[str]:
    if raw is None:
        return []

    data = raw
    if isinstance(raw, str):
        candidate = raw.strip()
        if not candidate:
            return []
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            data = [candidate]

    if isinstance(data, dict):
        iterable = data.keys()
    elif isinstance(data, (list, tuple, set)):
        iterable = data
    else:
        return []

    values: list[str] = []
    for value in iterable:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)
    return values


def _normalize_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _normalize_eligibility_state(value: Any) -> str:
    normalized = str(value or ELIGIBILITY_ACTIVE).strip().lower()
    if normalized not in _ELIGIBILITY_STATES:
        return ELIGIBILITY_ACTIVE
    return normalized


def _derive_eligibility_state(
    row: dict[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(UTC)
    explicit_state = _normalize_eligibility_state(row.get("eligibility_state"))

    if explicit_state == ELIGIBILITY_QUARANTINED or row.get("quarantined_at") is not None:
        return ELIGIBILITY_QUARANTINED

    last_seen_at = row.get("last_seen_at")
    if last_seen_at is None:
        return ELIGIBILITY_STALE

    ttl_seconds = _normalize_positive_int(
        row.get("liveness_ttl_seconds"),
        default=DEFAULT_LIVENESS_TTL_SECONDS,
    )
    return (
        ELIGIBILITY_ACTIVE
        if (last_seen_at + timedelta(seconds=ttl_seconds)) >= now
        else ELIGIBILITY_STALE
    )


def _transition_reason(previous_state: str, new_state: str) -> str:
    if previous_state == ELIGIBILITY_QUARANTINED and new_state == ELIGIBILITY_ACTIVE:
        return "re_registered"
    if previous_state == ELIGIBILITY_STALE and new_state == ELIGIBILITY_ACTIVE:
        return "health_restored"
    if new_state == ELIGIBILITY_STALE:
        return "ttl_expired"
    if new_state == ELIGIBILITY_QUARANTINED:
        return "quarantined"
    return "eligibility_state_changed"


def _normalize_route_contract_bounds(
    route_contract_min: Any,
    route_contract_max: Any,
) -> tuple[int, int]:
    min_version = _normalize_positive_int(
        route_contract_min,
        default=DEFAULT_ROUTE_CONTRACT_VERSION,
    )
    max_version = _normalize_positive_int(
        route_contract_max,
        default=min_version,
    )
    if max_version < min_version:
        max_version = min_version
    return min_version, max_version


def _is_route_contract_compatible(
    row: dict[str, Any],
    *,
    route_contract_version: int,
) -> bool:
    min_version, max_version = _normalize_route_contract_bounds(
        row.get("route_contract_min"),
        row.get("route_contract_max"),
    )
    return min_version <= route_contract_version <= max_version


def _supports_capability(
    row: dict[str, Any],
    *,
    required_capability: str | None,
) -> bool:
    if required_capability in (None, ""):
        return True

    advertised = {cap.lower() for cap in _normalize_string_list(row.get("capabilities"))}
    if not advertised:
        # Back-compat: permissive when legacy rows do not advertise capabilities.
        return True
    return required_capability.lower() in advertised


async def _audit_eligibility_transition(
    pool: asyncpg.Pool,
    *,
    name: str,
    previous_state: str,
    new_state: str,
    reason: str,
    previous_last_seen_at: datetime | None,
    new_last_seen_at: datetime | None,
    observed_at: datetime,
) -> None:
    try:
        await pool.execute(
            """
            INSERT INTO butler_registry_eligibility_log (
                butler_name,
                previous_state,
                new_state,
                reason,
                previous_last_seen_at,
                new_last_seen_at,
                observed_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            name,
            previous_state,
            new_state,
            reason,
            previous_last_seen_at,
            new_last_seen_at,
            observed_at,
        )
    except asyncpg.UndefinedTableError:
        logger.debug(
            "butler_registry_eligibility_log missing; skipping transition audit insert",
            exc_info=True,
        )


async def _reconcile_eligibility_state(
    pool: asyncpg.Pool,
    row: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    previous_state = _normalize_eligibility_state(row.get("eligibility_state"))
    current_state = _derive_eligibility_state(row, now=now)
    row["eligibility_state"] = current_state

    if current_state == previous_state:
        return row

    await pool.execute(
        """
        UPDATE butler_registry
        SET eligibility_state = $2,
            eligibility_updated_at = $3
        WHERE name = $1
        """,
        row["name"],
        current_state,
        now,
    )
    row["eligibility_updated_at"] = now
    await _audit_eligibility_transition(
        pool,
        name=row["name"],
        previous_state=previous_state,
        new_state=current_state,
        reason=_transition_reason(previous_state, current_state),
        previous_last_seen_at=row.get("last_seen_at"),
        new_last_seen_at=row.get("last_seen_at"),
        observed_at=now,
    )
    return row


def _normalized_registry_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized["modules"] = _normalize_string_list(normalized.get("modules"))
    normalized["capabilities"] = _normalize_string_list(normalized.get("capabilities"))
    min_version, max_version = _normalize_route_contract_bounds(
        normalized.get("route_contract_min"),
        normalized.get("route_contract_max"),
    )
    normalized["route_contract_min"] = min_version
    normalized["route_contract_max"] = max_version
    normalized["liveness_ttl_seconds"] = _normalize_positive_int(
        normalized.get("liveness_ttl_seconds"),
        default=DEFAULT_LIVENESS_TTL_SECONDS,
    )
    normalized["eligibility_state"] = _normalize_eligibility_state(
        normalized.get("eligibility_state")
    )
    return normalized


def _record_to_dict(row: Any) -> dict[str, Any]:
    """Convert an asyncpg.Record (or dict) to a plain dict."""
    if isinstance(row, dict):
        return dict(row)

    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


async def register_butler(
    pool: asyncpg.Pool,
    name: str,
    endpoint_url: str,
    description: str | None = None,
    modules: list[str] | None = None,
    *,
    capabilities: list[str] | None = None,
    route_contract_min: int = DEFAULT_ROUTE_CONTRACT_VERSION,
    route_contract_max: int = DEFAULT_ROUTE_CONTRACT_VERSION,
    liveness_ttl_seconds: int = DEFAULT_LIVENESS_TTL_SECONDS,
) -> None:
    """Register or update a butler in the registry."""
    now = datetime.now(UTC)
    module_list = _normalize_string_list(modules or [])
    capability_values = capabilities if capabilities is not None else module_list
    capability_list = _normalize_string_list(capability_values)
    if "trigger" not in {cap.lower() for cap in capability_list}:
        capability_list.append("trigger")
    min_version, max_version = _normalize_route_contract_bounds(
        route_contract_min,
        route_contract_max,
    )
    ttl_seconds = _normalize_positive_int(
        liveness_ttl_seconds,
        default=DEFAULT_LIVENESS_TTL_SECONDS,
    )

    existing = await pool.fetchrow(
        "SELECT eligibility_state, last_seen_at FROM butler_registry WHERE name = $1",
        name,
    )
    previous_state = (
        _normalize_eligibility_state(existing["eligibility_state"])
        if existing is not None
        else None
    )
    previous_last_seen_at = existing["last_seen_at"] if existing is not None else None

    await pool.execute(
        """
        INSERT INTO butler_registry (
            name,
            endpoint_url,
            description,
            modules,
            last_seen_at,
            eligibility_state,
            liveness_ttl_seconds,
            quarantined_at,
            quarantine_reason,
            route_contract_min,
            route_contract_max,
            capabilities,
            eligibility_updated_at
        )
        VALUES (
            $1,
            $2,
            $3,
            $4::jsonb,
            $5,
            $6,
            $7,
            NULL,
            NULL,
            $8,
            $9,
            $10::jsonb,
            $11
        )
        ON CONFLICT (name) DO UPDATE SET
            endpoint_url = EXCLUDED.endpoint_url,
            description = EXCLUDED.description,
            modules = EXCLUDED.modules,
            last_seen_at = EXCLUDED.last_seen_at,
            eligibility_state = EXCLUDED.eligibility_state,
            liveness_ttl_seconds = EXCLUDED.liveness_ttl_seconds,
            quarantined_at = NULL,
            quarantine_reason = NULL,
            route_contract_min = EXCLUDED.route_contract_min,
            route_contract_max = EXCLUDED.route_contract_max,
            capabilities = EXCLUDED.capabilities,
            eligibility_updated_at = EXCLUDED.eligibility_updated_at
        """,
        name,
        endpoint_url,
        description,
        json.dumps(module_list),
        now,
        ELIGIBILITY_ACTIVE,
        ttl_seconds,
        min_version,
        max_version,
        json.dumps(capability_list),
        now,
    )

    if previous_state is not None and previous_state != ELIGIBILITY_ACTIVE:
        await _audit_eligibility_transition(
            pool,
            name=name,
            previous_state=previous_state,
            new_state=ELIGIBILITY_ACTIVE,
            reason=_transition_reason(previous_state, ELIGIBILITY_ACTIVE),
            previous_last_seen_at=previous_last_seen_at,
            new_last_seen_at=now,
            observed_at=now,
        )


async def resolve_routing_target(
    pool: asyncpg.Pool,
    name: str,
    *,
    required_capability: str | None = None,
    route_contract_version: int = DEFAULT_ROUTE_CONTRACT_VERSION,
    allow_stale: bool = False,
    allow_quarantined: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    row = await pool.fetchrow(
        """
        SELECT
            name,
            endpoint_url,
            description,
            modules,
            last_seen_at,
            registered_at,
            eligibility_state,
            liveness_ttl_seconds,
            quarantined_at,
            quarantine_reason,
            route_contract_min,
            route_contract_max,
            capabilities,
            eligibility_updated_at
        FROM butler_registry
        WHERE name = $1
        """,
        name,
    )
    if row is None:
        return None, f"Butler '{name}' not found in registry"

    normalized = _normalized_registry_row(_record_to_dict(row))
    normalized = await _reconcile_eligibility_state(pool, normalized)

    eligibility_state = normalized["eligibility_state"]
    if eligibility_state == ELIGIBILITY_STALE and not allow_stale:
        return None, f"Butler '{name}' is stale and cannot be routed by default policy"
    if eligibility_state == ELIGIBILITY_QUARANTINED and not allow_quarantined:
        quarantine_reason = normalized.get("quarantine_reason")
        if quarantine_reason:
            return (
                None,
                f"Butler '{name}' is quarantined and cannot be routed ({quarantine_reason})",
            )
        return None, f"Butler '{name}' is quarantined and cannot be routed"

    if not _is_route_contract_compatible(
        normalized,
        route_contract_version=route_contract_version,
    ):
        return (
            None,
            (
                "Route contract mismatch for "
                f"'{name}': target supports v{normalized['route_contract_min']}"
                f"..v{normalized['route_contract_max']}, requested v{route_contract_version}"
            ),
        )

    if not _supports_capability(normalized, required_capability=required_capability):
        return (
            None,
            f"Butler '{name}' does not advertise required capability '{required_capability}'",
        )

    return normalized, None


async def validate_route_target(
    pool: asyncpg.Pool,
    name: str,
    *,
    required_capability: str | None = None,
    route_contract_version: int = DEFAULT_ROUTE_CONTRACT_VERSION,
    allow_stale: bool = False,
    allow_quarantined: bool = False,
) -> str | None:
    _target, error = await resolve_routing_target(
        pool,
        name,
        required_capability=required_capability,
        route_contract_version=route_contract_version,
        allow_stale=allow_stale,
        allow_quarantined=allow_quarantined,
    )
    return error


async def list_butlers(
    pool: asyncpg.Pool,
    *,
    routable_only: bool = False,
) -> list[dict[str, Any]]:
    """Return registered butlers, with optional routing-eligibility filtering."""
    rows = await pool.fetch(
        """
        SELECT
            name,
            endpoint_url,
            description,
            modules,
            last_seen_at,
            registered_at,
            eligibility_state,
            liveness_ttl_seconds,
            quarantined_at,
            quarantine_reason,
            route_contract_min,
            route_contract_max,
            capabilities,
            eligibility_updated_at
        FROM butler_registry
        ORDER BY name
        """
    )
    butlers: list[dict[str, Any]] = []
    for row in rows:
        normalized = _normalized_registry_row(dict(row))
        normalized = await _reconcile_eligibility_state(pool, normalized)
        if routable_only and normalized["eligibility_state"] != ELIGIBILITY_ACTIVE:
            continue
        butlers.append(normalized)
    return butlers


async def discover_butlers(
    pool: asyncpg.Pool,
    butlers_dir: Path,
) -> list[dict[str, str]]:
    """Discover butler configs from the butlers/ directory and register them.

    Scans for butler.toml files, registers each butler with its endpoint URL
    based on name and port from the config.
    """
    from butlers.config import load_config

    butlers_dir = Path(butlers_dir)
    discovered: list[dict[str, str]] = []
    if not butlers_dir.is_dir():
        return discovered
    for config_dir in sorted(butlers_dir.iterdir()):
        toml_path = config_dir / "butler.toml"
        if toml_path.exists():
            try:
                config = load_config(config_dir)
                endpoint_url = f"http://localhost:{config.port}/sse"
                modules = list(config.modules.keys())
                capabilities = sorted(set(modules) | {"trigger"})
                await register_butler(
                    pool,
                    config.name,
                    endpoint_url,
                    config.description,
                    modules,
                    capabilities=capabilities,
                )
                discovered.append({"name": config.name, "endpoint_url": endpoint_url})
            except Exception:
                logger.exception("Failed to discover butler in %s", config_dir)
    return discovered
