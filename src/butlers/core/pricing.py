"""Per-model token pricing configuration.

Loads ``pricing.toml`` and exposes helpers for cost estimation.  The file
maps model IDs to their input/output per-token prices in USD so the
dashboard can display approximate session costs.

Supports two pricing formats:

* **Flat** — a single ``input_price_per_token`` / ``output_price_per_token``
  pair (the original format).
* **Tiered** — an array of ``[[models."id".tiers]]`` tables, each with a
  ``context_threshold`` (in tokens) that determines when the tier applies,
  plus optional ``cached_input_price_per_token``.

Uses :mod:`tomllib` (stdlib since Python 3.11) — no external dependencies.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

logger = logging.getLogger(__name__)

# Default location: <repo-root>/pricing.toml
_DEFAULT_PATH = Path(__file__).resolve().parents[3] / "pricing.toml"


class PricingError(Exception):
    """Raised when pricing configuration is missing or malformed."""


BillingClass = Literal["metered", "subscription", "local"]
_BILLING_CLASSES = frozenset({"metered", "subscription", "local"})


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Per-token prices (USD) for a single model (flat pricing).

    ``cached_input_price_per_token`` (cache reads) and
    ``cache_creation_price_per_token`` (cache writes) default to ``None``,
    which bills those buckets at the full input rate — a conservative
    fallback for models whose vendor does not discount cached tokens or
    whose discount is unknown.
    """

    input_price_per_token: float
    output_price_per_token: float
    cached_input_price_per_token: float | None = None
    cache_creation_price_per_token: float | None = None
    # A known zero marginal rate is materially different from an absent entry:
    # subscription/local usage is priced at $0, while an unknown model is
    # unpriced and must remain visible as such.
    billing_class: BillingClass = "metered"

    @property
    def effective_cached_input_price(self) -> float:
        return (
            self.cached_input_price_per_token
            if self.cached_input_price_per_token is not None
            else self.input_price_per_token
        )

    @property
    def effective_cache_creation_price(self) -> float:
        return (
            self.cache_creation_price_per_token
            if self.cache_creation_price_per_token is not None
            else self.input_price_per_token
        )


@dataclass(frozen=True, slots=True)
class PricingTier:
    """Per-token prices (USD) for a single context-size tier.

    Cache prices follow the same ``None`` → full-input-rate fallback as
    :class:`ModelPricing`.
    """

    context_threshold: int  # tier applies when total context >= this many tokens
    input_price_per_token: float
    output_price_per_token: float
    cached_input_price_per_token: float | None = None
    cache_creation_price_per_token: float | None = None

    @property
    def effective_cached_input_price(self) -> float:
        return (
            self.cached_input_price_per_token
            if self.cached_input_price_per_token is not None
            else self.input_price_per_token
        )

    @property
    def effective_cache_creation_price(self) -> float:
        return (
            self.cache_creation_price_per_token
            if self.cache_creation_price_per_token is not None
            else self.input_price_per_token
        )


@dataclass(frozen=True, slots=True)
class TieredModelPricing:
    """Context-tiered pricing for a model with variable rates by context size."""

    tiers: tuple[PricingTier, ...]  # sorted ascending by context_threshold
    billing_class: BillingClass = "metered"

    def tier_for_context(self, context_tokens: int) -> PricingTier:
        """Return the tier applicable for the given context size.

        Picks the highest tier whose ``context_threshold`` does not exceed
        *context_tokens*.  Falls back to the first (lowest) tier.
        """
        result = self.tiers[0]
        for tier in self.tiers:
            if context_tokens >= tier.context_threshold:
                result = tier
            else:
                break
        return result


class PricingConfig:
    """Loaded pricing configuration backed by a ``pricing.toml`` file.

    Parameters
    ----------
    models:
        Mapping of model ID to :class:`ModelPricing` or
        :class:`TieredModelPricing`.
    """

    def __init__(self, models: dict[str, ModelPricing | TieredModelPricing]) -> None:
        self._models = models

    # -- public API ---------------------------------------------------------

    @property
    def model_ids(self) -> list[str]:
        """Return a sorted list of all known model IDs."""
        return sorted(self._models)

    def get_model_pricing(self, model_id: str) -> ModelPricing | TieredModelPricing | None:
        """Return pricing for *model_id*, or ``None`` if unknown."""
        return self._models.get(model_id)

    def billing_class_for(self, model_id: str) -> BillingClass | None:
        """Return the declared billing class, or ``None`` for an unknown model."""
        pricing = self._models.get(model_id)
        return pricing.billing_class if pricing is not None else None

    def estimate_cost(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        *,
        cached_input_tokens: int = 0,
        cache_creation_tokens: int = 0,
        context_tokens: int | None = None,
    ) -> float | None:
        """Estimate the USD cost of a request.

        Parameters
        ----------
        input_tokens:
            Uncached input tokens only — cache reads/writes are passed
            separately (see the runtime usage contract in
            ``butlers.core.runtimes.base``).
        cached_input_tokens:
            Tokens served from a prompt cache (cache reads). Billed at the
            model's cached rate, or the full input rate when no cached rate
            is configured.
        cache_creation_tokens:
            Tokens written to a prompt cache (cache writes). Billed at the
            model's cache-write rate, or the full input rate when not
            configured.
        context_tokens:
            Total context size in tokens — used to select the correct tier
            for tiered models.  Defaults to ``0`` (cheapest tier) when not
            provided.

        Returns ``None`` when the model is not in the pricing table.
        """
        pricing = self._models.get(model_id)
        if pricing is None:
            return None

        if isinstance(pricing, TieredModelPricing):
            rates: ModelPricing | PricingTier = pricing.tier_for_context(
                context_tokens if context_tokens is not None else 0
            )
        else:
            rates = pricing

        return (
            rates.input_price_per_token * input_tokens
            + rates.effective_cached_input_price * cached_input_tokens
            + rates.effective_cache_creation_price * cache_creation_tokens
            + rates.output_price_per_token * output_tokens
        )


