"""Metrics module — opt-in Prometheus integration for butler instances.

Lets any butler define and emit named metrics (counter, gauge, histogram)
via MCP tools, and query historical data via PromQL against the Prometheus
HTTP API.

Write-side emission uses the OTEL SDK via ``get_meter()`` (reuses the
existing MeterProvider/OTLP pipeline). Read-side queries hit the Prometheus
HTTP API via ``httpx``. Metric definitions are persisted to the butler's
existing state store (KV JSONB) for restart durability — no migrations.
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from butlers.core.metrics import get_meter
from butlers.modules.base import Module
from butlers.modules.metrics.prometheus import async_query, async_query_range
from butlers.modules.metrics.storage import count_definitions, load_all_definitions, save_definition

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# Regex for valid bare metric names: lowercase, starts with a letter,
# may contain digits and underscores.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Valid metric type strings accepted by _build_instrument.
_VALID_TYPES = frozenset({"counter", "gauge", "histogram"})

# Hard cap on defined metrics per butler.
_MAX_METRICS = 1000


class MetricsModuleConfig(BaseModel):
    """Configuration for the Metrics module."""

    model_config = ConfigDict(extra="forbid")

    prometheus_query_url: str = Field(
        description=(
            "Base URL of the Prometheus-compatible HTTP API endpoint "
            "(e.g. 'http://lgtm:9090'). Used for PromQL read queries only."
        )
    )


class MetricsModule(Module):
    """Opt-in module providing Prometheus metrics define/emit/query MCP tools.

    Metric definitions are persisted to the butler's state store under keys
    ``metrics_catalogue:<name>`` so they survive daemon restarts. Instruments
    are rebuilt from the state store during ``on_startup``.

    Hard cap: 1,000 defined metrics per butler.

    Instrument cache
    ----------------
    ``_instrument_cache`` maps bare metric names to ``(full_name, instrument)``
    tuples, where *full_name* is the OTEL instrument name (e.g.
    ``butler_finance_api_calls``) and *instrument* is the live OTEL object.
    """

    def __init__(self) -> None:
        self._config: MetricsModuleConfig | None = None
        self._db: Any = None
        self._butler_name: str | None = None
        self._pool: asyncpg.Pool | None = None
        # Maps bare name → (full_otel_name, OTELInstrument)
        self._instrument_cache: dict[str, tuple[str, Any]] = {}
        # Maps bare name → definition dict (name, type, help, labels, registered_at)
        self._definition_cache: dict[str, dict[str, Any]] = {}

    @property
    def name(self) -> str:
        return "metrics"

    @property
    def config_schema(self) -> type[BaseModel]:
        return MetricsModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register metrics MCP tools on the butler's FastMCP server."""
        self._config = self._coerce_config(config)
        self._db = db
        module = self  # capture for closures

        @mcp.tool()
        async def metrics_query(query: str, time: str | None = None) -> list[dict]:
            """Execute an instant PromQL query against the configured Prometheus endpoint.

            Returns the vector result on success.  On error (network failure,
            invalid PromQL, Prometheus unavailable), returns a list with a
            single ``{"error": "..."}`` dict describing the problem.

            Parameters
            ----------
            query:
                PromQL expression, e.g. ``up`` or ``rate(http_requests_total[5m])``.
            time:
                Optional evaluation timestamp (RFC 3339 or Unix epoch string).
                Defaults to Prometheus server time when omitted.
            """
            if module._config is None:
                return [{"error": "MetricsModule is not configured (prometheus_query_url missing)"}]
            return await async_query(module._config.prometheus_query_url, query, time)

        @mcp.tool()
        async def metrics_query_range(
            query: str,
            start: str,
            end: str,
            step: str,
        ) -> list[dict]:
            """Execute a range PromQL query against the configured Prometheus endpoint.

            Returns the matrix result on success.  On error, returns a list
            with a single ``{"error": "..."}`` dict.

            Parameters
            ----------
            query:
                PromQL expression.
            start:
                Range start (RFC 3339 or Unix epoch string).
            end:
                Range end (RFC 3339 or Unix epoch string).
            step:
                Resolution step, e.g. ``"15s"``, ``"1m"``, ``"300"``.
            """
            if module._config is None:
                return [{"error": "MetricsModule is not configured (prometheus_query_url missing)"}]
            return await async_query_range(
                module._config.prometheus_query_url, query, start, end, step
            )

        @mcp.tool()
        async def metrics_list() -> list[dict]:
            """Return all metric definitions registered with this butler.

            Each definition is a dict with at minimum ``name``, ``type``,
            ``help``, ``labels``, and ``registered_at`` keys (as originally
            stored by ``metrics_define``).  Returns an empty list when no
            definitions have been saved yet.
            """
            if module._db is None:
                return []
            return await load_all_definitions(module._db)

        # Ensure pool and butler_name are populated (on_startup runs first, but
        # register_tools may be called in contexts where on_startup did not run,
        # e.g. tests — fall back gracefully).
        if self._pool is None:
            self._pool = getattr(db, "pool", None)
        if self._butler_name is None:
            schema: str | None = getattr(db, "schema", None)
            if schema:
                self._butler_name = schema.replace("-", "_")

        module = self  # capture for closures

        @mcp.tool()
        async def metrics_define(
            name: str,
            metric_type: str,
            help: str,
            labels: list[str] | None = None,
        ) -> dict[str, Any]:
            """Define a new named metric (counter, gauge, or histogram) for this butler.

            Creates the metric instrument in the OTEL pipeline and persists the
            definition to the butler's state store so it survives daemon restarts.
            Re-defining an existing metric (idempotent) returns the cached entry
            without touching the state store.

            **Cardinality advisory**: avoid using high-cardinality values as label
            keys — do NOT use user IDs, UUIDs, request IDs, or other unbounded
            identifiers as label names.  High-cardinality labels create a unique
            time-series per distinct combination, which will exhaust Prometheus
            storage and degrade query performance.  Good label names are low-
            cardinality enum-like values (e.g. ``status``, ``method``, ``env``).

            Parameters
            ----------
            name:
                Bare metric name, e.g. ``api_calls``.  Must match
                ``^[a-z][a-z0-9_]*$`` (lowercase, starts with a letter).
            metric_type:
                One of ``"counter"``, ``"gauge"``, or ``"histogram"``.
            help:
                Human-readable description of what the metric measures.
            labels:
                Optional list of label key names (low-cardinality only — see
                cardinality advisory above).  Defaults to an empty list.

            Returns
            -------
            dict
                ``{"ok": true, "name": ..., "type": ..., "full_name": ...,
                  "registered_at": ..., "cached": <bool>}`` on success, or
                ``{"error": "..."}`` on validation or cap failure.
            """
            # Validate name format.
            if not module._validate_name(name):
                return {
                    "error": (
                        f"Invalid metric name {name!r}: must match ^[a-z][a-z0-9_]*$ "
                        "(lowercase, start with a letter, letters/digits/underscores only)"
                    )
                }

            # Validate metric type.
            if metric_type not in _VALID_TYPES:
                return {
                    "error": (
                        f"Invalid metric_type {metric_type!r}; "
                        f"must be one of {sorted(_VALID_TYPES)}"
                    )
                }

            # Idempotency: return cached entry if already defined.
            if name in module._instrument_cache:
                full_name, _ = module._instrument_cache[name]
                return {
                    "ok": True,
                    "name": name,
                    "type": metric_type,
                    "full_name": full_name,
                    "cached": True,
                }

            # Check the 1,000-metric cap.
            if module._pool is None:
                return {"error": "MetricsModule pool is not available; cannot persist definition"}
            current_count = await count_definitions(module._pool)
            if current_count >= _MAX_METRICS:
                return {
                    "error": (
                        f"Metric cap reached: this butler already has {current_count} "
                        f"defined metrics (limit {_MAX_METRICS}).  Remove unused metrics "
                        "before adding new ones."
                    )
                }

            # Build the fully-qualified OTEL name.
            if module._butler_name:
                full_name = module._full_name(module._butler_name, name)
            else:
                full_name = name

            # Create the OTEL instrument.
            instrument = module._build_instrument(full_name, metric_type, help)

            # Persist to state store.
            now_iso = datetime.datetime.now(datetime.UTC).isoformat()
            defn: dict[str, Any] = {
                "name": name,
                "type": metric_type,
                "help": help,
                "labels": labels or [],
                "registered_at": now_iso,
            }
            await save_definition(module._pool, name, defn)

            # Update the in-process caches.
            module._instrument_cache[name] = (full_name, instrument)
            module._definition_cache[name] = defn

            return {
                "ok": True,
                "name": name,
                "type": metric_type,
                "full_name": full_name,
                "registered_at": now_iso,
                "cached": False,
            }

        @mcp.tool()
        async def metrics_emit(
            name: str,
            value: float,
            labels: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            """Emit a single observation to a previously defined metric.

            Looks up the metric by *name* in the in-process instrument cache and
            records the observation via the OTEL SDK.  The metric must have been
            registered with ``metrics_define`` first.

            Parameters
            ----------
            name:
                Bare metric name previously registered with ``metrics_define``.
            value:
                Numeric observation.  Must be >= 0 for counters and histograms;
                any float is accepted for gauges (UpDownCounter).
            labels:
                Dict mapping label key names to string values.  Must contain
                exactly the keys declared in the metric's ``labels`` list — no
                extra keys, no missing keys.  Omit or pass ``null``/``{}``
                when the metric was defined with an empty labels list.

            Returns
            -------
            dict
                ``{"ok": true}`` on success, or ``{"error": "..."}`` on
                validation failure.
            """
            # Look up instrument in cache.
            if name not in module._instrument_cache:
                return {"error": (f"Unknown metric {name!r}: define it first with metrics_define")}

            full_name, instrument = module._instrument_cache[name]

            # Look up the stored definition for type and label validation.
            defn = module._definition_cache.get(name, {})
            metric_type: str = defn.get("type", "unknown")
            declared_labels: list[str] = defn.get("labels") or []

            # Validate value constraints.
            if metric_type in ("counter", "histogram") and value < 0:
                return {
                    "error": (
                        f"Value {value!r} is invalid for {metric_type} metric {name!r}: "
                        "must be >= 0"
                    )
                }

            # Validate labels against the declared label set.
            provided_labels: dict[str, str] = labels or {}
            declared_set = set(declared_labels)
            provided_set = set(provided_labels.keys())

            if provided_set != declared_set:
                missing = declared_set - provided_set
                extra = provided_set - declared_set
                parts: list[str] = []
                if missing:
                    parts.append(f"missing keys: {sorted(missing)}")
                if extra:
                    parts.append(f"extra keys: {sorted(extra)}")
                return {
                    "error": (
                        f"Label mismatch for metric {name!r}: {'; '.join(parts)}. "
                        f"Expected exactly: {sorted(declared_set)}"
                    )
                }

            # Emit the observation.
            attributes = provided_labels if provided_labels else None
            if metric_type == "histogram":
                instrument.record(value, attributes=attributes)
            else:
                instrument.add(value, attributes=attributes)

            return {"ok": True}

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Store config, derive butler name, and restore instrument cache.

        Derives ``_butler_name`` from ``db.schema`` (hyphens → underscores),
        stores the asyncpg pool, then loads all persisted metric definitions
        from the state store and rebuilds the in-process OTEL instrument cache.
        """
        self._config = self._coerce_config(config)
        self._db = db

        # Derive butler name from the DB schema (e.g. "my-butler" → "my_butler").
        schema: str | None = getattr(db, "schema", None)
        if schema:
            self._butler_name = schema.replace("-", "_")
        else:
            self._butler_name = None

        # Store pool for state-store access.
        self._pool = getattr(db, "pool", None)

        # Rebuild instrument cache from persisted definitions.
        await self._restore_instrument_cache()

        logger.debug(
            "MetricsModule: startup complete, butler=%s, instruments_restored=%d",
            self._butler_name,
            len(self._instrument_cache),
        )

    async def on_shutdown(self) -> None:
        """Release state references and clear instrument cache."""
        self._config = None
        self._db = None
        self._butler_name = None
        self._pool = None
        self._instrument_cache.clear()
        self._definition_cache.clear()
        logger.debug("MetricsModule: shutdown complete")

    # ------------------------------------------------------------------
    # Internal helpers — naming
    # ------------------------------------------------------------------

    @staticmethod
    def _full_name(butler_schema: str, metric_name: str) -> str:
        """Build the fully-qualified OTEL instrument name.

        Parameters
        ----------
        butler_schema:
            The butler's DB schema name (hyphens are replaced with underscores).
        metric_name:
            Bare metric name (must pass ``_validate_name``).

        Returns
        -------
        str
            e.g. ``butler_finance_api_calls`` for schema ``finance`` and
            name ``api_calls``.
        """
        safe_schema = butler_schema.replace("-", "_")
        return f"butler_{safe_schema}_{metric_name}"

    @staticmethod
    def _validate_name(name: str) -> bool:
        """Return True iff *name* is a valid bare metric name.

        Valid names match ``^[a-z][a-z0-9_]*$``:
        - Must start with a lowercase letter.
        - May contain lowercase letters, digits, and underscores.
        - No uppercase letters, leading digits, spaces, or hyphens.
        """
        return bool(_NAME_RE.match(name))

    # ------------------------------------------------------------------
    # Internal helpers — OTEL instrument construction
    # ------------------------------------------------------------------

    def _build_instrument(
        self,
        full_name: str,
        metric_type: str,
        help_text: str,
    ) -> Any:
        """Create and return an OTEL instrument for the given parameters.

        Parameters
        ----------
        full_name:
            Fully-qualified OTEL instrument name (e.g. ``butler_finance_api_calls``).
        metric_type:
            One of ``"counter"``, ``"gauge"``, or ``"histogram"``.
        help_text:
            Human-readable description for the instrument.

        Returns
        -------
        OTELInstrument
            A live ``Counter``, ``UpDownCounter``, or ``Histogram`` from the
            global MeterProvider.

        Raises
        ------
        ValueError
            If *metric_type* is not one of the supported types.
        """
        meter = get_meter()
        if metric_type == "counter":
            return meter.create_counter(name=full_name, description=help_text)
        if metric_type == "gauge":
            return meter.create_up_down_counter(name=full_name, description=help_text)
        if metric_type == "histogram":
            return meter.create_histogram(name=full_name, description=help_text)
        raise ValueError(
            f"Unsupported metric_type {metric_type!r}; expected one of {sorted(_VALID_TYPES)}"
        )

    # ------------------------------------------------------------------
    # Internal helpers — cache population
    # ------------------------------------------------------------------

    async def _restore_instrument_cache(self) -> None:
        """Load all persisted definitions and build OTEL instruments.

        Safe to call with an empty state store — returns immediately with
        an empty cache.  Also safe to call when ``_pool`` is None (e.g. during
        tests that do not wire up a real DB pool) — skips restoration.
        """
        if self._pool is None:
            logger.debug("MetricsModule: no pool available, skipping instrument cache restoration")
            return

        definitions = await load_all_definitions(self._pool)

        for defn in definitions:
            name: str | None = defn.get("name")
            metric_type: str | None = defn.get("type")
            help_text: str = defn.get("help", "")

            if not isinstance(name, str) or not self._validate_name(name):
                logger.warning(
                    "MetricsModule: skipping invalid definition name=%r during restore", name
                )
                continue

            if metric_type not in _VALID_TYPES:
                logger.warning(
                    "MetricsModule: skipping unknown metric type=%r for name=%r during restore",
                    metric_type,
                    name,
                )
                continue

            if self._butler_name:
                full_name = self._full_name(self._butler_name, name)
            else:
                full_name = name

            try:
                instrument = self._build_instrument(full_name, metric_type, help_text)
                self._instrument_cache[name] = (full_name, instrument)
                self._definition_cache[name] = defn
            except Exception:
                logger.exception(
                    "MetricsModule: failed to build instrument for name=%r during restore", name
                )

    # ------------------------------------------------------------------
    # Internal helpers — config coercion
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_config(config: Any) -> MetricsModuleConfig | None:
        """Coerce raw dict config to MetricsModuleConfig, or return None."""
        if config is None:
            return None
        if isinstance(config, MetricsModuleConfig):
            return config
        if isinstance(config, dict):
            return MetricsModuleConfig(**config)
        raise TypeError(f"Unsupported config type for MetricsModule: {type(config).__name__}")


__all__ = [
    "MetricsModule",
    "MetricsModuleConfig",
    "async_query",
    "async_query_range",
]
