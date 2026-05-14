"""Investigation notes schema and tolerant parser for QA self-healing output."""

from __future__ import annotations

import json
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


class EvidenceLine(_NotesModel):
    id: str
    ts: str
    lvl: str
    butler: str
    msg: str


class CounterEvidenceItem(_NotesModel):
    hypothesis: str
    verdict: Literal["rejected", "accepted", "pending"]
    reason: str


class DiffLine(_NotesModel):
    kind: Literal["meta", "+", "-", " "]
    text: str


class InvestigationNotes(_NotesModel):
    schema_version: Literal[1]
    headline: str
    hypothesis: str
    blurb_segments: list[str | BlurbSegment]
    claims: dict[str, Claim]
    evidence_lines: list[EvidenceLine]
    counter_evidence: list[CounterEvidenceItem]
    why_this_fix: str
    diff_snapshot: list[DiffLine]


ParseStatus = Literal["ok", "partial", "failed"]

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
