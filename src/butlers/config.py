"""Butler configuration loading and validation.

Reads butler.toml from a config directory, parses all sections, and returns
a validated ButlerConfig dataclass.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from butlers.core.runtimes import get_adapter

# Default LLM model used by all butlers unless overridden in butler.toml.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Pattern matching ${VAR_NAME} — supports alphanumeric + underscore variable names.
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(Exception):
    """Raised when butler configuration is missing, malformed, or invalid."""


@dataclass
class ScheduleConfig:
    """A single scheduled task entry from [[butler.schedule]]."""

    name: str
    cron: str
    prompt: str


@dataclass
class RuntimeConfig:
    """Runtime configuration from [butler.runtime] section.

    Controls which LLM runtime and model the butler uses. The model string
    is opaque to the framework — no validation beyond non-empty. Each runtime
    defines its own valid model IDs.
    """

    type: str = "claude-code"
    model: str | None = DEFAULT_MODEL


@dataclass
class GatedToolConfig:
    """Configuration for a single gated tool in the approvals module.

    Specifies an optional expiry override for this specific tool. If
    expiry_hours is None, the default_expiry_hours from ApprovalConfig
    is used.
    """

    expiry_hours: int | None = None


@dataclass
class ApprovalConfig:
    """Configuration for the approvals module from [modules.approvals].

    Controls approval gating behavior, default expiry, and which tools
    require approval.
    """

    enabled: bool
    default_expiry_hours: int = 48
    gated_tools: dict[str, GatedToolConfig] = field(default_factory=dict)

    def get_effective_expiry(self, tool_name: str) -> int:
        """Get the effective expiry hours for a tool.

        If the tool has a custom expiry override, use that. Otherwise,
        use the default expiry hours.

        Parameters
        ----------
        tool_name:
            The name of the tool to check.

        Returns
        -------
        int
            The effective expiry hours for this tool.
        """
        tool_config = self.gated_tools.get(tool_name)
        if tool_config and tool_config.expiry_hours is not None:
            return tool_config.expiry_hours
        return self.default_expiry_hours


@dataclass
class ButlerConfig:
    """Parsed and validated butler configuration."""

    name: str
    port: int
    description: str | None = None
    db_name: str = ""
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    schedules: list[ScheduleConfig] = field(default_factory=list)
    modules: dict[str, dict] = field(default_factory=dict)
    env_required: list[str] = field(default_factory=list)
    env_optional: list[str] = field(default_factory=list)
    shutdown_timeout_s: float = 30.0
    switchboard_url: str | None = None


def resolve_env_vars(value: Any) -> Any:
    """Recursively resolve ``${VAR_NAME}`` references in config values.

    Walks dicts, lists, and strings.  Non-string leaf values (int, bool,
    float, None) are returned unchanged.

    Parameters
    ----------
    value:
        A parsed TOML value — may be a dict, list, string, int, float,
        bool, or None.

    Returns
    -------
    Any
        The same structure with all ``${VAR_NAME}`` references in string
        values replaced by the corresponding environment variable.

    Raises
    ------
    ConfigError
        If a referenced environment variable is not set.
    """
    if isinstance(value, dict):
        return {k: resolve_env_vars(v) for k, v in value.items()}

    if isinstance(value, list):
        return [resolve_env_vars(item) for item in value]

    if isinstance(value, str):
        return _resolve_string(value)

    # int, float, bool, None — pass through unchanged.
    return value


def _resolve_string(s: str) -> str:
    """Replace all ``${VAR_NAME}`` occurrences in *s* with env var values.

    Collects all missing variable names and reports them in a single error.
    """
    missing: list[str] = []

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            missing.append(var_name)
            return match.group(0)  # keep placeholder for error reporting
        return env_value

    result = _ENV_VAR_PATTERN.sub(_replace, s)

    if missing:
        vars_str = ", ".join(missing)
        raise ConfigError(
            f"Unresolved environment variable(s) in config value: {vars_str} (original: {s!r})"
        )

    return result


def _parse_runtime(butler_section: dict) -> RuntimeConfig:
    """Parse the optional [butler.runtime] sub-section.

    Returns a RuntimeConfig using the dataclass default model (Haiku) if the
    section or field is absent. Empty-string model values fall through to the
    default.
    """
    runtime_section = butler_section.get("runtime", {})
    model = runtime_section.get("model")

    # Normalise empty/whitespace string → use default
    if isinstance(model, str) and not model.strip():
        model = None

    if model is not None:
        return RuntimeConfig(model=model)
    return RuntimeConfig()


def parse_approval_config(raw: dict[str, Any] | None) -> ApprovalConfig | None:
    """Parse approval configuration from [modules.approvals] section.

    Parameters
    ----------
    raw:
        Raw config dict from TOML, or None if the section is absent.

    Returns
    -------
    ApprovalConfig | None
        Parsed config, or None if *raw* is None.
    """
    if raw is None:
        return None

    enabled = raw.get("enabled", False)
    default_expiry_hours = raw.get("default_expiry_hours", 48)

    # Parse gated tools
    gated_tools_raw = raw.get("gated_tools", {})
    gated_tools: dict[str, GatedToolConfig] = {}

    for tool_name, tool_cfg in gated_tools_raw.items():
        if not isinstance(tool_cfg, dict):
            tool_cfg = {}
        expiry_hours = tool_cfg.get("expiry_hours")
        gated_tools[tool_name] = GatedToolConfig(expiry_hours=expiry_hours)

    return ApprovalConfig(
        enabled=enabled,
        default_expiry_hours=default_expiry_hours,
        gated_tools=gated_tools,
    )


def validate_approval_config(
    approval_config: ApprovalConfig | None, registered_tools: set[str]
) -> None:
    """Validate that all gated tools are actually registered.

    This should be called at butler startup after all modules have
    registered their tools.

    Parameters
    ----------
    approval_config:
        The parsed approval configuration, or None if approvals are not
        configured.
    registered_tools:
        Set of all tool names registered by the butler's modules.

    Raises
    ------
    ConfigError
        If any gated tool names are not in *registered_tools*.
    """
    if approval_config is None or not approval_config.enabled:
        return

    unknown_tools = set(approval_config.gated_tools.keys()) - registered_tools

    if unknown_tools:
        tools_str = ", ".join(sorted(unknown_tools))
        raise ConfigError(
            f"Unknown gated tool(s) in approval config: {tools_str}. "
            f"These tools are not registered by any module."
        )


def load_config(config_dir: Path) -> ButlerConfig:
    """Load and validate a butler.toml from *config_dir*.

    Parameters
    ----------
    config_dir:
        Directory containing ``butler.toml``.

    Returns
    -------
    ButlerConfig
        Fully parsed and validated configuration.

    Raises
    ------
    ConfigError
        If the file is missing, contains invalid TOML, or lacks required fields.
    """
    toml_path = config_dir / "butler.toml"

    if not toml_path.exists():
        raise ConfigError(f"Config file not found: {toml_path}")

    raw_bytes = toml_path.read_bytes()
    try:
        data = tomllib.loads(raw_bytes.decode())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {toml_path}: {exc}") from exc

    # --- Resolve env var references before any validation ---
    data = resolve_env_vars(data)

    # --- [butler] section (required) ---
    butler_section = data.get("butler")
    if not isinstance(butler_section, dict):
        raise ConfigError("Missing [butler] section in config")

    name = butler_section.get("name")
    if name is None:
        raise ConfigError("Missing required field: butler.name")

    port = butler_section.get("port")
    if port is None:
        raise ConfigError("Missing required field: butler.port")

    description = butler_section.get("description")

    # --- [butler.db] sub-section ---
    db_section = butler_section.get("db", {})
    db_name = db_section.get("name", f"butler_{name}")

    # --- [butler.env] sub-section ---
    env_section = butler_section.get("env", {})
    env_required = list(env_section.get("required", []))
    env_optional = list(env_section.get("optional", []))

    # --- [butler.shutdown] sub-section ---
    shutdown_section = butler_section.get("shutdown", {})
    shutdown_timeout_s = float(shutdown_section.get("timeout_s", 30.0))

    # --- [butler.switchboard] sub-section ---
    switchboard_section = butler_section.get("switchboard", {})
    switchboard_url: str | None = switchboard_section.get("url")
    # Default: derive from the Switchboard butler's known port (8100)
    # unless this butler IS the switchboard.
    if switchboard_url is None and name != "switchboard":
        switchboard_url = "http://localhost:8100/sse"

    # --- [[butler.schedule]] array ---
    raw_schedules = butler_section.get("schedule", [])
    schedules: list[ScheduleConfig] = []
    for entry in raw_schedules:
        schedules.append(
            ScheduleConfig(
                name=entry["name"],
                cron=entry["cron"],
                prompt=entry["prompt"],
            )
        )

    # --- [modules.*] sections ---
    modules: dict[str, dict] = {}
    raw_modules = data.get("modules", {})
    for mod_name, mod_cfg in raw_modules.items():
        modules[mod_name] = dict(mod_cfg) if isinstance(mod_cfg, dict) else {}

    # --- [runtime] section ---
    runtime_section = data.get("runtime", {})
    runtime_type = runtime_section.get("type", "claude-code")

    # Validate runtime type early (fail fast at config load time)
    try:
        get_adapter(runtime_type)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    # Parse model from [butler.runtime] sub-section
    butler_runtime = _parse_runtime(butler_section)
    runtime = RuntimeConfig(type=runtime_type, model=butler_runtime.model)

    return ButlerConfig(
        name=name,
        port=port,
        description=description,
        db_name=db_name,
        runtime=runtime,
        schedules=schedules,
        modules=modules,
        env_required=env_required,
        env_optional=env_optional,
        shutdown_timeout_s=shutdown_timeout_s,
        switchboard_url=switchboard_url,
    )
