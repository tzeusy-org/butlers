"""Education butler — pedagogy: technique selection, citations, reading pathways.

Three things the teaching phase needs and the rest of the tool surface does not
provide:

- **Technique selection.** A concept's ``metadata.concept_type`` (written by the
  curriculum planner, see ``concept_types.py``) says which evidence-based
  technique fits it.  :func:`select_technique` maps type → technique and falls
  back to Socratic questioning when the type is unset, which is the common case:
  the classifier abstains rather than guessing.  Every technique record carries
  the *principle* behind it, because the owner is entitled to ask "why are you
  teaching it this way?" and get a real answer rather than a rationalisation
  invented on the spot.

- **Citations.** :func:`teaching_cite_source` appends a ``source_refs`` entry to
  a node's metadata.  ``provenance`` is a required keyword with no default: the
  session knows whether it read the source or recalled it, and the display
  honours what is stored.  "referenced" is earned only by naming a source that
  is registered *and* saying so explicitly; recall against a registered source
  stays recall.  See REQ-education-source-grounding-002.

- **Reading pathways.** :func:`teaching_reading_pathways` turns the refs on a
  node into optional "for deeper study, see…" suggestions, resolving each source
  through the registry so a removed source is never presented as readable.
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Mapping
from typing import Any

import asyncpg

from butlers.tools.education.concept_types import CONCEPT_TYPES
from butlers.tools.education.source_material import PROVENANCE_VALUES, source_material_list

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Technique repertoire
# ---------------------------------------------------------------------------

DEFAULT_TECHNIQUE_ID = "socratic"

# One technique per concept type, plus the default.  ``principle`` is the
# sentence the session cites when asked why — it names the finding, not just the
# preference, so the answer survives a follow-up question.  ``moves`` are the
# ordered beats of the technique; the skill follows them in the explaining
# phase.
_TECHNIQUES: dict[str, dict[str, Any]] = {
    "retrieval-practice": {
        "id": "retrieval-practice",
        "label": "retrieval practice",
        "principle": (
            "the testing effect: pulling a fact out of memory strengthens it far more than "
            "reading it again, so I ask before I tell"
        ),
        "moves": [
            "ask for the fact before supplying it, even if they are likely to miss it",
            "confirm or correct in one line — no lecture on a miss",
            "come back for a second retrieval later in the session",
        ],
    },
    "worked-example": {
        "id": "worked-example",
        "label": "worked example, then guided practice",
        "principle": (
            "cognitive load theory: studying a complete worked example before practising frees "
            "working memory to build the procedure, which is why novices learn procedures faster "
            "from examples than from being handed a problem"
        ),
        "moves": [
            "walk through one complete worked example, narrating each decision",
            "hand over a near-identical problem with the hardest step already done",
            "fade the scaffolding on the next problem and let them drive",
        ],
    },
    "socratic-analogy": {
        "id": "socratic-analogy",
        "label": "Socratic questioning with an analogy",
        "principle": (
            "elaborative interrogation: a concept sticks when the learner supplies the 'why', and "
            "an analogy gives them something concrete to reason from"
        ),
        "moves": [
            "ask what they already believe about the concept",
            "offer one concrete analogy and ask where it breaks down",
            "ask them to state the principle in their own words",
        ],
    },
    "divergent-then-critique": {
        "id": "divergent-then-critique",
        "label": "divergent prompts, then critique",
        "principle": (
            "generating and judging at the same time collapses the option space, so divergent "
            "production comes first and evaluation second"
        ),
        "moves": [
            "ask for several genuinely different attempts before judging any of them",
            "have them pick one and say what made it the pick",
            "critique only the chosen attempt, against criteria they named",
        ],
    },
    DEFAULT_TECHNIQUE_ID: {
        "id": DEFAULT_TECHNIQUE_ID,
        "label": "Socratic questioning",
        "principle": (
            "with no concept type to go on, questioning first is the safe default: it reveals what "
            "they already know before I spend words on what they do not need"
        ),
        "moves": [
            "ask what they already know about the concept",
            "calibrate the explanation to the gap their answer reveals",
            "check understanding with one question before moving on",
        ],
    },
}

_TECHNIQUE_BY_CONCEPT_TYPE: dict[str, str] = {
    "factual": "retrieval-practice",
    "procedural": "worked-example",
    "conceptual": "socratic-analogy",
    "creative": "divergent-then-critique",
}


def select_technique(concept_type: str | None) -> dict[str, Any]:
    """Return the technique record for *concept_type*.

    An unset or unrecognized type yields the Socratic default rather than a
    guess: the classifier abstains when markers conflict, and a technique chosen
    from a word we do not understand would silently change how a concept is
    taught.

    Returns
    -------
    dict
        A JSON-storable copy with ``id``, ``label``, ``concept_type`` (the type
        that selected it, or ``None`` for the default), ``principle``, and
        ``moves``.
    """
    if concept_type in CONCEPT_TYPES:
        technique_id = _TECHNIQUE_BY_CONCEPT_TYPE[concept_type]
        selected_for: str | None = concept_type
    else:
        if concept_type is not None:
            logger.warning(
                "unrecognized concept_type %r — falling back to %s",
                concept_type,
                DEFAULT_TECHNIQUE_ID,
            )
        technique_id = DEFAULT_TECHNIQUE_ID
        selected_for = None

    record = copy.deepcopy(_TECHNIQUES[technique_id])
    record["concept_type"] = selected_for
    return record


def technique_for_node(node: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the technique a node's ``metadata.concept_type`` calls for."""
    metadata = (node or {}).get("metadata") or {}
    concept_type = metadata.get("concept_type") if isinstance(metadata, Mapping) else None
    return select_technique(concept_type if isinstance(concept_type, str) else None)


