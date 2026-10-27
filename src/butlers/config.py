"""Butler configuration loading and validation.

Reads butler.toml from a config directory, validates it once through the strict
pydantic models below (``extra="forbid"``: an unknown key is an error), and
returns a ButlerConfig dataclass whose sections and schedules are those typed
models. Nothing downstream re-validates or re-shapes them.
"""

from __future__ import annotations

import enum
import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from butlers.core.mcp_urls import resolve_cross_container_mcp_url

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


class _StrictModel(BaseModel):
    """Base for every butler.toml section: unknown keys are errors, not silently dropped."""

    model_config = ConfigDict(extra="forbid")


# Keys the core-daemon spec says a butler.toml may still carry: they are accepted and ignored,
# never surfaced as config. Everything else unknown stays an error.
_IGNORED_LEGACY_BLOB_KEYS = ("blob_dir", "blob_storage_dir")
_IGNORED_LEGACY_BUTLER_KEYS = ("runtime", "seed_configs", *_IGNORED_LEGACY_BLOB_KEYS)


def _drop_keys(data: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(data, dict):
        return data
    return {key: value for key, value in data.items() if key not in keys}


def _non_empty_stripped(value: str, what: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{what} must be a non-empty string")
    return stripped


class PermissionsConfig(_StrictModel):
    """Cross-butler permissions from [butler.permissions] section.

    Declares which other agents this agent may connect to or act on behalf of.
    Staffers may declare wildcard (``["*"]``) or scoped access lists.
    Butlers default to an empty list (no cross-butler access).
    """

    cross_butler_access: list[str] = Field(default_factory=list)

    @field_validator("cross_butler_access")
    @classmethod
    def _strip_entries(cls, value: list[str]) -> list[str]:
        return [_non_empty_stripped(entry, "cross_butler_access entry") for entry in value]


class LoggingConfig(_StrictModel):
    """Logging configuration from [butler.logging] section."""

    level: str = "INFO"
    format: Literal["text", "json"] = "text"
    log_root: str | None = None

    @field_validator("level", mode="before")
    @classmethod
    def _upper_level(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("format", mode="before")
    @classmethod
    def _lower_format(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value


_ALLOWED_SCHEDULE_COMPLEXITIES: frozenset[str] = frozenset(
    {"reasoning", "workhorse", "cheap", "specialty", "local", "legacy"}
)


class ScheduleSpec(_StrictModel):
    """A single scheduled task entry from [[butler.schedule]].

    The one place a schedule is validated: ``load_config`` builds these from TOML and
    ``sync_schedules`` consumes them as-is. ``task_type='deadline'`` carries the
    deadline-specific fields; a ``cron`` task must not set them.
    """

    name: str
    cron: str
    task_type: Literal["cron", "deadline"] = "cron"
    prompt: str | None = None
    dispatch_mode: ScheduleDispatchMode = ScheduleDispatchMode.PROMPT
    job_name: str | None = None
    job_args: dict[str, Any] | None = None
    max_token_budget: StrictInt | None = Field(default=None, gt=0)
    complexity: str | None = None
    continuity: StrictBool = False
    # Explicit IANA zone for this schedule's cron. None follows the owner timezone. The literal
    # "UTC" is the owner-timezone sentinel (see scheduler._effective_schedule_timezone), so pin
    # real UTC with the alias "Etc/UTC".
    timezone: str | None = None
    target_date: date | None = None
    lead_time_days: StrictInt | None = None
    alert_thresholds: list[dict[str, Any]] | None = None

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, value: str) -> str:
        _non_empty_stripped(value, "name")
        return value

    @field_validator("cron")
    @classmethod
    def _cron_valid(cls, value: str) -> str:
        _non_empty_stripped(value, "cron")
        if not croniter.is_valid(value):
            raise ValueError(f"{value!r} is not a valid cron expression")
        return value

    @field_validator("dispatch_mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("complexity", mode="before")
    @classmethod
    def _normalize_complexity(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_SCHEDULE_COMPLEXITIES:
            raise ValueError(
                f"{value!r} is not valid. Expected one of {sorted(_ALLOWED_SCHEDULE_COMPLEXITIES)}."
            )
        return normalized

    @field_validator("timezone")
    @classmethod
    def _timezone_is_iana(cls, value: str | None) -> str | None:
        if value is None:
            return None
        zone = value.strip()
        if not zone or zone.upper() == "UTC":
            raise ValueError(
                f"{value!r} is the follow-the-owner sentinel and would be silently re-resolved "
                'to the owner timezone; use "Etc/UTC" to pin UTC or omit the key'
            )
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
            raise ValueError(f"{value!r} is not an IANA zone name") from exc
        return zone

    @model_validator(mode="after")
    def _mode_specific_fields(self) -> Self:
        if self.dispatch_mode == ScheduleDispatchMode.PROMPT:
            if self.prompt is None or not self.prompt.strip():
                raise ValueError("dispatch_mode='prompt' requires non-empty prompt")
            if self.job_name is not None:
                raise ValueError("job_name is only valid when dispatch_mode='job'")
            if self.job_args is not None:
                raise ValueError("job_args is only valid when dispatch_mode='job'")
        else:
            if self.prompt is not None:
                raise ValueError("prompt is not allowed when dispatch_mode='job'")
            if self.job_name is None or not self.job_name.strip():
                raise ValueError("dispatch_mode='job' requires non-empty job_name")
            if self.continuity:
                raise ValueError("continuity is only valid when dispatch_mode='prompt'")
            self.job_name = self.job_name.strip()
        return self

    @model_validator(mode="after")
    def _task_type_fields(self) -> Self:
        deadline_fields = {
            "target_date": self.target_date,
            "lead_time_days": self.lead_time_days,
            "alert_thresholds": self.alert_thresholds,
        }
        if self.task_type == "cron":
            stray = sorted(key for key, value in deadline_fields.items() if value is not None)
            if stray:
                raise ValueError(f"{', '.join(stray)} only valid when task_type='deadline'")
            return self
        if self.target_date is None:
            raise ValueError("target_date is required when task_type='deadline'")
        if self.lead_time_days is None or self.lead_time_days <= 0:
            raise ValueError(
                f"lead_time_days must be a positive integer (got {self.lead_time_days!r})"
            )
        if not self.alert_thresholds:
            raise ValueError("alert_thresholds must be a non-empty list of threshold tables")
        return self


class RuntimeSeedConfig(_StrictModel):
    """Sole butler-scoped runtime configuration from ``[butler.runtime_seed]``.

    Used on first boot to seed the per-schema ``runtime_config`` DB table and,
    thereafter, as the Git authority for ``core_groups`` plus the in-memory
    fallback when the ``RuntimeConfigAccessor`` is unavailable or its cache is
    empty. This is the only butler-scoped runtime config source in git.

    Fields:

    - ``core_groups`` / ``catalog_read_sensitivity`` /
      ``max_concurrent_sessions`` / ``max_queued_sessions`` /
      ``tool_exposure_policy`` are the operational
      tuning knobs that map to the DB-backed
      ``runtime_config`` row. Operational fields prefer the DB row via
      :class:`RuntimeConfigAccessor` and falls back to the values here when
      no accessor is wired. ``tool_exposure_policy`` is hot: the DB-backed
      row is authoritative per invocation, and this seed value only applies
      before the row is first seeded.
    - ``liveness_ttl_seconds`` / ``route_contract_min`` / ``route_contract_max``
      are registration-only and are not stored in ``runtime_config``.

    Model selection, per-session timeouts, and runtime CLI args live in
    ``public.model_catalog`` (resolved per spawn). The runtime adapter type
    is fixed for the whole roster — see ``DEFAULT_RUNTIME_TYPE`` in
    :mod:`butlers.core.runtimes`. See ``about/heart-and-soul/vision.md``
    Rule 5 for the ownership model.
    """

    core_groups: tuple[str, ...] | None = None
    catalog_read_sensitivity: Literal["normal", "internal", "confidential"] = "normal"
    max_concurrent_sessions: PositiveInt = 3
    max_queued_sessions: PositiveInt = 10
    liveness_ttl_seconds: int = 300
    route_contract_min: int = 1
    route_contract_max: int = 1
    tool_exposure_policy: Literal["eager_filtered", "auto"] = "eager_filtered"

    @model_validator(mode="before")
    @classmethod
    def _reject_retired_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key, destination in _RETIRED_RUNTIME_SEED_KEYS.items():
                if key in data:
                    raise ValueError(
                        f"runtime_seed.{key} is no longer supported. Move it to {destination}."
                    )
        return data

    @field_validator("core_groups")
    @classmethod
    def _strip_groups(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        return tuple(_non_empty_stripped(entry, "core_groups entry") for entry in value)


_RETIRED_RUNTIME_SEED_KEYS: dict[str, str] = {
    "model": "the model catalog",
    "runtime_type": "DEFAULT_RUNTIME_TYPE in butlers.core.runtimes (fixed for the whole roster)",
    "args": "the model catalog",
    "session_timeout_s": "the model catalog",
}


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


class BufferConfig(_StrictModel):
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


class SchedulerConfig(_StrictModel):
    """Scheduler loop configuration from [butler.scheduler] section.

    Controls the internal asyncio scheduler loop that calls tick() periodically
    to dispatch due cron tasks without relying on external heartbeat calls.

    Also controls the liveness reporter loop that periodically sends HTTP POST
    to the Switchboard's /api/switchboard/heartbeat endpoint.
    """

    tick_interval_seconds: PositiveInt = 60
    heartbeat_interval_seconds: PositiveInt = 120
    switchboard_url: str = "http://localhost:41200"


class StorageConfig(_StrictModel):
    """S3-compatible blob storage marker.

    All blob storage parameters (endpoint_url, bucket, region, credentials)
    are resolved at runtime from the CredentialStore (managed via the
    dashboard secrets UI at /secrets).  This model is kept as a
    placeholder for potential future non-secret storage settings. The legacy
    ``blob_dir`` / ``blob_storage_dir`` keys are ignored; any other key is an error.
    """

    @model_validator(mode="before")
    @classmethod
    def _ignore_legacy_blob_keys(cls, data: Any) -> Any:
        return _drop_keys(data, _IGNORED_LEGACY_BLOB_KEYS)


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
    schedules: list[ScheduleSpec] = field(default_factory=list)
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
    #   [oauth.example_provider]
    #   scopes = ["profile.read", "activity.read"]
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
    approval_config: ApprovalConfig | None,
    registered_tools: set[str],
    tool_metadata: dict[str, Any] | None = None,
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
    tool_metadata:
        Optional ``{tool_name: ToolMeta}`` map aggregated from every active
        module's ``tool_metadata()``. When given, also enforces the reverse
        direction (bu-0ynlk.1): a chat-reachable write tool — one a module
        explicitly declared with ``arg_sensitivities={"_write": True, ...}``
        (the convention ``spotify``/``steam`` already use) — that is
        registered but missing from ``gated_tools`` fails validation, closing
        the gap where a butler enables approvals but forgets to gate one of
        its own write tools. Only tools using this opt-in declaration are
        checked; tools whose owning module has not declared it are
        unaffected.

    Raises
    ------
    ConfigError
        If any gated tool names are not in *registered_tools*, or (when
        *tool_metadata* is given) if a declared write tool is registered but
        not gated.
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

    if tool_metadata:
        ungated_write_tools = sorted(
            tool_name
            for tool_name, meta in tool_metadata.items()
            if tool_name in registered_tools
            and getattr(meta, "arg_sensitivities", {}).get("_write") is True
            and tool_name not in approval_config.gated_tools
        )
        if ungated_write_tools:
            tools_str = ", ".join(ungated_write_tools)
            raise ConfigError(
                f"Chat-reachable write tool(s) not covered by approval config: "
                f"{tools_str}. These tools are declared as writes via "
                f"tool_metadata() but have no gated_tools entry — add one or "
                f"mark the tool read-only."
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


class _DbSection(_StrictModel):
    """``[butler.db]``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = CONSOLIDATED_DB_NAME
    # ``schema`` shadows a BaseModel attribute, so the field is spelled db_schema.
    db_schema: str | None = Field(default=None, alias="schema")

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, value: str) -> str:
        return _non_empty_stripped(value, "name")

    @field_validator("db_schema")
    @classmethod
    def _schema_is_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = _non_empty_stripped(value, "schema")
        if _DB_SCHEMA_PATTERN.fullmatch(stripped) is None:
            raise ValueError(f"{value!r}. Expected a valid SQL identifier-style value.")
        return stripped


class _EnvSection(_StrictModel):
    """``[butler.env]``: names of required and optional environment variables."""

    required: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)


class _ShutdownSection(_StrictModel):
    """``[butler.shutdown]``."""

    timeout_s: float = 30.0


class _SwitchboardSection(_StrictModel):
    """``[butler.switchboard]``."""

    url: str | None = None


class _SecuritySection(_StrictModel):
    """``[butler.security]``."""

    trusted_route_callers: list[str] | None = None

    @field_validator("trusted_route_callers")
    @classmethod
    def _strip_callers(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else [c.strip() for c in value if c.strip()]


class _ButlerSection(_StrictModel):
    """The ``[butler]`` table."""

    name: str
    port: int
    description: str | None = None
    type: ButlerType = ButlerType.BUTLER
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    db: _DbSection = Field(default_factory=_DbSection)
    env: _EnvSection = Field(default_factory=_EnvSection)
    shutdown: _ShutdownSection = Field(default_factory=_ShutdownSection)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    switchboard: _SwitchboardSection = Field(default_factory=_SwitchboardSection)
    security: _SecuritySection = Field(default_factory=_SecuritySection)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    runtime_seed: RuntimeSeedConfig = Field(default_factory=RuntimeSeedConfig)
    schedule: list[ScheduleSpec] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _ignore_legacy_keys(cls, data: Any) -> Any:
        # The obsolete [butler.runtime] and [butler.seed_configs] tables and the blob keys are
        # accepted and ignored (core-daemon spec); they never alter RuntimeSeedConfig.
        return _drop_keys(data, _IGNORED_LEGACY_BUTLER_KEYS)

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value


class _OAuthProvider(_StrictModel):
    """``[oauth.<provider>]``: the scopes this butler needs from the provider."""

    scopes: list[str] = Field(default_factory=list)

    @field_validator("scopes")
    @classmethod
    def _strip_scopes(cls, value: list[str]) -> list[str]:
        return [_non_empty_stripped(scope, "scope") for scope in value]


class ButlerToml(_StrictModel):
    """The whole ``butler.toml`` file. Module sections stay free-form tables.

    Each module validates its own table against its own schema at startup
    (``Module.config_schema``); this model only guarantees they are tables.
    """

    butler: _ButlerSection
    modules: dict[str, dict[str, Any]] = Field(default_factory=dict)
    oauth: dict[str, _OAuthProvider] = Field(default_factory=dict)
    buffer: BufferConfig = Field(default_factory=BufferConfig)

    @model_validator(mode="before")
    @classmethod
    def _reject_runtime_section(cls, data: Any) -> Any:
        # The runtime adapter type is fixed for the entire roster; keeping a
        # per-butler toggle in git invites silent drift and offers no consumer
        # differentiation. See DEFAULT_RUNTIME_TYPE in butlers.core.runtimes.
        if isinstance(data, dict) and "runtime" in data:
            raise ValueError(
                "Top-level [runtime] section is no longer supported. "
                "The runtime adapter type is fixed to DEFAULT_RUNTIME_TYPE in "
                "butlers.core.runtimes. Delete the [runtime] section from "
                "butler.toml."
            )
        return data


def _loc_path(loc: tuple[int | str, ...]) -> str:
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else part
    return path


def _format_validation_error(exc: ValidationError) -> str:
    """Render pydantic errors as ``Invalid butler.schedule[0].cron: ...`` lines."""
    lines: list[str] = []
    for err in exc.errors():
        path = _loc_path(err["loc"])
        if err["type"] == "missing":
            lines.append(f"Missing required field: {path}")
        elif err["type"] == "extra_forbidden":
            lines.append(f"Unknown key: {path}")
        else:
            message = err["msg"].removeprefix("Value error, ")
            lines.append(f"Invalid {path}: {message}" if path else message)
    return "; ".join(lines)


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
        If the file is missing, contains invalid TOML, has an unknown key, or
        lacks required fields.
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

    try:
        parsed = ButlerToml.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from exc

    section = parsed.butler
    name = section.name

    db_schema = section.db.db_schema
    if section.db.name == CONSOLIDATED_DB_NAME and db_schema is None:
        # Default schema to butler name for one-db topology
        db_schema = name

    # Default: derive from the Switchboard butler's known port (41100)
    # unless this butler IS the switchboard.
    switchboard_url = section.switchboard.url
    if switchboard_url is None and name != "switchboard":
        switchboard_url = os.environ.get("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
    if switchboard_url is not None:
        # Roster TOMLs explicitly describe the colocated canonical endpoint.
        # Resolve it once before notify, reconnect and fact-authority consumers
        # receive the config. Explicit remote/custom endpoints remain literal.
        endpoint = urlparse(switchboard_url)
        if (endpoint.hostname or "").lower() == "localhost" and endpoint.port == 41100:
            switchboard_url = resolve_cross_container_mcp_url(switchboard_url)

    # Switchboard URL for the liveness reporter: toml > env var > default
    scheduler = section.scheduler
    if "switchboard_url" not in scheduler.model_fields_set:
        scheduler = scheduler.model_copy(
            update={
                "switchboard_url": os.environ.get(
                    "BUTLERS_SWITCHBOARD_URL", scheduler.switchboard_url
                )
            }
        )

    raw_trusted = section.security.trusted_route_callers
    trusted_route_callers = (
        DEFAULT_TRUSTED_ROUTE_CALLERS if raw_trusted is None else tuple(raw_trusted)
    )

    _validate_messenger_requirements(name, parsed.modules)

    return ButlerConfig(
        name=name,
        port=section.port,
        description=section.description,
        type=section.type,
        permissions=section.permissions,
        db_name=section.db.name,
        db_schema=db_schema,
        logging=section.logging,
        runtime_seed=section.runtime_seed,
        schedules=section.schedule,
        modules=parsed.modules,
        env_required=section.env.required,
        env_optional=section.env.optional,
        shutdown_timeout_s=section.shutdown.timeout_s,
        switchboard_url=switchboard_url,
        trusted_route_callers=trusted_route_callers,
        storage=section.storage,
        buffer=parsed.buffer,
        scheduler=scheduler,
        oauth={provider: cfg.scopes for provider, cfg in parsed.oauth.items()},
    )
