"""Per-model token pricing configuration.

Loads ``pricing.toml`` and exposes helpers for cost estimation.  The file
maps model IDs to their input/output per-token prices in USD so the
dashboard can display approximate session costs.

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
    """Per-token prices (USD) for a single model."""

    input_price_per_token: float
    output_price_per_token: float


class PricingConfig:
    """Loaded pricing configuration backed by a ``pricing.toml`` file.

    Parameters
    ----------
    models:
        Mapping of model ID to :class:`ModelPricing`.
    """

    def __init__(self, models: dict[str, ModelPricing]) -> None:
        self._models = models

    # -- public API ---------------------------------------------------------

    @property
    def model_ids(self) -> list[str]:
        """Return a sorted list of all known model IDs."""
        return sorted(self._models)

    def get_model_pricing(self, model_id: str) -> ModelPricing | None:
        """Return pricing for *model_id*, or ``None`` if unknown."""
        return self._models.get(model_id)

    def estimate_cost(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float | None:
        """Estimate the USD cost of a request.

        Returns ``None`` when the model is not in the pricing table.
        """
        pricing = self._models.get(model_id)
        if pricing is None:
            return None
        return (
            pricing.input_price_per_token * input_tokens
            + pricing.output_price_per_token * output_tokens
        )


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

    models: dict[str, ModelPricing] = {}
    for model_id, values in models_section.items():
        if not isinstance(values, dict):
            raise PricingError(
                f"Expected table for model '{model_id}', got {type(values).__name__}"
            )

        try:
            models[model_id] = ModelPricing(
                input_price_per_token=float(values["input_price_per_token"]),
                output_price_per_token=float(values["output_price_per_token"]),
            )
        except KeyError as exc:
            raise PricingError(f"Missing required field {exc} for model '{model_id}'") from exc
        except (TypeError, ValueError) as exc:
            raise PricingError(f"Invalid price value for model '{model_id}': {exc}") from exc

    return PricingConfig(models)


_warned_models: set[str] = set()


def estimate_session_cost(
    config: PricingConfig,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Estimate cost for a session, returning 0.0 for unknown models."""
    cost = config.estimate_cost(model_id, input_tokens, output_tokens)
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