def is_technique(value: Any) -> bool:
    """Return True when *value* looks like a technique record this module wrote."""
    return isinstance(value, Mapping) and value.get("id") in _TECHNIQUES


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required and must be a non-empty string")
    return value.strip()


async def _load_node(pool: asyncpg.Pool, node_id: str) -> dict[str, Any]:
    """Return ``{"label", "metadata"}`` for *node_id*, raising if it is gone."""
    row = await pool.fetchrow(
        "SELECT label, metadata FROM education.mind_map_nodes WHERE id = $1",
        node_id,
    )
    if row is None:
        raise ValueError(
            f"Node not found: {node_id}. "
            "Use mind_map_node_list(mind_map_id=...) to list nodes in a mind map."
        )
    metadata = row["metadata"]
    if isinstance(metadata, str):
        # Defensive: a pool without the JSONB codec hands back the raw text.
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, ValueError):
            metadata = {}
    return {"label": row["label"], "metadata": metadata if isinstance(metadata, dict) else {}}


def _stored_refs(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs = metadata.get("source_refs")
    return [ref for ref in refs if isinstance(ref, dict)] if isinstance(refs, list) else []


async def teaching_cite_source(
    pool: asyncpg.Pool,
    node_id: str,
    *,
    location: str,
    provenance: str,
    source_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Record where a claim in a teaching explanation came from.

    Parameters
    ----------
    location:
        Where in the source the claim lives (chapter, page range, section).
        Free text, but must be specific enough to act on — omit the citation
        rather than inventing a location.
    provenance:
        ``"referenced"`` when this session read the registered source itself,
        ``"model-recalled"`` when the location comes from the model's knowledge
        of the source.  Required, with no default: the display shows the owner
        exactly what is stored here, so guessing on their behalf would overstate
        what the butler did.
    source_id:
        A registered source ID, or ``None`` for a work the owner has not
        registered.  ``"referenced"`` requires a registered ID; a recalled
        citation of an unregistered work uses ``None``.

    Raises
    ------
    ValueError
        If the node does not exist, the provenance is unknown, the location is
        blank, ``"referenced"`` is claimed without a registered source, or the
        named source is not in the registry.
    """
    location = _nonempty(location, "location")
    if provenance not in PROVENANCE_VALUES:
        allowed = ", ".join(sorted(PROVENANCE_VALUES))
        raise ValueError(
            f"provenance must be one of: {allowed}; got {provenance!r}. Use 'referenced' only if "
            "this session read the source itself."
        )
    if source_id is not None:
        source_id = _nonempty(source_id, "source_id")
    if provenance == "referenced" and source_id is None:
        raise ValueError(
            "provenance='referenced' requires a registered source_id — a citation with no "
            "registered source cannot have been read. Register the source with "
            "source_material_register(), or cite it with provenance='model-recalled'."
        )

    if source_id is not None:
        registry = {source["source_id"]: source for source in await source_material_list(pool)}
        if source_id not in registry:
            raise ValueError(
                f"source_id {source_id!r} is not registered. Use source_material_list() to find "
                "registered IDs, or cite with source_id=None and provenance='model-recalled'."
            )

    node = await _load_node(pool, node_id)
    ref: dict[str, Any] = {
        "source_id": source_id,
        "location": location,
        "provenance": provenance,
    }
    if note is not None and note.strip():
        ref["note"] = note.strip()

    merged = _stored_refs(node["metadata"])
    created = True
    for index, existing in enumerate(merged):
        if (existing.get("source_id"), existing.get("location")) == (source_id, location):
            # Same claim, cited again. The current writer knows what it just did,
            # so its provenance replaces the stored one — this is how the
            # planner's model-recalled guess becomes a citation once a session
            # actually reads the page.
            if "note" not in ref and existing.get("note"):
                ref["note"] = existing["note"]
            merged[index] = ref
            created = False
            break
    else:
        merged.append(ref)

    await pool.execute(
        """
        UPDATE education.mind_map_nodes
        SET metadata = metadata || $2::jsonb,
            updated_at = now()
        WHERE id = $1
        """,
        node_id,
        json.dumps({"source_refs": merged}),
    )

    return {
        "node_id": node_id,
        "node_label": node["label"],
        "source_ref": ref,
        "created": created,
        "source_refs": merged,
    }


# ---------------------------------------------------------------------------
# Reading pathways
# ---------------------------------------------------------------------------


async def teaching_reading_pathways(pool: asyncpg.Pool, node_id: str) -> dict[str, Any]:
    """Return optional further-reading suggestions for a concept node.

    A pathway is a location in a source the owner can actually open, so only
    refs whose ``source_id`` still resolves in the registry become one.  Refs
    naming no source, or a source that has since been removed, are counted and
    dropped — suggesting a book by ID the owner cannot look up is noise.

    Each pathway carries its ``provenance`` unchanged so the session can phrase
    a recalled location honestly ("I believe it is around chapter 3 — worth
    checking") instead of asserting it.

    Returns
    -------
    dict
        ``node_id``, ``node_label``, ``pathways``, and the two skip counts
        ``skipped_unregistered`` / ``recalled_without_source``.
    """
    node = await _load_node(pool, node_id)
    refs = _stored_refs(node["metadata"])

    pathways: list[dict[str, Any]] = []
    skipped_unregistered = 0
    recalled_without_source = 0

    if refs:
        registry = {source["source_id"]: source for source in await source_material_list(pool)}
        for ref in refs:
            location = ref.get("location")
            if not isinstance(location, str) or not location.strip():
                continue
            source_id = ref.get("source_id")
            if not isinstance(source_id, str) or not source_id.strip():
                recalled_without_source += 1
                continue
            source = registry.get(source_id)
            if source is None:
                skipped_unregistered += 1
                logger.info(
                    "reading pathway skipped for node %s: source %s is no longer registered",
                    node_id,
                    source_id,
                )
                continue

            provenance = ref.get("provenance")
            pathway = {
                "source_id": source_id,
                "title": source.get("title"),
                "authors": source.get("authors", []),
                "type": source.get("type"),
                "url": source.get("url"),
                "location": location.strip(),
                "provenance": (provenance if provenance in PROVENANCE_VALUES else "model-recalled"),
            }
            note = ref.get("note")
            if isinstance(note, str) and note.strip():
                pathway["note"] = note.strip()
            pathways.append(pathway)

    return {
        "node_id": node_id,
        "node_label": node["label"],
        "pathways": pathways,
        "skipped_unregistered": skipped_unregistered,
        "recalled_without_source": recalled_without_source,
    }
