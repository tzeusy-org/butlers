"""Multi-butler signal extraction pipeline.

Performs a single-pass extraction step on incoming messages, detecting signals
relevant to ANY registered butler. Runs in parallel with primary routing so it
never adds latency to the user-facing response.

Flow:
1. Build a unified extraction prompt from registered ExtractorSchemas
2. Spawn a single runtime instance with the unified prompt
3. Parse structured JSON extractions (type, confidence, tool, target_butler)
4. Gate by confidence — only HIGH-confidence extractions auto-dispatch
5. Dispatch each group to the appropriate butler via route()
6. Log all extractions in routing_log for audit
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import asyncpg

from butlers.tools.switchboard import route

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------

# Confidence ordering: lower index = higher confidence
_CONFIDENCE_ORDER = ["HIGH", "MEDIUM", "LOW"]


class Confidence(StrEnum):
    """Extraction confidence levels."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


def _confidence_at_or_above(
    confidence: Confidence,
    threshold: Confidence,
) -> bool:
    """Return True if confidence is at or above the threshold."""
    return _CONFIDENCE_ORDER.index(confidence.value) <= _CONFIDENCE_ORDER.index(threshold.value)


# Float scores recorded on calendar proposals for each categorical confidence
# level. The proposals store keeps a 0.0-1.0 ``confidence``; extraction only
# yields categorical levels, so we map them onto representative scores.
_CONFIDENCE_SCORE: dict[Confidence, float] = {
    Confidence.HIGH: 0.9,
    Confidence.MEDIUM: 0.5,
    Confidence.LOW: 0.2,
}

# Tool the calendar-owning butler exposes to stage an inferred event for review.
CALENDAR_PROPOSE_TOOL = "calendar_propose_event"

# Calendar is a module enabled on several butlers, not a standalone butler.
# Inferred-event proposals from ingestion are routed to the general-purpose
# butler, which owns the user-facing shared calendar.
CALENDAR_PROPOSAL_BUTLER = "general"

# Inferred calendar events route to a human-review lane (the proposals lane).
# Apply a floor so only sufficiently confident signals become proposals, which
# keeps the lane low-noise. With the score map above only HIGH-confidence
# calendar signals clear this floor by default.
CALENDAR_PROPOSAL_CONFIDENCE_FLOOR = 0.7

# Max characters of the originating message retained as proposal provenance.
_SOURCE_SNIPPET_MAX_CHARS = 500


@dataclass(frozen=True)
class ExtractorSchema:
    """Defines what signals a butler cares about.

    Each butler registers one of these to declare the signal types
    it can process and the tool mappings for dispatching extractions.

    Attributes
    ----------
    butler_name:
        Name of the butler that owns these signal types.
    signal_types:
        List of signal categories this butler handles
        (e.g. ["contacts", "interactions", "life_events"]).
    tool_mappings:
        Maps signal type to the MCP tool name to call on the butler
        (e.g. {"contacts": "contact_create", "symptoms": "symptom_log"}).
    """

    butler_name: str
    signal_types: list[str]
    tool_mappings: dict[str, str]


@dataclass
class Extraction:
    """A single extracted signal from a message.

    Attributes
    ----------
    type:
        The signal type (e.g. "contacts", "symptoms").
    confidence:
        Extraction confidence level.
    tool_name:
        The MCP tool to invoke on the target butler.
    tool_args:
        Arguments to pass to the tool.
    target_butler:
        Which butler should receive this extraction.
    dispatched:
        Whether this extraction was auto-dispatched (set after gating).
    """

    type: str
    confidence: Confidence
    tool_name: str
    tool_args: dict[str, Any]
    target_butler: str
    dispatched: bool = False


# ------------------------------------------------------------------
# Default schemas for known butlers
# ------------------------------------------------------------------

RELATIONSHIP_SCHEMA = ExtractorSchema(
    butler_name="relationship",
    signal_types=[
        "contacts",
        "interactions",
        "life_events",
        "dates",
        "facts",
        "sentiments",
    ],
    tool_mappings={
        "contacts": "contact_create",
        "interactions": "interaction_log",
        "life_events": "note_create",
        "dates": "date_add",
        "facts": "fact_set",
        "sentiments": "note_create",
    },
)

HEALTH_SCHEMA = ExtractorSchema(
    butler_name="health",
    signal_types=["symptoms", "medications", "measurements"],
    tool_mappings={
        "symptoms": "symptom_log",
        "medications": "medication_add",
        "measurements": "measurement_log",
    },
)

CALENDAR_SCHEMA = ExtractorSchema(
    butler_name=CALENDAR_PROPOSAL_BUTLER,
    signal_types=["events"],
    tool_mappings={"events": CALENDAR_PROPOSE_TOOL},
)


# ------------------------------------------------------------------
# Prompt building
# ------------------------------------------------------------------


