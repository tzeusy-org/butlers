"""Utility helpers used by the Butler daemon.

Extracted from daemon.py to reduce its size. These are pure utility
functions with no daemon-lifecycle dependencies.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError

from butlers.config import ButlerConfig


def _format_validation_error(prefix: str, exc: ValidationError) -> str:
    """Build a deterministic single-line validation error summary."""
    errors = exc.errors()
    if not errors:
        return prefix

    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg") or "invalid value")
    if location:
        return f"{prefix} ({location}): {message}"
    return f"{prefix}: {message}"


def _extract_delivery_id(
    *,
    channel: str,
    adapter_result: Any,
    fallback_request_id: str | None,
) -> str:
    """Derive a stable delivery identifier from adapter output."""
    if isinstance(adapter_result, dict):
        for key in ("delivery_id", "message_id", "id", "thread_id"):
            value = adapter_result.get(key)
            if value not in (None, ""):
                return str(value)

        nested = adapter_result.get("result")
        if isinstance(nested, dict):
            for key in ("delivery_id", "message_id", "id"):
                value = nested.get(key)
                if value not in (None, ""):
                    return str(value)

    if fallback_request_id:
        return f"{channel}:{fallback_request_id}"
    return f"{channel}:{uuid.uuid4()}"


def _flatten_config_for_secret_scan(config: ButlerConfig) -> dict[str, Any]:
    """Flatten ButlerConfig into a dict for secret scanning.

    Excludes credentials_env fields and [butler.env] lists per spec.
    """
    flat: dict[str, Any] = {}

    # Butler identity
    flat["butler.name"] = config.name
    flat["butler.port"] = config.port
    if config.description:
        flat["butler.description"] = config.description
    flat["butler.db.name"] = config.db_name
    if config.db_schema:
        flat["butler.db.schema"] = config.db_schema

    # Schedules (cron and prompt strings)
    for i, schedule in enumerate(config.schedules):
        flat[f"butler.schedule[{i}].name"] = schedule.name
        flat[f"butler.schedule[{i}].cron"] = schedule.cron
        flat[f"butler.schedule[{i}].prompt"] = schedule.prompt

    # Module configs (flatten nested dicts, skip env-var name declaration keys)
    def _flatten_module_value(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, nested_value in value.items():
                if key == "credentials_env" or key.endswith("_env"):
                    continue
                _flatten_module_value(f"{prefix}.{key}", nested_value)
            return
        flat[prefix] = value

    for mod_name, mod_cfg in config.modules.items():
        _flatten_module_value(f"modules.{mod_name}", mod_cfg)

    # NOTE: [butler.env].required and [butler.env].optional are lists of
    # env var *names* (not values), so they are exempt from scanning.

    return flat


def _extract_identity_scope_credentials(
    module_name: str, module_config: Any
) -> dict[str, list[str]]:
    """Extract scoped env-var names from ``user``/``bot`` config sections."""
    if hasattr(module_config, "model_dump"):
        config_dict = module_config.model_dump()
    elif isinstance(module_config, dict):
        config_dict = module_config
    else:
        return {}

    scoped_credentials: dict[str, list[str]] = {}
    for scope_name in ("bot",):  # user-scope excluded: resolved from owner entity_info
        scope_cfg = config_dict.get(scope_name)
        if not isinstance(scope_cfg, dict):
            continue
        if scope_cfg.get("enabled", True) is False:
            continue

        env_vars: list[str] = []
        for key, value in scope_cfg.items():
            if key.endswith("_env") and isinstance(value, str) and value:
                env_vars.append(value)
            if key == "credentials_env":
                if isinstance(value, str) and value:
                    env_vars.append(value)
                elif isinstance(value, list):
                    env_vars.extend(item for item in value if isinstance(item, str) and item)

        if env_vars:
            # Preserve declaration order while deduplicating.
            scoped_credentials[f"{module_name}.{scope_name}"] = list(dict.fromkeys(env_vars))

    return scoped_credentials
