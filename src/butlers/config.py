"""Butler configuration loading and validation.

Reads butler.toml from a config directory, parses all sections, and returns
a validated ButlerConfig dataclass.
"""

from __future__ import annotations

import enum
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default trusted callers for route.execute authz.
DEFAULT_TRUSTED_ROUTE_CALLERS: tuple[str, ...] = ("switchboard",)

# Pattern matching ${VAR_NAME} — supports alphanumeric + underscore variable names.
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_DB_SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CONSOLIDATED_DB_NAME = "butlers"
# Backwards-compatible alias for the historical private name.
_CONSOLIDATED_DB_NAME = CONSOLIDATED_DB_NAME


class ConfigError(Exception):
    """Raised when butler configuration is missing, malformed, or invalid."""


class ButlerType(enum.StrEnum):
    """Agent type — butler (user-facing domain agent) or staffer (infrastructure service)."""

    BUTLER = "butler"
    STAFFER = "staffer"


class ScheduleDispatchMode(enum.StrEnum):
    """Execution mode for scheduled tasks."""

    PROMPT = "prompt"
    JOB = "job"


@dataclass
class PermissionsConfig:
    """Cross-butler permissions from [butler.permissions] section.

    Declares which other agents this agent may connect to or act on behalf of.
    Staffers may declare wildcard (``["*"]``) or scoped access lists.
    Butlers default to an empty list (no cross-butler access).
    """

    cross_butler_access: list[str] = field(default_factory=list)


@dataclass
class LoggingConfig:
    """Logging configuration from [butler.logging] section."""

    level: str = "INFO"
    format: str = "text"  # "text" or "json"
    log_root: str | None = None


_ALLOWED_SCHEDULE_COMPLEXITIES: frozenset[str] = frozenset(
    {"reasoning", "workhorse", "cheap", "specialty", "local", "legacy"}
)


@dataclass
class ScheduleConfig:
    """A single scheduled task entry from [[butler.schedule]]."""

    name: str
    cron: str
    prompt: str | None = None
    dispatch_mode: ScheduleDispatchMode = ScheduleDispatchMode.PROMPT
    job_name: str | None = None
    job_args: dict[str, Any] | None = None
    max_token_budget: int | None = None
    complexity: str | None = None


@dataclass
class RuntimeSeedConfig:
    """Sole butler-scoped runtime configuration from ``[butler.runtime_seed]``.

    Used on first boot to seed the per-schema ``runtime_config`` DB table and,
    thereafter, as the in-memory fallback when the ``RuntimeConfigAccessor``
    is unavailable or its cache is empty. This is the only butler-scoped
    runtime config source in git.

    Fields:

    - ``core_groups`` / ``max_concurrent_sessions`` / ``max_queued_sessions``
      are the operational tuning knobs that map to the DB-backed
      ``runtime_config`` row. The Spawner prefers the DB row via
      :class:`RuntimeConfigAccessor` and falls back to the values here when
      no accessor is wired.
    - ``liveness_ttl_seconds`` / ``route_contract_min`` / ``route_contract_max``
      are registration-only and are not stored in ``runtime_config``.

    Model selection, per-session timeouts, and runtime CLI args live in
    ``public.model_catalog`` (resolved per spawn). The runtime adapter type
    is fixed for the whole roster — see ``DEFAULT_RUNTIME_TYPE`` in
    :mod:`butlers.core.runtimes`. See ``about/heart-and-soul/vision.md``
    Rule 5 for the ownership model.
    """

    core_groups: tuple[str, ...] | None = None
    max_concurrent_sessions: int = 3
    max_queued_sessions: int = 10
    liveness_ttl_seconds: int = 300
    route_contract_min: int = 1
    route_contract_max: int = 1


@dataclass
class GatedToolConfig:
    """Configuration for a single gated tool in the approvals module.

    Specifies an optional expiry override for this specific tool. If
    expiry_hours is None, the default_expiry_hours from ApprovalConfig
    is used.
    """

    expiry_hours: int | None = None
    risk_tier: ApprovalRiskTier | None = None


