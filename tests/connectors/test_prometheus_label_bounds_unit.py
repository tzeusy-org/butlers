"""Prometheus connector label bounds: no PII and no sender-chosen values (bu-p88e3l).

RFC 0005 keeps the "bounded, non-identifying label values" rule as a design rule for
prometheus_client instruments. These tests pin the two clamps (``endpoint_identity`` and
``media_type``) and the already-closed ``error_detail`` vocabulary.
"""

from __future__ import annotations

import re

import pytest
from prometheus_client import REGISTRY

from butlers.connectors.gmail_policy import (
    LabelFilterPolicy,
    PolicyTierAssigner,
    evaluate_message_policy,
)
from butlers.connectors.metrics import (
    ConnectorMetrics,
    prometheus_endpoint_identity,
    prometheus_media_type,
)
from butlers.tools.switchboard.routing.transport import (
    SAFE_DELIVERY_EVIDENCE,
    TransportErrorClass,
    TransportErrorDetail,
    TransportOutcome,
    TransportResult,
)

pytestmark = pytest.mark.unit

_EMAIL = "someone.private@example.invalid"
_IDENTITY_SHAPE = re.compile(r"[a-z][a-z0-9_-]*:[0-9a-f]{8}")


def _label_values(metric: str, label: str) -> set[str]:
    """Collect every value the registry holds for ``label`` on sample family ``metric``."""
    values: set[str] = set()
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == metric and label in sample.labels:
                values.add(sample.labels[label])
    return values


class TestEndpointIdentityClamp:
    @pytest.mark.parametrize(
        "raw",
        [
            f"gmail:user:{_EMAIL}",
            f"google_drive:user:{_EMAIL}",
            "whatsapp:+15555550100",
            "telegram:user:@private_handle",
            "steam:user:76561190000000000",
        ],
    )
    def test_raw_account_never_survives(self, raw: str) -> None:
        clamped = prometheus_endpoint_identity(raw)
        assert _IDENTITY_SHAPE.fullmatch(clamped), clamped
        account = raw.split(":", 2)[-1]
        assert account not in clamped
        assert "@" not in clamped

    def test_stable_and_distinct_per_account(self) -> None:
        a = prometheus_endpoint_identity(f"gmail:user:{_EMAIL}")
        assert a == prometheus_endpoint_identity(f"gmail:user:{_EMAIL}")
        assert a != prometheus_endpoint_identity("gmail:user:other@example.invalid")

    def test_empty_and_odd_family_fall_back(self) -> None:
        assert prometheus_endpoint_identity("") == "unknown"
        assert prometheus_endpoint_identity(None) == "unknown"
        odd = prometheus_endpoint_identity(f"{_EMAIL}:x")
        assert odd.startswith("unknown:")
        assert "@" not in odd

    def test_connector_metrics_exports_only_the_clamped_value(self) -> None:
        raw = f"gmail:user:{_EMAIL}"
        ConnectorMetrics("gmail", raw).record_ingest_submission("success", 0.01)
        seen = _label_values("connector_ingest_submissions_total", "endpoint_identity")
        assert prometheus_endpoint_identity(raw) in seen
        assert raw not in seen
        assert not any("@" in value for value in seen)

    def test_gmail_policy_counters_export_only_the_clamped_value(self) -> None:
        raw = f"gmail:user:{_EMAIL}"
        message = {
            "id": "m1",
            "threadId": "t1",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "sender@example.invalid"},
                    {"name": "To", "value": _EMAIL},
                ]
            },
        }
        evaluate_message_policy(
            message,
            label_filter=LabelFilterPolicy.default(),
            tier_assigner=PolicyTierAssigner(user_email=_EMAIL),
            endpoint_identity=raw,
        )
        for metric in (
            "butlers_connector_gmail_label_filter_total",
            "butlers_connector_gmail_tier_assigned_total",
            "butlers_connector_gmail_priority_tier_assigned_total",
        ):
            seen = _label_values(metric, "endpoint_identity")
            assert prometheus_endpoint_identity(raw) in seen, metric
            assert raw not in seen, metric
            assert not any("@" in value for value in seen), metric

    def test_gmail_label_filter_excluded_path_exports_only_the_clamped_value(self) -> None:
        raw = f"gmail:user:{_EMAIL}"
        message = {
            "id": "m2",
            "threadId": "t2",
            "labelIds": ["SPAM"],
            "payload": {"headers": [{"name": "From", "value": "sender@example.invalid"}]},
        }
        result = evaluate_message_policy(
            message,
            label_filter=LabelFilterPolicy.default(),
            tier_assigner=PolicyTierAssigner(user_email=_EMAIL),
            endpoint_identity=raw,
        )
        assert result.should_ingest is False
        seen = _label_values("butlers_connector_gmail_label_filter_total", "endpoint_identity")
        assert prometheus_endpoint_identity(raw) in seen
        assert raw not in seen
        assert not any("@" in value for value in seen)


