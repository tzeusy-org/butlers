"""Investigation notes schema and tolerant parser for QA self-healing output."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError


class _NotesModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class BlurbSegment(_NotesModel):
    claim: str
    text: str


class Claim(_NotesModel):
    evidence_ids: list[str]
    note: str
    #: Set by the dispatcher when none of the cited lines is supported (never agent-trusted).
    unsupported: bool = False


class EvidenceLine(_NotesModel):
    id: str
    ts: str
    lvl: str
    butler: str
    msg: str
    #: Notes v2: the dispatcher-issued bundle item id this line quotes (``E1``...).
    ref: str | None = None
    #: Set by the dispatcher; an agent-supplied value is overwritten when a bundle exists.
    unsupported: bool = False


class CounterEvidenceItem(_NotesModel):
    hypothesis: str
    verdict: Literal["rejected", "accepted", "pending"]
    reason: str


class DiffLine(_NotesModel):
    kind: Literal["meta", "+", "-", " "]
    text: str


class InvestigationNotes(_NotesModel):
    schema_version: Literal[1, 2]
    headline: str
    hypothesis: str
    blurb_segments: list[str | BlurbSegment]
    claims: dict[str, Claim]
    evidence_lines: list[EvidenceLine]
    counter_evidence: list[CounterEvidenceItem]
    why_this_fix: str
    diff_snapshot: list[DiffLine]


ParseStatus = Literal["ok", "partial", "failed"]


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _bundle_texts(bodies: Mapping[str, object]) -> dict[str, str]:
    """Item id -> whitespace-normalised text from the dispatcher-held bundle bodies."""
    texts: dict[str, str] = {}
    sections = bodies.get("sections")
    for section in sections if isinstance(sections, list) else []:
        items = section.get("items") if isinstance(section, dict) else None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                texts[item["id"]] = _normalized(str(item.get("text") or ""))
    return texts


def verify_evidence_against_bundle(
    notes: InvestigationNotes, bodies: Mapping[str, object]
) -> InvestigationNotes:
    """Mark evidence lines and claims the dispatcher's own bundle record does not support.

    ``bodies`` is the dispatcher-held anonymized bundle (never the agent-visible copy). A line
    is supported only when its ``ref`` names a bundle item and its ``msg``, after whitespace
    normalisation, is non-empty and contained in that item's text, so an invented or edited
    line cannot pass. A claim is unsupported when it cites no supported line. Agent-supplied
    ``unsupported`` flags are ignored.
    """
    texts = _bundle_texts(bodies)
    lines = []
    supported: set[str] = set()
    for line in notes.evidence_lines:
        msg = _normalized(line.msg)
        ok = bool(msg) and line.ref is not None and msg in texts.get(line.ref, "")
        if ok:
            supported.add(line.id)
        lines.append(line.model_copy(update={"unsupported": not ok}))
    claims = {
        claim_id: claim.model_copy(
            update={"unsupported": not any(e in supported for e in claim.evidence_ids)}
        )
        for claim_id, claim in notes.claims.items()
    }
    return notes.model_copy(update={"evidence_lines": lines, "claims": claims})


def can_be_patch_ready(notes: InvestigationNotes) -> bool:
    """A case whose every claim is unsupported cannot be ``patch_ready``."""
    return not notes.claims or any(not claim.unsupported for claim in notes.claims.values())


_FIELD_ADAPTERS = {
    name: TypeAdapter(field.annotation) for name, field in InvestigationNotes.model_fields.items()
}
_MISSING_FIELD_DEFAULTS = {
    "schema_version": 1,
    "headline": "",
    "hypothesis": "",
    "blurb_segments": [],
    "claims": {},
    "evidence_lines": [],
    "counter_evidence": [],
    "why_this_fix": "",
    "diff_snapshot": [],
}


def parse_investigation_notes(raw: str) -> tuple[InvestigationNotes | None, ParseStatus]:
    """Parse agent-authored investigation notes without raising on malformed input."""

    try:
        return InvestigationNotes.model_validate_json(raw), "ok"
    except Exception:
        pass

    try:
        decoded = json.loads(raw)
    except Exception:
        return None, "failed"

    if not isinstance(decoded, dict):
        return None, "failed"

    partial_kwargs = {}
    recovered_any = False
    for field_name, adapter in _FIELD_ADAPTERS.items():
        if field_name in decoded:
            try:
                partial_kwargs[field_name] = adapter.validate_python(decoded[field_name])
            except ValidationError:
                pass
            except Exception:
                pass
            else:
                recovered_any = True
                continue

        if field_name in _MISSING_FIELD_DEFAULTS:
            default = _MISSING_FIELD_DEFAULTS[field_name]
            partial_kwargs[field_name] = (
                default.copy() if isinstance(default, (list, dict)) else default
            )

    if not recovered_any:
        return None, "failed"

    return InvestigationNotes.model_construct(**partial_kwargs), "partial"