class ApprovalRiskTier(enum.StrEnum):
    """Risk tier for approval-gated actions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


DEFAULT_APPROVAL_RULE_PRECEDENCE: tuple[str, ...] = (
    "constraint_specificity_desc",
    "bounded_scope_desc",
    "created_at_desc",
    "rule_id_asc",
)


@dataclass
class ApprovalConfig:
    """Configuration for the approvals module from [modules.approvals].

    Controls approval gating behavior, default expiry, and which tools
    require approval.
    """

    enabled: bool
    default_expiry_hours: int = 48
    default_risk_tier: ApprovalRiskTier = ApprovalRiskTier.MEDIUM
    rule_precedence: tuple[str, ...] = DEFAULT_APPROVAL_RULE_PRECEDENCE
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

    def get_effective_risk_tier(self, tool_name: str) -> ApprovalRiskTier:
        """Get the effective risk tier for a tool."""
        tool_config = self.gated_tools.get(tool_name)
        if tool_config and tool_config.risk_tier is not None:
            if isinstance(tool_config.risk_tier, ApprovalRiskTier):
                return tool_config.risk_tier
            return ApprovalRiskTier(str(tool_config.risk_tier).lower())
        return self.default_risk_tier


@dataclass
class BufferConfig:
    """Durable buffer configuration from [buffer] section.

    Controls the in-memory queue, worker pool, and cold-path scanner for the
    durable message buffer. All fields have sensible defaults so existing
    butler.toml files without a [buffer] section remain fully compatible.
    """

    queue_capacity: int = 100
    worker_count: int = 1
    scanner_interval_s: int = 30
    scanner_grace_s: int = 10
    scanner_batch_size: int = 50
    max_consecutive_same_tier: int = 10
    scanner_lock_timeout_s: int = 300


@dataclass
class SchedulerConfig:
    """Scheduler loop configuration from [butler.scheduler] section.

    Controls the internal asyncio scheduler loop that calls tick() periodically
    to dispatch due cron tasks without relying on external heartbeat calls.

    Also controls the liveness reporter loop that periodically sends HTTP POST
    to the Switchboard's /api/switchboard/heartbeat endpoint.
    """

    tick_interval_seconds: int = 60
    heartbeat_interval_seconds: int = 120
    switchboard_url: str = "http://localhost:41200"


@dataclass
class StorageConfig:
    """S3-compatible blob storage marker.

    All blob storage parameters (endpoint_url, bucket, region, credentials)
    are resolved at runtime from the CredentialStore (managed via the
    dashboard secrets UI at /secrets).  This dataclass is kept as a
    placeholder for potential future non-secret storage settings.
    """

    pass


@dataclass
class ButlerConfig:
    """Parsed and validated butler configuration."""

    name: str
    port: int
    description: str | None = None
    type: ButlerType = ButlerType.BUTLER
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    db_name: str = ""
    db_schema: str | None = None
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    runtime_seed: RuntimeSeedConfig = field(default_factory=RuntimeSeedConfig)
    schedules: list[ScheduleConfig] = field(default_factory=list)
    modules: dict[str, dict] = field(default_factory=dict)
    env_required: list[str] = field(default_factory=list)
    env_optional: list[str] = field(default_factory=list)
    shutdown_timeout_s: float = 30.0
    switchboard_url: str | None = None
    trusted_route_callers: tuple[str, ...] = DEFAULT_TRUSTED_ROUTE_CALLERS
    storage: StorageConfig = field(default_factory=StorageConfig)
    buffer: BufferConfig = field(default_factory=BufferConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    # OAuth scope declarations keyed by provider name.
    # Populated from top-level [oauth.<provider>] sections in butler.toml.
    # Each value is a flat list of OAuth scope strings that this butler requires
    # from the named provider.  Example:
    #
    #   [oauth.google]
    #   scopes = [
    #       "https://www.googleapis.com/auth/calendar",
    #       "https://www.googleapis.com/auth/gmail.modify",
    #   ]
    #
    #   [oauth.spotify]
    #   scopes = ["user-read-recently-played", "user-top-read"]
    #
    # These declarations are read by the dashboard OAuth router to resolve the
    # scope-set for each provider as the union of all butler declarations.
    oauth: dict[str, list[str]] = field(default_factory=dict)


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


def _parse_runtime_seed(butler_section: dict) -> RuntimeSeedConfig:
    """Parse the optional [butler.runtime_seed] sub-section.

    Returns a RuntimeSeedConfig using dataclass defaults for any absent fields.
    This section is operational-only; model selection lives in the model
    catalog, while runtime adapter type lives in top-level ``[runtime]``.
    """
    seed_section = butler_section.get("runtime_seed", {})

    # --- core_groups ---
    raw_core_groups = seed_section.get("core_groups")
    core_groups: tuple[str, ...] | None = None
    if raw_core_groups is not None:
        if not isinstance(raw_core_groups, list):
            raise ConfigError(
                "Invalid butler.runtime_seed.core_groups: expected an array of strings"
            )
        validated_groups: list[str] = []
        for i, entry in enumerate(raw_core_groups):
            if not isinstance(entry, str) or not entry.strip():
                raise ConfigError(
                    f"Invalid butler.runtime_seed.core_groups[{i}]: expected a non-empty string"
                )
            validated_groups.append(entry.strip())
        core_groups = tuple(validated_groups)

    # Reject retired runtime-selection fields that now belong elsewhere.
    retired_keys = {
        "model": "the model catalog",
        "runtime_type": (
            "DEFAULT_RUNTIME_TYPE in butlers.core.runtimes (fixed for the whole roster)"
        ),
        "args": "the model catalog",
        "session_timeout_s": "the model catalog",
    }
    for key, destination in retired_keys.items():
        if key in seed_section:
            raise ConfigError(
                f"Invalid butler.runtime_seed.{key}: this field is no longer supported. "
                f"Move it to {destination}."
            )

    # --- numeric fields with validation ---
    max_concurrent_sessions = int(seed_section.get("max_concurrent_sessions", 3))
    if max_concurrent_sessions <= 0:
        raise ConfigError(
            f"Invalid butler.runtime_seed.max_concurrent_sessions: {max_concurrent_sessions!r}. "
            "Must be a positive integer."
        )

    max_queued_sessions = int(seed_section.get("max_queued_sessions", 10))
    if max_queued_sessions <= 0:
        raise ConfigError(
            f"Invalid butler.runtime_seed.max_queued_sessions: {max_queued_sessions!r}. "
            "Must be a positive integer."
        )

    liveness_ttl_seconds = int(seed_section.get("liveness_ttl_seconds", 300))
    route_contract_min = int(seed_section.get("route_contract_min", 1))
    route_contract_max = int(seed_section.get("route_contract_max", 1))

    return RuntimeSeedConfig(
        core_groups=core_groups,
        max_concurrent_sessions=max_concurrent_sessions,
        max_queued_sessions=max_queued_sessions,
        liveness_ttl_seconds=liveness_ttl_seconds,
        route_contract_min=route_contract_min,
        route_contract_max=route_contract_max,
    )


def _parse_schedule_entry(entry: Any, index: int) -> ScheduleConfig:
    """Parse and validate one ``[[butler.schedule]]`` entry."""
    entry_path = f"butler.schedule[{index}]"
    if not isinstance(entry, dict):
        raise ConfigError(f"{entry_path} must be a TOML table")

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"{entry_path}.name must be a non-empty string")

    cron = entry.get("cron")
    if not isinstance(cron, str) or not cron.strip():
        raise ConfigError(f"{entry_path}.cron must be a non-empty string")

    raw_mode = entry.get("dispatch_mode", ScheduleDispatchMode.PROMPT.value)
    if not isinstance(raw_mode, str):
        raise ConfigError(
            f"{entry_path}.dispatch_mode must be a string: "
            f"{ScheduleDispatchMode.PROMPT.value!r} or {ScheduleDispatchMode.JOB.value!r}"
        )
    normalized_mode = raw_mode.strip().lower()
    try:
        dispatch_mode = ScheduleDispatchMode(normalized_mode)
    except ValueError as exc:
        raise ConfigError(
            f"Invalid {entry_path}.dispatch_mode: {raw_mode!r}. "
            f"Expected {ScheduleDispatchMode.PROMPT.value!r} or {ScheduleDispatchMode.JOB.value!r}."
        ) from exc

    prompt = entry.get("prompt")
    if prompt is not None and not isinstance(prompt, str):
        raise ConfigError(f"{entry_path}.prompt must be a string when set")

    job_name = entry.get("job_name")
    if job_name is not None and not isinstance(job_name, str):
        raise ConfigError(f"{entry_path}.job_name must be a string when set")

    job_args = entry.get("job_args")
    if job_args is not None and not isinstance(job_args, dict):
        raise ConfigError(f"{entry_path}.job_args must be a table/object when set")

    raw_budget = entry.get("max_token_budget")
    max_token_budget: int | None = None
    if raw_budget is not None:
        if not isinstance(raw_budget, int) or raw_budget <= 0:
            raise ConfigError(f"{entry_path}.max_token_budget must be a positive integer")
        max_token_budget = raw_budget

    raw_complexity = entry.get("complexity")
    complexity: str | None = None
    if raw_complexity is not None:
        if not isinstance(raw_complexity, str):
            raise ConfigError(f"{entry_path}.complexity must be a string when set")
        normalized_complexity = raw_complexity.strip().lower()
        if normalized_complexity not in _ALLOWED_SCHEDULE_COMPLEXITIES:
            raise ConfigError(
                f"Invalid {entry_path}.complexity: {raw_complexity!r}. "
                f"Expected one of {sorted(_ALLOWED_SCHEDULE_COMPLEXITIES)}."
            )
        complexity = normalized_complexity

    if dispatch_mode == ScheduleDispatchMode.PROMPT:
        if prompt is None or not prompt.strip():
            raise ConfigError(f"{entry_path} with dispatch_mode='prompt' requires non-empty prompt")
        if job_name is not None:
            raise ConfigError(f"{entry_path}.job_name is only valid when dispatch_mode='job'")
        if job_args is not None:
            raise ConfigError(f"{entry_path}.job_args is only valid when dispatch_mode='job'")
        return ScheduleConfig(
            name=name,
            cron=cron,
            prompt=prompt,
            dispatch_mode=dispatch_mode,
            max_token_budget=max_token_budget,
            complexity=complexity,
        )

    if prompt is not None:
        raise ConfigError(f"{entry_path}.prompt is not allowed when dispatch_mode='job'")
    if job_name is None or not job_name.strip():
        raise ConfigError(f"{entry_path} with dispatch_mode='job' requires non-empty job_name")

    return ScheduleConfig(
        name=name,
        cron=cron,
        dispatch_mode=dispatch_mode,
        job_name=job_name.strip(),
        job_args=dict(job_args) if job_args is not None else None,
        max_token_budget=max_token_budget,
        complexity=complexity,
    )


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
    default_risk_tier_raw = raw.get("default_risk_tier", ApprovalRiskTier.MEDIUM.value)

    try:
        default_risk_tier = ApprovalRiskTier(str(default_risk_tier_raw).lower())
    except ValueError as exc:
        tiers = ", ".join(t.value for t in ApprovalRiskTier)
        raise ConfigError(
            f"Invalid approvals default_risk_tier: {default_risk_tier_raw!r}. "
            f"Expected one of: {tiers}"
        ) from exc

    # Parse gated tools
    gated_tools_raw = raw.get("gated_tools", {})
    gated_tools: dict[str, GatedToolConfig] = {}

    for tool_name, tool_cfg in gated_tools_raw.items():
        if not isinstance(tool_cfg, dict):
            tool_cfg = {}
        expiry_hours = tool_cfg.get("expiry_hours")
        risk_tier_raw = tool_cfg.get("risk_tier")
        risk_tier: ApprovalRiskTier | None = None
        if risk_tier_raw is not None:
            try:
                risk_tier = ApprovalRiskTier(str(risk_tier_raw).lower())
            except ValueError as exc:
                tiers = ", ".join(t.value for t in ApprovalRiskTier)
                raise ConfigError(
                    f"Invalid approvals risk_tier for gated tool {tool_name!r}: "
                    f"{risk_tier_raw!r}. Expected one of: {tiers}"
                ) from exc

        gated_tools[tool_name] = GatedToolConfig(
            expiry_hours=expiry_hours,
            risk_tier=risk_tier,
        )

    return ApprovalConfig(
        enabled=enabled,
        default_expiry_hours=default_expiry_hours,
        default_risk_tier=default_risk_tier,
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


def _messenger_bot_scope_enabled(module_cfg: dict[str, Any]) -> bool:
    """Return whether a messenger channel module's bot scope is enabled."""
    bot_cfg = module_cfg.get("bot")
    if not isinstance(bot_cfg, dict):
        # If absent or malformed, defer detailed validation to module schemas.
        return True
    return bool(bot_cfg.get("enabled", True))