def build_extraction_prompt(
    message: str,
    schemas: list[ExtractorSchema],
) -> str:
    """Build a unified extraction prompt from registered schemas.

    The prompt instructs the runtime instance to analyze the message and
    return a JSON array of extractions covering all registered butler
    signal types.
    """
    schema_sections: list[str] = []
    for schema in schemas:
        tools_desc = ", ".join(
            f"{st} -> {schema.tool_mappings.get(st, 'unknown')}" for st in schema.signal_types
        )
        schema_sections.append(
            f"Butler '{schema.butler_name}':\n"
            f"  Signal types: {', '.join(schema.signal_types)}\n"
            f"  Tool mappings: {tools_desc}"
        )

    schemas_text = "\n\n".join(schema_sections)

    encoded_message = json.dumps({"message": message}, ensure_ascii=False)

    return (
        "Please use the /signal-extraction skill to analyze the message and "
        "extract all relevant signals for any registered butler.\n\n"
        "IMPORTANT: Return ONLY a JSON array of extraction objects.\n\n"
        f"Registered butler schemas:\n\n{schemas_text}\n\n"
        f"User input JSON:\n{encoded_message}\n"
    )


# ------------------------------------------------------------------
# Response parsing
# ------------------------------------------------------------------


def parse_extractions(
    raw: str,
    schemas: list[ExtractorSchema],
) -> list[Extraction]:
    """Parse CC response into Extraction objects.

    Validates that each extraction references a known butler and signal
    type. Silently drops invalid extractions.
    """
    # Build lookup: butler_name -> set of valid signal types
    valid_signals: dict[str, set[str]] = {}
    tool_lookups: dict[str, dict[str, str]] = {}
    for schema in schemas:
        valid_signals[schema.butler_name] = set(schema.signal_types)
        tool_lookups[schema.butler_name] = dict(schema.tool_mappings)

    # Extract JSON from response (handle markdown code blocks)
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(
            "Failed to parse extraction response as JSON: %s",
            text[:200],
        )
        return []

    if not isinstance(data, list):
        logger.warning("Extraction response is not a JSON array: %s", type(data))
        return []

    extractions: list[Extraction] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        try:
            target = item.get("target_butler", "")
            sig_type = item.get("type", "")
            confidence_raw = item.get("confidence", "LOW").upper()
            tool_name = item.get("tool_name", "")
            tool_args = item.get("tool_args", {})

            # Validate confidence
            try:
                confidence = Confidence(confidence_raw)
            except ValueError:
                confidence = Confidence.LOW

            # Validate target butler and signal type
            if target not in valid_signals:
                continue
            if sig_type not in valid_signals[target]:
                continue

            # Use tool mapping if tool_name not provided
            if not tool_name:
                tool_name = tool_lookups.get(target, {}).get(sig_type, "")
            if not tool_name:
                continue

            if not isinstance(tool_args, dict):
                tool_args = {}

            extractions.append(
                Extraction(
                    type=sig_type,
                    confidence=confidence,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    target_butler=target,
                )
            )
        except Exception:
            logger.warning(
                "Skipping invalid extraction item: %s",
                item,
                exc_info=True,
            )
            continue

    return extractions


# ------------------------------------------------------------------
# Dispatch and logging
# ------------------------------------------------------------------


def _is_calendar_proposal(ext: Extraction) -> bool:
    """True if this extraction stages a calendar event proposal."""
    return (
        ext.target_butler == CALENDAR_SCHEMA.butler_name and ext.tool_name == CALENDAR_PROPOSE_TOOL
    )


def _build_calendar_proposal_args(
    tool_args: dict[str, Any],
    *,
    confidence: float,
    source_event_id: str | None,
    source_snippet: str | None,
    entity_ids: list[str] | None,
) -> dict[str, Any]:
    """Enrich an LLM-extracted calendar proposal with code-authoritative provenance.

    The LLM proposes the event shape (title/start/end). The originating
    ingestion-event id, snippet, confidence score, and resolved entity ids come
    from the ingestion context — never the model — so they are injected here.
    """
    args = dict(tool_args)
    args["confidence"] = confidence
    if source_event_id is not None:
        args["source_event_id"] = source_event_id
    resolved_snippet = source_snippet if source_snippet is not None else args.get("source_snippet")
    if isinstance(resolved_snippet, str):
        args["source_snippet"] = resolved_snippet[:_SOURCE_SNIPPET_MAX_CHARS]
    if entity_ids:
        args["entity_ids"] = list(entity_ids)
    return args


