"""Alert rules of the public MCP surface, run through promtool (REQ-public-mcp-authorization-014).

The chart's ``PrometheusRule`` is rendered with ``helm template`` and its ``spec`` is fed to
``promtool test rules`` with synthetic series, one case per condition that must drive a flag to 0,
plus the healthy case and the two pending-owner-step scenarios. promtool is the pinned, checksum
verified binary from ``make install-lint-tools`` (``scripts/lint-tool-versions.json``). Without it
(or helm) the cases skip locally; CI's guards job sets ``BUTLERS_REQUIRE_PROMTOOL=1`` so a missing
tool fails there instead of passing silently.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import pytest
import yaml

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_CHART = _ROOT / "deploy" / "helm" / "butlers-public-mcp"
_REQUIRE_ENV = "BUTLERS_REQUIRE_PROMTOOL"

_SRV = 'job="butlers-public-mcp", namespace="butlers-public-dev", instance="10.1.0.9:9090"'
_PG = 'namespace="postgres-dev", pod="postgres-dev-1"'
_CRITICAL = "butlers_public_mcp:critical_ok"
_WARNING = "butlers_public_mcp:warning_ok"


def _load_lint_tools() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lint_tools", _ROOT / "scripts/lint_tools.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tool_missing(reason: str) -> None:
    if os.environ.get(_REQUIRE_ENV):
        pytest.fail(f"{_REQUIRE_ENV} is set but {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="module")
def promtool() -> Path:
    lint_tools = _load_lint_tools()
    try:
        return lint_tools.installed_binary("promtool")
    except lint_tools.ToolError as exc:
        _tool_missing(f"pinned promtool not installed: {exc}")
    raise AssertionError("unreachable")


def _render(*settings: str) -> list[dict]:
    if shutil.which("helm") is None:
        _tool_missing("helm not installed")
    command = ["helm", "template", "butlers-public-mcp", str(_CHART), "-n", "butlers-dev"]
    for values in ("values.yaml", "values.dev.yaml"):
        command += ["-f", str(_CHART / values)]
    for setting in (
        "image.tag=t",
        "audience=https://mcp.example/mcp",
        "allowedSubs=owner",
        *settings,
    ):
        command += ["--set", setting]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


@pytest.fixture(scope="module")
def rules_spec() -> dict:
    docs = [d for d in _render() if d["kind"] == "PrometheusRule"]
    assert len(docs) == 1
    return docs[0]["spec"]


@dataclass
class Case:
    series: list[tuple[str, str]]  # (selector, promtool values)
    flags: list[tuple[str, str, int]]  # (eval_time, flag, expected value)
    alerts: list[dict] = field(default_factory=list)  # promtool alert_rule_test entries
    missing: tuple[str, ...] = ()  # source-health series to leave out: server, slot, importer


# Every case runs with all three metric sources present, so the source-health alerts stay quiet
# unless a case removes a source. Distinct names or labels keep them out of the other rules: the
# slot is physical (the slot rules read logical ones) and the importer gauge is switch_enabled.
_SOURCES = {
    "server": (f"up{{{_SRV}}}", "1+0x3100"),
    "slot": (
        f'cnpg_pg_replication_slots_active{{{_PG}, slot_name="base", slot_type="physical"}}',
        "1+0x3100",
    ),
    "importer": ("butlers_public_mcp_proposal_import_switch_enabled", "1+0x3100"),
}


def _run(promtool: Path, spec: dict, case: Case, tmp_path: Path) -> subprocess.CompletedProcess:
    (tmp_path / "rules.yaml").write_text(yaml.safe_dump(spec))
    annotations = {r["alert"]: r["annotations"] for r in spec["groups"][0]["rules"] if "alert" in r}
    alert_tests = [
        {
            **entry,
            "exp_alerts": [
                {**alert, "exp_annotations": annotations[entry["alertname"]]}
                for alert in entry["exp_alerts"]
            ],
        }
        for entry in case.alerts
    ]
    test = {
        "rule_files": ["rules.yaml"],
        "evaluation_interval": "1m",
        "tests": [
            {
                "interval": "1m",
                "input_series": [
                    {"series": s, "values": v}
                    for s, v in [
                        *(src for name, src in _SOURCES.items() if name not in case.missing),
                        *case.series,
                    ]
                ],
                "promql_expr_test": [
                    {
                        "expr": flag,
                        "eval_time": at,
                        "exp_samples": [{"labels": flag, "value": value}],
                    }
                    for at, flag, value in case.flags
                ],
                **({"alert_rule_test": alert_tests} if alert_tests else {}),
            }
        ],
    }
    (tmp_path / "test.yaml").write_text(yaml.safe_dump(test))
    return subprocess.run(
        [str(promtool), "test", "rules", "test.yaml"], cwd=tmp_path, capture_output=True, text=True
    )


def _both(at: str, critical: int, warning: int) -> list[tuple[str, str, int]]:
    return [(at, _CRITICAL, critical), (at, _WARNING, warning)]


def _pending(change: str, values: str) -> tuple[str, str]:
    return (f'butlers_public_mcp_owner_step_pending{{{_SRV}, change="{change}"}}', values)


def _slot_series(metric: str, slot: str, slot_type: str, values: str) -> tuple[str, str]:
    labels = f'{_PG}, slot_name="{slot}", slot_type="{slot_type}", database="butlers"'
    return (f"cnpg_pg_replication_slots_{metric}{{{labels}}}", values)


CASES: dict[str, Case] = {
    # Source health: a missing source must show on a flag instead of silencing its rules.
    "server scrape absent": Case(
        series=[], missing=("server",), flags=[("4m", _CRITICAL, 1), ("6m", _CRITICAL, 0)]
    ),
    "server scrape down": Case(
        series=[(f"up{{{_SRV}}}", "0+0x30")],
        missing=("server",),
        flags=[("4m", _CRITICAL, 1), ("6m", _CRITICAL, 0)],
    ),
    "slot series absent": Case(
        series=[],
        missing=("slot",),
        flags=[("9m", _WARNING, 1), ("11m", _WARNING, 0), ("11m", _CRITICAL, 1)],
    ),
    "importer series absent": Case(
        series=[],
        missing=("importer",),
        flags=[("29m", _WARNING, 1), ("31m", _WARNING, 0), ("31m", _CRITICAL, 1)],
    ),
    # Healthy: traffic, an allowed slot and an unrelated critical cluster alert, which the
    # `surface` matcher must keep out of the flags. Both flags are present and 1.
    "healthy": Case(
        series=[
            (f'butlers_public_mcp_requests_total{{{_SRV}, tool="search", outcome="ok"}}', "0+3x60"),
            _slot_series("active", "butlers_public_sub", "logical", "1+0x60"),
            _slot_series("pg_wal_lsn_diff", "butlers_public_sub", "logical", "1000+0x60"),
            _pending("narrowing", "0+0x60"),
            _pending("widening", "0+0x60"),
            ('ALERTS{alertname="NodeDown", alertstate="firing", severity="critical"}', "1+0x60"),
        ],
        flags=_both("30m", 1, 1),
    ),
    "canary hit alone, on the counter's first sample": Case(
        series=[(f"butlers_public_mcp_canary_hits_total{{{_SRV}}}", "_x9 1+0x60")],
        flags=[
            ("8m", _CRITICAL, 1),
            ("11m", _CRITICAL, 0),
            ("40m", _CRITICAL, 1),
            ("11m", _WARNING, 1),
        ],
    ),
    "canary latch holds": Case(
        series=[(f"butlers_public_mcp_canary_latched{{{_SRV}}}", "1+0x30")],
        flags=_both("5m", 0, 1),
    ),
    "slot retains over half the WAL cap": Case(
        series=[
            _slot_series("pg_wal_lsn_diff", "butlers_public_sub", "logical", "6000000000+0x30")
        ],
        flags=_both("5m", 0, 1),
    ),
    "slot retains under half the WAL cap": Case(
        series=[
            _slot_series("pg_wal_lsn_diff", "butlers_public_sub", "logical", "5000000000+0x30")
        ],
        flags=_both("5m", 1, 1),
    ),
    "foreign logical slot": Case(
        series=[_slot_series("active", "analytics_sub", "logical", "1+0x30")],
        flags=_both("5m", 0, 1),
        alerts=[
            {
                "eval_time": "5m",
                "alertname": "PublicMcpForeignLogicalSlot",
                "exp_alerts": [
                    {
                        "exp_labels": {
                            "severity": "critical",
                            "surface": "butlers-public-mcp",
                            "slot_name": "analytics_sub",
                            "database": "butlers",
                        }
                    }
                ],
            }
        ],
    ),
    "allowed slots never trigger the foreign-slot rule": Case(
        series=[
            _slot_series("active", "butlers_public_sub", "logical", "1+0x30"),
            _slot_series("active", "pg_16401_sync_16500_7312345678901234567", "logical", "1+0x30"),
            _slot_series("active", "_cnpg_postgres_dev_2", "physical", "1+0x30"),
        ],
        flags=_both("5m", 1, 1),
    ),
    "narrowing pending past 24 hours": Case(
        series=[_pending("narrowing", "1+0x1600")],
        flags=[
            *_both("23h", 1, 0),  # pending, but only a warning so far
            *_both("24h1m", 0, 0),
        ],
    ),
    "widening pending for 48 hours stays a warning": Case(
        series=[_pending("widening", "1+0x3000")],
        flags=[*_both("1m", 1, 0), *_both("48h", 1, 0)],
    ),
    "assertion failure burst": Case(
        series=[
            (
                f'butlers_public_mcp_auth_failures_total{{{_SRV}, cause="expired"}}',
                "0 0 11 11 11 11",
            ),
        ],
        flags=_both("4m", 1, 0),
        alerts=[
            {
                "eval_time": "4m",
                "alertname": "PublicMcpAssertionFailures",
                "exp_alerts": [
                    {"exp_labels": {"severity": "warning", "surface": "butlers-public-mcp"}}
                ],
            }
        ],
    ),
    "assertion failures under the threshold": Case(
        series=[(f'butlers_public_mcp_auth_failures_total{{{_SRV}, cause="expired"}}', "100+0x30")],
        flags=_both("10m", 1, 1),
    ),
    "bearer refusal burst": Case(
        series=[(f"butlers_public_mcp_bearer_refusals_total{{{_SRV}}}", "0 0 25 25 25 25")],
        flags=_both("4m", 1, 0),
    ),
    "redactions over the hourly threshold": Case(
        series=[
            (
                f'butlers_public_mcp_redactions_total{{{_SRV}, tool="search", rule="phone"}}',
                "0 25 25 25",
            )
        ],
        flags=_both("3m", 1, 0),
    ),
    "dlp drop on the counter's first sample": Case(
        series=[
            (
                f'butlers_public_mcp_dlp_blocked_total{{{_SRV}, tool="finance_records"}}',
                "_x4 1+0x80",
            )
        ],
        flags=[
            ("3m", _WARNING, 1),
            ("6m", _WARNING, 0),
            ("40m", _WARNING, 1),
            ("6m", _CRITICAL, 1),
        ],
    ),
    "bulk finance read": Case(
        series=[
            (
                f'butlers_public_mcp_rows_served_total{{{_SRV}, tool="finance_records", '
                'domain="finance", client="claude.ai"}',
                "0 0 2001 2001 2001",
            )
        ],
        flags=_both("4m", 1, 0),
        alerts=[
            {
                "eval_time": "4m",
                "alertname": "PublicMcpBulkRead",
                "exp_alerts": [
                    {
                        "exp_labels": {
                            "severity": "warning",
                            "surface": "butlers-public-mcp",
                            "domain": "finance",
                            "client": "claude.ai",
                        }
                    }
                ],
            }
        ],
    ),
    "a large read of another domain is not a bulk read": Case(
        series=[
            (
                f'butlers_public_mcp_rows_served_total{{{_SRV}, tool="entity_profile", '
                'domain="relationship", client="claude.ai"}',
                "0 0 5000 5000 5000",
            )
        ],
        flags=_both("4m", 1, 1),
    ),
    "slot inactive past 10 minutes": Case(
        series=[_slot_series("active", "butlers_public_sub", "logical", "0+0x60")],
        flags=[("9m", _WARNING, 1), ("11m", _WARNING, 0), ("11m", _CRITICAL, 1)],
    ),
    "inbox undrained above 500": Case(
        series=[("butlers_public_mcp_proposal_import_inbox_depth", "501+0x30")],
        flags=_both("5m", 1, 0),
    ),
    "inbox at the threshold": Case(
        series=[("butlers_public_mcp_proposal_import_inbox_depth", "500+0x30")],
        flags=_both("5m", 1, 1),
    ),
    "proposal reaching apply_failed": Case(
        series=[
            (
                'butlers_public_mcp_proposal_import_outcomes_total{state="apply_failed", job="butlers"}',
                "_x4 1+0x80",
            )
        ],
        flags=[
            ("3m", _WARNING, 1),
            ("6m", _WARNING, 0),
            ("40m", _WARNING, 1),
            ("6m", _CRITICAL, 1),
        ],
    ),
    "other proposal outcomes are quiet": Case(
        series=[
            (
                'butlers_public_mcp_proposal_import_outcomes_total{state="applied", job="butlers"}',
                "_x4 3+0x20",
            )
        ],
        flags=_both("6m", 1, 1),
    ),
}


def test_rules_pass_promtool_check(promtool: Path, rules_spec: dict, tmp_path: Path) -> None:
    (tmp_path / "rules.yaml").write_text(yaml.safe_dump(rules_spec))
    result = subprocess.run(
        [str(promtool), "check", "rules", "rules.yaml"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("name", list(CASES))
def test_condition_drives_its_flag(
    promtool: Path, rules_spec: dict, tmp_path: Path, name: str
) -> None:
    result = _run(promtool, rules_spec, CASES[name], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_wrong_expectation_fails_so_the_cases_are_not_vacuous(
    promtool: Path, rules_spec: dict, tmp_path: Path
) -> None:
    case = Case(series=CASES["canary latch holds"].series, flags=_both("5m", 1, 1))
    assert _run(promtool, rules_spec, case, tmp_path).returncode != 0


def test_every_alert_carries_a_severity_and_the_surface_label(rules_spec: dict) -> None:
    (group,) = rules_spec["groups"]
    alerts = [r for r in group["rules"] if "alert" in r]
    assert {r["labels"]["severity"] for r in alerts} == {"critical", "warning"}
    assert all(r["labels"]["surface"] == "butlers-public-mcp" for r in alerts)
    assert [r["record"] for r in group["rules"] if "record" in r] == [_CRITICAL, _WARNING]
    # The flags are evaluated after every alert in the group, so they see this cycle's ALERTS.
    assert group["rules"][-2:] == [r for r in group["rules"] if "record" in r]


def test_thresholds_are_values(rules_spec: dict) -> None:
    custom = _render(
        "monitoring.rules.thresholds.inboxUndrained=7",
        "monitoring.rules.thresholds.narrowingPendingFor=36h",
        "monitoring.rules.thresholds.slotRetainedWalBytes=1000",
    )
    (rule,) = [d for d in custom if d["kind"] == "PrometheusRule"]
    rules = {r["alert"]: r for r in rule["spec"]["groups"][0]["rules"] if "alert" in r}
    assert rules["PublicMcpInboxUndrained"]["expr"].strip().endswith("> 7")
    assert rules["PublicMcpNarrowingOwnerStepPending"]["for"] == "36h"
    assert rules["PublicMcpSlotRetainedWal"]["expr"].strip().endswith("> 1000")
