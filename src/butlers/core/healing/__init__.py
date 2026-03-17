"""Core healing package for butler self-healing infrastructure.

Provides deterministic error fingerprinting, severity scoring, and
dual-input support (raw exception objects or structured string fields).
"""

from __future__ import annotations

from butlers.core.healing.fingerprint import (
    FingerprintResult,
    compute_fingerprint,
    compute_fingerprint_from_report,
)

__all__ = [
    "FingerprintResult",
    "compute_fingerprint",
    "compute_fingerprint_from_report",
]
