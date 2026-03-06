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

logger = logging.getLogger(__name__)

# Default location: <repo-root>/pricing.toml
_DEFAULT_PATH = Path(__file__).resolve().parents[3] / "pricing.toml"


class PricingError(Exception):
    """Raised when pricing configuration is missing or malformed."""


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Per-token prices (USD) for a single model (flat pricing)."""

    input_price_per_token: float
    output_price_per_token: float


@dataclass(frozen=True, slots=True)
class PricingTier:
    """Per-token prices (USD) for a single context-size tier."""

    context_threshold: int  # tier applies when total context >= this many tokens
    input_price_per_token: float
    output_price_per_token: float
    cached_input_price_per_token: float = 0.0


@dataclass(frozen=True, slots=True)
class TieredModelPricing:
    """Context-tiered pricing for a model with variable rates by context size."""

    tiers: tuple[PricingTier, ...]  # sorted ascending by context_threshold

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

    def estimate_cost(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        *,
        cached_input_tokens: int = 0,
        context_tokens: int | None = None,
    ) -> float | None:
        """Estimate the USD cost of a request.

        Parameters
        ----------
        cached_input_tokens:
            Tokens served from cache (charged at a lower rate for tiered
            models that define ``cached_input_price_per_token``).
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
            tier = pricing.tier_for_context(context_tokens if context_tokens is not None else 0)
            return (
                tier.input_price_per_token * input_tokens
                + tier.cached_input_price_per_token * cached_input_tokens
                + tier.output_price_per_token * output_tokens
            )

        return (
            pricing.input_price_per_token * input_tokens
            + pricing.output_price_per_token * output_tokens
        )


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
                    cached_input_price_per_token=float(td.get("cached_input_price_per_token", 0.0)),
                )
            )
        except KeyError as exc:
            raise PricingError(
                f"Missing required field {exc} in tier {i} for model '{model_id}'"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise PricingError(f"Invalid value in tier {i} for model '{model_id}': {exc}") from exc

    parsed.sort(key=lambda t: t.context_threshold)
    return TieredModelPricing(tiers=tuple(parsed))


def _parse_flat_model(model_id: str, values: dict) -> ModelPricing:
    """Parse a flat (non-tiered) pricing entry from TOML data."""
    try:
        return ModelPricing(
            input_price_per_token=float(values["input_price_per_token"]),
            output_price_per_token=float(values["output_price_per_token"]),
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
    context_tokens: int | None = None,
) -> float:
    """Estimate cost for a session, returning 0.0 for unknown models."""
    cost = config.estimate_cost(
        model_id,
        input_tokens,
        output_tokens,
        cached_input_tokens=cached_input_tokens,
        context_tokens=context_tokens,
    )
    if cost is None:
        if model_id and model_id not in _warned_models:
            _warned_models.add(model_id)
            logger.warning(
                "No pricing entry for model %r — cost will be reported as $0. "
                "Add it to pricing.toml to fix.",
                model_id,
            )
        return 0.0
    return cost
