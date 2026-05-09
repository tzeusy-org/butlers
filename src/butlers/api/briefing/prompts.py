"""Pinned LLM prompt for the dashboard briefing elaboration.

Model: claude-haiku-4-5
Parameters: max_tokens=120, temperature=0.4, timeout=4.0s

The prompt encodes the dashboard voice rules from
about/heart-and-soul/design-language.md:
    - Past tense for events, present tense for state.
    - No future tense.
    - No first person (I, we, us, our).
    - Avoid "your" when "the" works.
    - No hedging adverbs (currently, presently, just, simply, basically).
    - No exclamation marks.
    - No em-dashes.

Design reference: openspec/changes/dashboard-overview-briefing/design.md D2.
"""

from __future__ import annotations

import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model configuration (pinned per spec D2)
# ---------------------------------------------------------------------------

ELABORATION_MODEL = "claude-haiku-4-5"
ELABORATION_MAX_TOKENS = 120
ELABORATION_TEMPERATURE = 0.4
ELABORATION_TIMEOUT_S = 4.0

# Anthropic API endpoint
_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_API_VERSION = "2023-06-01"

# ---------------------------------------------------------------------------
# Pinned system prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You write a one-to-three sentence elaboration paragraph for a personal multi-agent \
dashboard. The paragraph names what is true about the system right now, drawing only \
from the state JSON provided in the user message.

Voice rules (all mandatory):
- Past tense for completed events. Present tense for current state. No future tense. \
Do not write "will be" or "is going to."
- No first person. Write "the system," "the butler," "the queue." Never "I," "we," \
"us," or "our."
- Avoid "your" when "the" works. Write "the calendar" not "your calendar."
- No hedging adverbs: do not write "currently," "presently," "just," "simply," \
or "basically."
- No exclamation marks.
- No em-dashes.
- Maximum 50 words. Three sentences at most.
- Write only the paragraph. No preamble, no sign-off, no markdown formatting.
"""


def _build_user_message(state: dict, state_class: str) -> str:
    """Render the user turn from dashboard state and the computed class."""
    attention_items = state.get("attention_items", [])
    butler_statuses = state.get("butler_statuses", [])

    state_summary = {
        "state_class": state_class,
        "attention_items": attention_items,
        "butler_statuses": butler_statuses,
    }
    return (
        f"Dashboard state:\n{json.dumps(state_summary, default=str, indent=2)}\n\n"
        f"Write the elaboration paragraph for state_class={state_class!r}."
    )


async def elaborate_llm(state: dict, state_class: str) -> str | None:
    """Call claude-haiku-4-5 and return the elaboration paragraph or None.

    Returns:
        The model response string if the call succeeded within the timeout
        and the response is non-empty.
        None on any failure (timeout, HTTP error, empty response, API error).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY is not set; LLM elaboration unavailable")
        return None

    payload = {
        "model": ELABORATION_MODEL,
        "max_tokens": ELABORATION_MAX_TOKENS,
        "temperature": ELABORATION_TEMPERATURE,
        "system": _SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": _build_user_message(state, state_class)},
        ],
    }

    headers = {
        "x-api-key": api_key,
        "anthropic-version": _ANTHROPIC_API_VERSION,
        "content-type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=ELABORATION_TIMEOUT_S) as client:
            response = await client.post(_ANTHROPIC_API_URL, json=payload, headers=headers)
        response.raise_for_status()
    except httpx.TimeoutException:
        logger.info("LLM elaboration timed out after %.1fs", ELABORATION_TIMEOUT_S)
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning("LLM elaboration HTTP error: %s", exc.response.status_code)
        return None
    except Exception as exc:
        logger.warning("LLM elaboration failed: %s", exc)
        return None

    try:
        body = response.json()
        content_blocks = body.get("content", [])
        if not content_blocks:
            logger.info("LLM elaboration returned empty content blocks")
            return None
        text = content_blocks[0].get("text", "").strip()
        if not text:
            logger.info("LLM elaboration returned empty text")
            return None
        return text
    except Exception as exc:
        logger.warning("LLM elaboration response parse error: %s", exc)
        return None
