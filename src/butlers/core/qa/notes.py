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


class Verification(_NotesModel):
    """Whether the dispatcher checked these notes against its recorded evidence bundle."""

    state: Literal["verified", "unverified"] = "unverified"
    #: Digest of the recorded bundle the notes were checked against (``verified`` only).
    bundle_digest: str | None = None


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
    #: Set only by the dispatcher; whatever the agent wrote here is discarded.
    verification: Verification = Verification()


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


#: A quote shorter than this proves nothing (a single character is contained in any item), so it
#: only counts when it is the item's whole text.
MIN_QUOTE_CHARS = 16


def reset_verification(notes: InvestigationNotes) -> InvestigationNotes:
    """Discard every agent-supplied verification claim: nothing is verified yet."""
    return notes.model_copy(
        update={
            "evidence_lines": [
                line.model_copy(update={"unsupported": False}) for line in notes.evidence_lines
            ],
            "claims": {
                claim_id: claim.model_copy(update={"unsupported": False})
                for claim_id, claim in notes.claims.items()
            },
            "verification": Verification(),
        }
    )


def _quote_supported(msg: str, item_text: str | None) -> bool:
    if not msg or item_text is None:
        return False
    if msg == item_text:
        return True
    return len(msg) >= MIN_QUOTE_CHARS and msg in item_text


def verify_evidence_against_bundle(
    notes: InvestigationNotes, bodies: Mapping[str, object]
) -> InvestigationNotes:
    """Mark evidence lines and claims the dispatcher's own bundle record does not support.

    ``bodies`` is the dispatcher-held anonymized bundle (never the agent-visible copy). A line
    is supported only when its ``ref`` names a bundle item and its ``msg``, after whitespace
    normalisation, is the item's whole text or a span of at least ``MIN_QUOTE_CHARS``
    characters of it, so an invented, edited or trivially short quote cannot pass. A claim is
    unsupported when it cites no supported line. Only ``msg`` is checked: ``ts``, ``lvl`` and
    ``butler`` are agent-reported because bundle items carry no such metadata. Agent-supplied
    flags and verification state are discarded.
    """
    texts = _bundle_texts(bodies)
    lines = []
    supported: set[str] = set()
    for line in notes.evidence_lines:
        ok = line.ref is not None and _quote_supported(_normalized(line.msg), texts.get(line.ref))
        if ok:
            supported.add(line.id)
        lines.append(line.model_copy(update={"unsupported": not ok}))
    claims = {
        claim_id: claim.model_copy(
            update={"unsupported": not any(e in supported for e in claim.evidence_ids)}
        )
        for claim_id, claim in notes.claims.items()
    }
    digest = bodies.get("digest")
    return notes.model_copy(
        update={
            "evidence_lines": lines,
            "claims": claims,
            "verification": Verification(
                state="verified", bundle_digest=digest if isinstance(digest, str) else None
            ),
        }
    )


def carry_forward_verification(
    notes: InvestigationNotes, stored: Mapping[str, object] | None
) -> InvestigationNotes:
    """Keep an earlier verification when the bundle bodies are no longer available.

    A re-persist after retention pruning has nothing to check against; it must not turn flags
    the dispatcher already stored back into clean ones. Only a stored ``verified`` result is
    carried, matched by line and claim id.
    """
    notes = reset_verification(notes)
    verification = stored.get("verification") if isinstance(stored, Mapping) else None
    if not isinstance(verification, Mapping) or verification.get("state") != "verified":
        return notes
    flagged_lines = {
        str(line["id"]): bool(line.get("unsupported"))
        for line in stored.get("evidence_lines") or []
        if isinstance(line, Mapping) and "id" in line
    }
    flagged_claims = {
        str(cid): bool(claim.get("unsupported"))
        for cid, claim in (stored.get("claims") or {}).items()
        if isinstance(claim, Mapping)
    }
    lines = [
        # An id the earlier pass never saw cannot have been verified.
        line.model_copy(update={"unsupported": flagged_lines.get(line.id, True)})
        for line in notes.evidence_lines
    ]
    claims = {
        cid: claim.model_copy(update={"unsupported": flagged_claims.get(cid, True)})
        for cid, claim in notes.claims.items()
    }
    digest = verification.get("bundle_digest")
    return notes.model_copy(
        update={
            "evidence_lines": lines,
            "claims": claims,
            "verification": Verification(
                state="verified", bundle_digest=digest if isinstance(digest, str) else None
            ),
        }
    )


def can_be_patch_ready(notes: InvestigationNotes) -> bool:
    """A case needs at least one supported claim: no claims is no evidenced diagnosis."""
    return any(not claim.unsupported for claim in notes.claims.values())


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
    "verification": Verification(),
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
