"""Static contracts for permanent domain-event delivery observability."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD_PATH = _REPO_ROOT / "observability/grafana/butlers-switchboard.json"
_RULE_PATH = (
    _REPO_ROOT / "observability/grafana-alerting/domain-event-delivery-failed-permanent.json"
)
_DASHBOARD_ROOT = _REPO_ROOT / "observability/grafana"
_OBSERVABILITY_COMPOSE_PATH = _REPO_ROOT / "docker-compose.observability.yml"
_PROMETHEUS_METRIC = "butlers_domain_event_delivery_failed_permanent_deliveries_total"


def test_failed_permanent_delivery_panel_and_paused_warning_rule_are_reset_safe() -> None:
    """The paused rule preserves its diagnostic policies for explicit later enablement."""
    dashboard = json.loads(_DASHBOARD_PATH.read_text())
    panels = dashboard["panels"]
    panel = next(
        panel for panel in panels if panel.get("id") == 17 and panel.get("type") == "timeseries"
    )
    panel_expression = panel["targets"][0]["expr"]
    assert _PROMETHEUS_METRIC in panel_expression
    assert "increase(" in panel_expression
    assert "sum by(source_butler, destination_butler, reason)" in panel_expression
    assert panel["targets"][0]["legendFormat"] == (
        "{{source_butler}} to {{destination_butler}} ({{reason}})"
    )

    assert _RULE_PATH.is_file(), "the disabled Grafana warning-rule definition is required"
    provisioned = json.loads(_RULE_PATH.read_text())
    rule = provisioned["groups"][0]["rules"][0]
    query = next(data for data in rule["data"] if data["refId"] == "A")["model"]["expr"]
    threshold = next(data for data in rule["data"] if data["refId"] == "C")["model"]

    assert rule["isPaused"] is True
    assert rule["for"] == "5m"
    assert rule["noDataState"] == "NoData"
    assert rule["execErrState"] == "Error"
    assert "only after an owner explicitly enables evaluation" in rule["annotations"]["no_data"]
    assert "only after an owner explicitly enables evaluation" in rule["annotations"]["exec_error"]
    assert "no alert instances" in rule["annotations"]["disabled"]
    assert rule["labels"] == {"severity": "warning"}
    assert rule["dashboardUid"] == dashboard["uid"]
    assert rule["panelId"] == panel["id"]
    assert _PROMETHEUS_METRIC in query
    assert "increase(" in query
    assert "[15m]" in query
    assert threshold["type"] == "threshold"
    assert threshold["expression"] == "B"
    assert threshold["conditions"][0]["evaluator"] == {"params": [0], "type": "gt"}
    assert "source_butler" in rule["annotations"]["deduplication"]
    assert "destination_butler" in rule["annotations"]["deduplication"]
    assert "reason" in rule["annotations"]["deduplication"]
    assert "reset" in rule["annotations"]
    assert "rather than treating it as healthy" in rule["annotations"]["no_data"]
    assert "contactPoints" not in provisioned
    assert "policies" not in provisioned


def test_alert_rule_is_mounted_only_in_grafana_alerting_provisioning() -> None:
    """Dashboard discovery must never parse this alert-rule JSON as a dashboard."""
    compose = yaml.safe_load(_OBSERVABILITY_COMPOSE_PATH.read_text(encoding="utf-8"))
    grafana_volumes = compose["services"]["grafana"]["volumes"]
    alerting_mounts = [
        tuple(volume.split(":", 2))
        for volume in grafana_volumes
        if volume.split(":", 2)[0] == "./observability/grafana-alerting"
    ]

    assert _RULE_PATH.is_file()
    assert not _RULE_PATH.is_relative_to(_DASHBOARD_ROOT)
    assert alerting_mounts == [
        (
            "./observability/grafana-alerting",
            "/etc/grafana/provisioning/alerting",
            "ro",
        )
    ]