class TestMediaTypeClamp:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("text/calendar", "text/calendar"),
            ("IMAGE/JPEG", "image/jpeg"),
            ("text/plain; charset=utf-8", "text/plain"),
            (" application/pdf ", "application/pdf"),
            ("application/x-sender-invented-2026", "application/other"),
            ("image/x-weird", "image/other"),
            ("not-a-mime", "other"),
            ("x-evil/payload", "other"),
            ("", "other"),
            (None, "other"),
        ],
    )
    def test_clamp(self, raw: str | None, expected: str) -> None:
        assert prometheus_media_type(raw) == expected

    def test_arbitrary_sender_strings_stay_in_a_closed_set(self) -> None:
        values = {prometheus_media_type(f"application/x-{n}") for n in range(500)}
        assert values == {"application/other"}

    def test_connector_metrics_exports_only_clamped_values(self) -> None:
        metrics = ConnectorMetrics("gmail", f"gmail:user:{_EMAIL}")
        invented = "application/x-invented-by-the-sender"
        metrics.record_attachment_fetched(invented, "eager", "success")
        metrics.record_attachment_fetched(invented, "lazy", "success")
        metrics.record_attachment_skipped_oversized(invented)
        metrics.record_attachment_type_distribution(invented)
        for metric in (
            "connector_attachment_fetched_eager_total",
            "connector_attachment_fetched_lazy_total",
            "connector_attachment_skipped_oversized_total",
            "connector_attachment_type_distribution_total",
        ):
            seen = _label_values(metric, "media_type")
            assert "application/other" in seen, metric
            assert invented not in seen, metric


class TestRuntimeAttentionErrorDetailIsClosed:
    """``error_detail`` on switchboard_runtime_attention_delivery_total comes from a StrEnum."""

    def test_vocabulary_is_the_six_fixed_codes(self) -> None:
        assert {d.value for d in TransportErrorDetail} == {
            "recipient_unavailable",
            "policy_denied",
            "provider_rejected",
            "transport_timeout",
            "transport_connection_lost",
            "worker_recovery",
        }

    def test_every_result_detail_is_in_the_vocabulary(self) -> None:
        for error_class, detail in SAFE_DELIVERY_EVIDENCE:
            outcome = {
                TransportErrorClass.PRE_TRANSPORT: TransportOutcome.NOT_ATTEMPTED,
                TransportErrorClass.TRANSPORT_REJECTED: TransportOutcome.REJECTED,
                TransportErrorClass.TRANSPORT_UNCERTAIN: TransportOutcome.UNCERTAIN,
            }[error_class]
            result = TransportResult(outcome, error_class, detail)
            assert str(result.error_detail) in {d.value for d in TransportErrorDetail}

    def test_free_form_detail_cannot_be_constructed(self) -> None:
        with pytest.raises(ValueError):
            TransportResult(
                TransportOutcome.REJECTED,
                TransportErrorClass.TRANSPORT_REJECTED,
                "provider said: user@example.invalid",  # type: ignore[arg-type]
            )
