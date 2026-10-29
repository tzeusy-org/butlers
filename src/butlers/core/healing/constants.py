"""Shared historical attempt and current QA tracking constants."""

CIRCUIT_BREAKER_FAILURE_STATUSES = frozenset({"failed", "timeout", "anonymization_failed"})
UNFIXABLE_SENTINEL_FILENAME = "UNFIXABLE"