async def _dispatch_extractions(
    pool: asyncpg.Pool,
    extractions: list[Extraction],
    threshold: Confidence,
    *,
    call_fn: Any | None = None,
    source_event_id: str | None = None,
    source_snippet: str | None = None,
    entity_ids: list[str] | None = None,
    calendar_confidence_floor: float = CALENDAR_PROPOSAL_CONFIDENCE_FLOOR,
) -> list[Extraction]:
    """Dispatch eligible extractions to target butlers.

    Non-calendar signals dispatch when at or above ``threshold``. Calendar
    proposals are gated by the dedicated float ``calendar_confidence_floor``
    (to keep the proposals lane low-noise) and are enriched with ingestion
    provenance before routing. Returns all extractions with ``dispatched``
    updated.
    """
    for ext in extractions:
        if _is_calendar_proposal(ext):
            score = _CONFIDENCE_SCORE.get(ext.confidence, 0.0)
            if score < calendar_confidence_floor:
                # Below the proposals-lane floor — leave undispatched.
                continue
            ext.tool_args = _build_calendar_proposal_args(
                ext.tool_args,
                confidence=score,
                source_event_id=source_event_id,
                source_snippet=source_snippet,
                entity_ids=entity_ids,
            )
        elif not _confidence_at_or_above(ext.confidence, threshold):
            continue

        try:
            await route(
                pool,
                target_butler=ext.target_butler,
                tool_name=ext.tool_name,
                args=ext.tool_args,
                source_butler="switchboard:extractor",
                call_fn=call_fn,
            )
            ext.dispatched = True
        except Exception:
            logger.warning(
                "Failed to dispatch extraction to %s: %s",
                ext.target_butler,
                ext.tool_name,
                exc_info=True,
            )

    return extractions


async def _log_extractions(
    pool: asyncpg.Pool,
    extractions: list[Extraction],
) -> None:
    """Log all extractions to routing_log for audit."""
    for ext in extractions:
        error_msg = None if ext.dispatched else f"Below threshold ({ext.confidence.value})"
        await pool.execute(
            """
            INSERT INTO routing_log
                (source_butler, target_butler, tool_name,
                 success, duration_ms, error)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            "switchboard:extractor",
            ext.target_butler,
            ext.tool_name,
            ext.dispatched,
            0,
            error_msg,
        )


# ------------------------------------------------------------------
# Main pipeline
# ------------------------------------------------------------------


async def extract_signals(
    pool: asyncpg.Pool,
    message: str,
    dispatch_fn: Any,
    extractor_schemas: list[ExtractorSchema] | None = None,
    *,
    confidence_threshold: Confidence = Confidence.HIGH,
    call_fn: Any | None = None,
    source_event_id: str | None = None,
    source_snippet: str | None = None,
    entity_ids: list[str] | None = None,
    calendar_confidence_floor: float = CALENDAR_PROPOSAL_CONFIDENCE_FLOOR,
) -> list[Extraction]:
    """Extract multi-butler signals from a message in a single CC pass.

    This is the main entry point for the extraction pipeline.

    Parameters
    ----------
    pool:
        Database connection pool.
    message:
        The incoming message to analyze.
    dispatch_fn:
        Callable to spawn a runtime instance. Signature:
        ``async (prompt: str, trigger_source: str) -> SpawnerResult``.
    extractor_schemas:
        List of ExtractorSchemas defining what signals each butler
        handles. Defaults to
        [RELATIONSHIP_SCHEMA, HEALTH_SCHEMA, CALENDAR_SCHEMA].
    confidence_threshold:
        Minimum confidence for auto-dispatch of non-calendar signals.
        Defaults to HIGH.
    call_fn:
        Optional callable for testing route dispatch.
        Passed through to route().
    source_event_id:
        ``public.ingestion_events.id`` of the originating signal. Injected
        into calendar proposals as the idempotency key / provenance link so
        re-ingesting the same signal does not create a duplicate proposal.
    source_snippet:
        Human-readable excerpt that triggered the inference (provenance).
        Defaults to the incoming ``message`` when not supplied.
    entity_ids:
        Resolved participant entity ids, injected into calendar proposals.
    calendar_confidence_floor:
        Minimum (float) confidence for a calendar signal to become a
        proposal. Limits proposals-lane noise.

    Returns
    -------
    list[Extraction]
        All extractions found, with ``dispatched=True`` for those that
        were auto-dispatched to their target butler.
    """
    if extractor_schemas is None:
        extractor_schemas = [RELATIONSHIP_SCHEMA, HEALTH_SCHEMA, CALENDAR_SCHEMA]

    if not extractor_schemas:
        return []

    # Build unified prompt
    prompt = build_extraction_prompt(message, extractor_schemas)

    # Spawn CC for extraction
    try:
        result = await dispatch_fn(prompt=prompt, trigger_source="extraction")
    except Exception:
        logger.exception("Extraction runtime invocation failed — returning empty")
        return []

    # Parse response
    raw_text = ""
    if result and hasattr(result, "result") and result.result:
        raw_text = result.result

    if not raw_text:
        return []

    extractions = parse_extractions(raw_text, extractor_schemas)

    if not extractions:
        return []

    # Provenance excerpt for calendar proposals defaults to the message itself.
    effective_snippet = source_snippet if source_snippet is not None else message

    # Dispatch eligible extractions (calendar proposals gated by their floor).
    await _dispatch_extractions(
        pool,
        extractions,
        confidence_threshold,
        call_fn=call_fn,
        source_event_id=source_event_id,
        source_snippet=effective_snippet,
        entity_ids=entity_ids,
        calendar_confidence_floor=calendar_confidence_floor,
    )

    # Log all extractions for audit
    await _log_extractions(pool, extractions)

    return extractions