def _billing_class(values: dict, model_id: str) -> BillingClass:
    """Parse and validate a model's declared marginal-cost classification."""
    raw = values.get("billing_class", "metered")
    if not isinstance(raw, str) or raw not in _BILLING_CLASSES:
        allowed = ", ".join(sorted(_BILLING_CLASSES))
        raise PricingError(
            f"Model '{model_id}': billing_class must be one of {allowed}; got {raw!r}"
        )
    return cast(BillingClass, raw)


def _parse_tiered_model(model_id: str, values: dict) -> TieredModelPricing:
    """Parse a tiered pricing entry from TOML data."""
    tiers_data = values["tiers"]
    if not isinstance(tiers_data, list) or len(tiers_data) == 0:
        raise PricingError(f"Model '{model_id}': 'tiers' must be a non-empty array of tables")

    parsed: list[PricingTier] = []
    for i, td in enumerate(tiers_data):
        if not isinstance(td, dict):
            raise PricingError(f"Tier {i} for model '{model_id}' must be a table")
        try:
            parsed.append(
                PricingTier(
                    context_threshold=int(td["context_threshold"]),
                    input_price_per_token=float(td["input_price_per_token"]),
                    output_price_per_token=float(td["output_price_per_token"]),
                    cached_input_price_per_token=_optional_price(
                        td, "cached_input_price_per_token"
                    ),
                    cache_creation_price_per_token=_optional_price(
                        td, "cache_creation_price_per_token"
                    ),
                )
            )
        except KeyError as exc:
            raise PricingError(
                f"Missing required field {exc} in tier {i} for model '{model_id}'"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise PricingError(f"Invalid value in tier {i} for model '{model_id}': {exc}") from exc

    parsed.sort(key=lambda t: t.context_threshold)
    return TieredModelPricing(
        tiers=tuple(parsed),
        billing_class=_billing_class(values, model_id),
    )


def _optional_price(values: dict, key: str) -> float | None:
    """Return ``float(values[key])`` or ``None`` when the key is absent."""
    raw = values.get(key)
    return None if raw is None else float(raw)


def _parse_flat_model(model_id: str, values: dict) -> ModelPricing:
    """Parse a flat (non-tiered) pricing entry from TOML data."""
    try:
        return ModelPricing(
            input_price_per_token=float(values["input_price_per_token"]),
            output_price_per_token=float(values["output_price_per_token"]),
            cached_input_price_per_token=_optional_price(values, "cached_input_price_per_token"),
            cache_creation_price_per_token=_optional_price(
                values, "cache_creation_price_per_token"
            ),
            billing_class=_billing_class(values, model_id),
        )
    except KeyError as exc:
        raise PricingError(f"Missing required field {exc} for model '{model_id}'") from exc
    except (TypeError, ValueError) as exc:
        raise PricingError(f"Invalid price value for model '{model_id}': {exc}") from exc


def load_pricing(path: Path | None = None) -> PricingConfig:
    """Load pricing from a TOML file.

    Parameters
    ----------
    path:
        Path to the ``pricing.toml`` file.  Falls back to the repo-root
        default when ``None``.

    Returns
    -------
    PricingConfig

    Raises
    ------
    PricingError
        If the file is missing, unreadable, or contains invalid data.
    """
    if path is None:
        path = _DEFAULT_PATH

    if not path.exists():
        raise PricingError(f"Pricing file not found: {path}")

    raw_bytes = path.read_bytes()
    try:
        data = tomllib.loads(raw_bytes.decode())
    except tomllib.TOMLDecodeError as exc:
        raise PricingError(f"Invalid TOML in {path}: {exc}") from exc

    models_section = data.get("models")
    if not isinstance(models_section, dict):
        raise PricingError("Missing or invalid [models] section in pricing config")

    models: dict[str, ModelPricing | TieredModelPricing] = {}
    for model_id, values in models_section.items():
        if not isinstance(values, dict):
            raise PricingError(
                f"Expected table for model '{model_id}', got {type(values).__name__}"
            )

        if "tiers" in values:
            models[model_id] = _parse_tiered_model(model_id, values)
        else:
            models[model_id] = _parse_flat_model(model_id, values)

    return PricingConfig(models)


_warned_models: set[str] = set()


def estimate_session_cost(
    config: PricingConfig,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cached_input_tokens: int = 0,
    cache_creation_tokens: int = 0,
    context_tokens: int | None = None,
) -> float | None:
    """Estimate cost for a session, preserving absent pricing as ``None``.

    A declared zero-rate model is a real numeric ``0.0``. A missing model
    entry is not a free model and must remain ``None`` so callers can expose
    unpriced usage instead of fabricating a calm zero-dollar reading.
    """
    cost = config.estimate_cost(
        model_id,
        input_tokens,
        output_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_creation_tokens=cache_creation_tokens,
        context_tokens=context_tokens,
    )
    if cost is None:
        if model_id and model_id not in _warned_models:
            _warned_models.add(model_id)
            logger.warning(
                "No pricing entry for model %r — cost is unpriced. "
                "Add an explicit pricing.toml entry or billing class.",
                model_id,
            )
        return None
    return cost