def _validate_messenger_requirements(name: str, modules: dict[str, dict[str, Any]]) -> None:
    """Enforce minimum messenger delivery-module requirements."""
    if name != "messenger":
        return

    delivery_modules = [mod for mod in ("telegram", "email") if mod in modules]
    if not delivery_modules:
        raise ConfigError(
            "Messenger butler requires at least one delivery module: "
            "[modules.telegram] and/or [modules.email]."
        )

    if not any(_messenger_bot_scope_enabled(modules[mod]) for mod in delivery_modules):
        raise ConfigError(
            "Messenger butler requires at least one enabled bot credential scope "
            "(modules.telegram.bot.enabled or modules.email.bot.enabled)."
        )


def list_butlers(roster_dir: Path | None = None) -> list[ButlerConfig]:
    """Discover all butlers from the roster directory.

    Scans ``roster/*/`` for directories containing a ``butler.toml`` and returns
    the parsed configs sorted by name.

    Parameters
    ----------
    roster_dir:
        Path to the roster directory. Defaults to ``<repo>/roster/``.

    Returns
    -------
    list[ButlerConfig]
        Parsed configs sorted by butler name.
    """
    if roster_dir is None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        roster_dir = repo_root / "roster"

    if not roster_dir.is_dir():
        return []

    configs: list[ButlerConfig] = []
    for entry in sorted(roster_dir.iterdir()):
        if not entry.is_dir():
            continue
        toml_path = entry / "butler.toml"
        if not toml_path.exists():
            continue
        try:
            configs.append(load_config(entry))
        except ConfigError:
            pass

    return configs


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

    # --- [butler].type field ---
    raw_type = butler_section.get("type", ButlerType.BUTLER.value)
    if not isinstance(raw_type, str):
        raise ConfigError("butler.type must be a string when set")
    try:
        butler_type = ButlerType(raw_type.strip().lower())
    except ValueError as exc:
        valid = ", ".join(t.value for t in ButlerType)
        raise ConfigError(f"Invalid butler.type: {raw_type!r}. Expected one of: {valid}") from exc

    # --- [butler.permissions] sub-section ---
    permissions_section = butler_section.get("permissions", {})
    if permissions_section is None:
        permissions_section = {}
    if not isinstance(permissions_section, dict):
        raise ConfigError("butler.permissions must be a table")
    raw_cross_butler = permissions_section.get("cross_butler_access", [])
    if not isinstance(raw_cross_butler, list):
        raise ConfigError("butler.permissions.cross_butler_access must be a list of strings")
    cross_butler_access: list[str] = []
    for i, entry in enumerate(raw_cross_butler):
        if not isinstance(entry, str):
            raise ConfigError(f"butler.permissions.cross_butler_access[{i}] must be a string")
        normalized_entry = entry.strip()
        if not normalized_entry:
            raise ConfigError(
                f"butler.permissions.cross_butler_access[{i}] must be a non-empty string"
            )
        cross_butler_access.append(normalized_entry)
    permissions_config = PermissionsConfig(cross_butler_access=cross_butler_access)

    # --- [butler.db] sub-section ---
    db_section = butler_section.get("db", {})
    db_name = str(db_section.get("name", "butlers")).strip()
    if not db_name:
        raise ConfigError("butler.db.name must be a non-empty string")

    db_schema_raw = db_section.get("schema")
    db_schema: str | None = None
    if db_schema_raw is not None:
        if not isinstance(db_schema_raw, str):
            raise ConfigError("butler.db.schema must be a string when set")
        normalized_schema = db_schema_raw.strip()
        if not normalized_schema:
            raise ConfigError("butler.db.schema must be a non-empty string when set")
        if _DB_SCHEMA_PATTERN.fullmatch(normalized_schema) is None:
            raise ConfigError(
                "Invalid butler.db.schema: "
                f"{db_schema_raw!r}. Expected a valid SQL identifier-style value."
            )
        db_schema = normalized_schema

    if db_name == _CONSOLIDATED_DB_NAME and db_schema is None:
        # Default schema to butler name for one-db topology
        db_schema = name

    # --- [butler.env] sub-section ---
    env_section = butler_section.get("env", {})
    env_required = list(env_section.get("required", []))
    env_optional = list(env_section.get("optional", []))

    # --- [butler.shutdown] sub-section ---
    shutdown_section = butler_section.get("shutdown", {})
    shutdown_timeout_s = float(shutdown_section.get("timeout_s", 30.0))

    # --- [butler.logging] sub-section ---
    logging_section = butler_section.get("logging", {})
    log_level = str(logging_section.get("level", "INFO")).upper()
    log_format = str(logging_section.get("format", "text")).lower()
    if log_format not in ("text", "json"):
        raise ConfigError(
            f"Invalid butler.logging.format: {log_format!r}. Expected 'text' or 'json'."
        )
    log_root = logging_section.get("log_root")
    logging_config = LoggingConfig(
        level=log_level,
        format=log_format,
        log_root=log_root,
    )

    # --- [butler.switchboard] sub-section ---
    switchboard_section = butler_section.get("switchboard", {})
    switchboard_url: str | None = switchboard_section.get("url")
    # Default: derive from the Switchboard butler's known port (41100)
    # unless this butler IS the switchboard.
    if switchboard_url is None and name != "switchboard":
        switchboard_url = os.environ.get("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")

    # --- [butler.security] sub-section ---
    security_section = butler_section.get("security", {})
    raw_trusted = security_section.get("trusted_route_callers")
    if raw_trusted is not None:
        if isinstance(raw_trusted, list):
            trusted_route_callers = tuple(
                str(c).strip() for c in raw_trusted if isinstance(c, str) and c.strip()
            )
        else:
            raise ConfigError("butler.security.trusted_route_callers must be a list of strings")
    else:
        trusted_route_callers = DEFAULT_TRUSTED_ROUTE_CALLERS

    # --- [butler.storage] ---
    # All blob storage parameters (endpoint_url, bucket, region, credentials)
    # are resolved at runtime from the CredentialStore (dashboard secrets UI).
    # No TOML or env-var config needed.
    storage_config = StorageConfig()

    # --- [butler.scheduler] sub-section ---
    scheduler_section = butler_section.get("scheduler", {})
    raw_tick_interval = scheduler_section.get("tick_interval_seconds", 60)
    tick_interval_seconds = int(raw_tick_interval)
    if tick_interval_seconds <= 0:
        raise ConfigError(
            f"Invalid butler.scheduler.tick_interval_seconds: {tick_interval_seconds!r}. "
            "Must be a positive integer."
        )
    raw_heartbeat_interval = scheduler_section.get("heartbeat_interval_seconds", 120)
    heartbeat_interval_seconds = int(raw_heartbeat_interval)
    if heartbeat_interval_seconds <= 0:
        raise ConfigError(
            f"Invalid butler.scheduler.heartbeat_interval_seconds: {heartbeat_interval_seconds!r}. "
            "Must be a positive integer."
        )
    # Switchboard URL for liveness reporter: env var > toml > default
    _default_sb_url = os.environ.get("BUTLERS_SWITCHBOARD_URL", "http://localhost:41200")
    switchboard_liveness_url = scheduler_section.get("switchboard_url", _default_sb_url)
    scheduler_config = SchedulerConfig(
        tick_interval_seconds=tick_interval_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        switchboard_url=switchboard_liveness_url,
    )

    # --- [buffer] top-level section ---
    buffer_section = data.get("buffer", {})
    buffer_config = BufferConfig(
        queue_capacity=int(buffer_section.get("queue_capacity", 100)),
        worker_count=int(buffer_section.get("worker_count", 1)),
        scanner_interval_s=int(buffer_section.get("scanner_interval_s", 30)),
        scanner_grace_s=int(buffer_section.get("scanner_grace_s", 10)),
        scanner_batch_size=int(buffer_section.get("scanner_batch_size", 50)),
        max_consecutive_same_tier=int(buffer_section.get("max_consecutive_same_tier", 10)),
    )

    # --- [[butler.schedule]] array ---
    raw_schedules = butler_section.get("schedule", [])
    schedules: list[ScheduleConfig] = []
    for i, entry in enumerate(raw_schedules):
        schedules.append(_parse_schedule_entry(entry, i))

    # --- [modules.*] sections ---
    modules: dict[str, dict] = {}
    raw_modules = data.get("modules", {})
    for mod_name, mod_cfg in raw_modules.items():
        modules[mod_name] = dict(mod_cfg) if isinstance(mod_cfg, dict) else {}
    _validate_messenger_requirements(name, modules)

    # --- [oauth.<provider>] top-level sections ---
    # Each [oauth.<provider>] table declares the OAuth scopes this butler
    # requires from the named provider.  Consumers (e.g. the dashboard OAuth
    # router) union all butler declarations to build the authorization URL.
    oauth: dict[str, list[str]] = {}
    raw_oauth = data.get("oauth", {})
    if raw_oauth and not isinstance(raw_oauth, dict):
        raise ConfigError(
            f"[oauth] must be a table of provider sections, got {type(raw_oauth).__name__}"
        )
    if isinstance(raw_oauth, dict):
        for provider_name, provider_cfg in raw_oauth.items():
            if not isinstance(provider_cfg, dict):
                raise ConfigError(
                    f"[oauth.{provider_name}] must be a table, got {type(provider_cfg).__name__}"
                )
            raw_scopes = provider_cfg.get("scopes", [])
            if not isinstance(raw_scopes, list):
                raise ConfigError(f"[oauth.{provider_name}].scopes must be a list of strings")
            validated: list[str] = []
            for i, scope in enumerate(raw_scopes):
                if not isinstance(scope, str):
                    raise ConfigError(f"[oauth.{provider_name}].scopes[{i}] must be a string")
                stripped = scope.strip()
                if not stripped:
                    raise ConfigError(
                        f"[oauth.{provider_name}].scopes[{i}] must be a non-empty string"
                    )
                validated.append(stripped)
            oauth[provider_name] = validated

    # --- Reject top-level [runtime] section ---
    # The runtime adapter type is fixed for the entire roster; keeping a
    # per-butler toggle in git invites silent drift and offers no consumer
    # differentiation. See DEFAULT_RUNTIME_TYPE in butlers.core.runtimes.
    if "runtime" in data:
        raise ConfigError(
            "Top-level [runtime] section is no longer supported. "
            "The runtime adapter type is fixed to DEFAULT_RUNTIME_TYPE in "
            "butlers.core.runtimes. Delete the [runtime] section from "
            "butler.toml."
        )

    # --- [butler.runtime_seed] sub-section (new canonical name) ---
    runtime_seed = _parse_runtime_seed(butler_section)

    return ButlerConfig(
        name=name,
        port=port,
        description=description,
        type=butler_type,
        permissions=permissions_config,
        db_name=db_name,
        db_schema=db_schema,
        logging=logging_config,
        runtime_seed=runtime_seed,
        schedules=schedules,
        modules=modules,
        env_required=env_required,
        env_optional=env_optional,
        shutdown_timeout_s=shutdown_timeout_s,
        switchboard_url=switchboard_url,
        trusted_route_callers=trusted_route_callers,
        storage=storage_config,
        buffer=buffer_config,
        scheduler=scheduler_config,
        oauth=oauth,
    )
