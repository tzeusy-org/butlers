"""Spawner — LLM provider routing seam.

Responsible for:
 - deriving the LLM provider name from a model string (_derive_llm_provider)
 - building the OpenCode-compatible provider config for a given model (resolve_provider_config)

Extracted from butlers.core.spawner as part of bu-y9dwz (structural
decomposition into internal seams, follow-on to bu-dl98i.7.1 / PRs #2483/#2486).
The Spawner continues to use these via re-exports so existing import paths and
test patches that reference ``butlers.core.spawner.<name>`` remain valid.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _derive_llm_provider(model: str | None) -> str:
    """Derive the LLM provider name from a model string.

    The model string may be prefixed with a provider name separated by a
    forward slash (e.g. ``"ollama/llama3"`` → ``"ollama"``).  If no prefix
    is present the default runtime is the Anthropic API, so ``"anthropic"``
    is returned.
    """
    return model.split("/", 1)[0] if model and "/" in model else "anthropic"


async def resolve_provider_config(
    pool: Any | None,
    model_id: str | None,
) -> dict[str, dict[str, Any]] | None:
    """Build an OpenCode-compatible provider config for the given model.

    When *model_id* starts with ``ollama/``, queries
    ``public.provider_config`` for the Ollama provider's configured base
    URL and returns a config dict that OpenCode can consume, including the
    ``npm`` adapter package, ``/v1``-suffixed base URL, and explicit model
    registration.  See https://docs.ollama.com/integrations/opencode

    Returns ``None`` when no provider is configured, the model doesn't
    use a provider prefix, or no DB pool is available.
    """
    if pool is None or not model_id or "/" not in model_id:
        return None

    provider_type = model_id.split("/", 1)[0]
    if provider_type != "ollama":
        return None

    try:
        row = await pool.fetchrow(
            "SELECT config FROM public.provider_config WHERE provider_type = $1 AND enabled = true",
            provider_type,
        )
    except Exception:
        logger.debug("Failed to query provider_config for %s", provider_type, exc_info=True)
        return None

    if row is None:
        return None

    raw = row["config"]
    config = json.loads(raw) if isinstance(raw, str) else (raw or {})
    base_url = config.get("base_url", "")
    if not base_url:
        return None

    base_url = base_url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"

    ollama_model = model_id.split("/", 1)[1]
    return {
        provider_type: {
            "npm": "@ai-sdk/openai-compatible",
            "options": {"baseURL": base_url},
            "models": {ollama_model: {"name": ollama_model}},
        }
    }
