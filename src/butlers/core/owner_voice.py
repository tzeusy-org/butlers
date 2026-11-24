"""D19 presentation only; identity and authority remain in the original receipts."""

from __future__ import annotations

import os

ONE_VOICE_ENV = "BUTLERS_D19_ONE_VOICE"


def one_voice_enabled() -> bool:
    """Dark by default; only the exact deployment opt-in enables presentation."""
    return os.environ.get(ONE_VOICE_ENV) == "true"


def notification_text(
    text: str, origin: str, *, owner_voice: bool = False, subject: bool = False
) -> str:
    """Render the known source prefix, without inventing or changing provenance.

    ``owner_voice`` is supplied only after server-side owner-channel resolution.
    Remove only an exact leading source marker already present in the stored
    text; arbitrary quoted markers, other authors and body content stay intact.
    The legacy branch preserves the existing subject/body prefix rules exactly.
    """
    prefix = f"[{origin}]"
    if owner_voice:
        stripped = text.lstrip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].lstrip()
        return text
    if subject:
        return text if prefix.lower() in text.lower() else f"{prefix} {text}"
    return text if text.lstrip().startswith(prefix) else f"{prefix} {text}"
