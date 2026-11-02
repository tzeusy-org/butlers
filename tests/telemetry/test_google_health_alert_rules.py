"""Static contracts for the Google Health connector's provisioned alert rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from butlers.connectors import google_health

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RULE_PATH = _REPO_ROOT / "observability/grafana-alerting/connector-google-health.json"
_PROMETHEUS_PATH = _REPO_ROOT / "observability/prometheus/prometheus.yml"


def _rules_by_uid() -> dict[str, dict]:
    provisioned = json.loads(_RULE_PATH.read_text(encoding="utf-8"))
    assert "contactPoints" not in provisioned
    assert "policies" not in provisioned
    (group,) = provisioned["groups"]
    return {rule["uid"]: rule for rule in group["rules"]}


def _query(rule: dict) -> str:
    return next(data for data in rule["data"] if data["refId"] == "A")["model"]["expr"]


def _evaluator(rule: dict) -> dict:
    threshold = next(data for data in rule["data"] if data["refId"] == "C")["model"]
    assert threshold["type"] == "threshold"
    assert threshold["expression"] == "B"
    return threshold["conditions"][0]["evaluator"]


@pytest.mark.parametrize(
    ("uid", "metric", "evaluator", "hold"),
    [
        (
            "butlers-google-health-resource-failing",
            google_health.google_health_resource_consecutive_failures,
            {
                "params": [google_health._RESOURCE_DEGRADED_FAILURE_THRESHOLD - 1],
                "type": "gt",
            },
            "1h",
        ),
        (
            "butlers-google-health-window-page-cap",
            google_health.google_health_window_page_cap_exceeded_total,
            {"params": [0], "type": "gt"},
            "0s",
        ),
        (
            "butlers-google-health-quarantine",
            google_health.google_health_quarantined_envelopes_total,
            {"params": [0], "type": "gt"},
            "0s",
        ),
    ],
)
def test_google_health_rule_alerts_on_its_connector_metric(
    uid: str, metric, evaluator: dict, hold: str
) -> None:
    """Each paused rule watches a metric the connector really exports, per resource."""
    rule = _rules_by_uid()[uid]
    query = _query(rule)
    # prometheus_client exposes a Counter as "<name>_total"; a Gauge as its name.
    exported = metric._name + ("_total" if metric._type == "counter" else "")

    assert exported in query
    assert "sum by(endpoint_identity, resource)" in query or (
        "max by(endpoint_identity, resource)" in query
    )
    if metric._type == "counter":
        assert "increase(" in query, "counter resets must not read as new events"
    assert _evaluator(rule) == evaluator
    assert rule["for"] == hold
    assert rule["isPaused"] is True
    assert rule["noDataState"] == "OK"
    assert rule["execErrState"] == "Error"
    assert rule["labels"]["severity"] == "warning"
    assert rule["annotations"]["summary"]
    assert "no alert instances" in rule["annotations"]["disabled"]


def test_google_health_rules_cover_exactly_the_three_alertable_signals() -> None:
    assert set(_rules_by_uid()) == {
        "butlers-google-health-resource-failing",
        "butlers-google-health-window-page-cap",
        "butlers-google-health-quarantine",
    }


def test_local_prometheus_scrapes_the_google_health_connector() -> None:
    """Without a scrape job the rules would evaluate against no series at all.

    That the target reaches the connector's metrics (through its proxy sidecar)
    is pinned for every connector job in test_compose_connector_metrics_scrape.
    """
    scrape = yaml.safe_load(_PROMETHEUS_PATH.read_text(encoding="utf-8"))
    job = next(
        job for job in scrape["scrape_configs"] if job["job_name"] == "connector-google-health"
    )

    ((target,),) = [static["targets"] for static in job["static_configs"]]
    assert target.rsplit(":", 1)[0] == "connector-google-health"
    assert job["static_configs"][0]["labels"] == {"connector": "google_health"}
