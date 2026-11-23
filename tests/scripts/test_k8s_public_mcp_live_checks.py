"""Static contract for scripts/k8s/public-mcp-live-checks.sh (RFC 0039 task 2.5).

The live checks exec Python inside the butlers-app image. That image puts no venv on PATH and
need not ship a `python3` name, so every exec names the image interpreter, and the isolation
probe, which imports asyncpg, runs through the sealed entrypoint that selects the venv.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = (_ROOT / "scripts" / "k8s" / "public-mcp-live-checks.sh").read_text()
_IMAGE_PYTHON = "/usr/local/bin/python -I"
_VENV_PYTHON = f"{_IMAGE_PYTHON} /app/scripts/runtime_clis/python_entrypoint.py python"


def _exec_lines() -> list[str]:
    return [line for line in _SCRIPT.splitlines() if re.search(r"\bexec\b.*--\s", line)]


def test_no_exec_relies_on_a_python_name_on_path():
    lines = _exec_lines()
    assert lines, "the live checks exec into pods"
    for line in lines:
        assert not re.search(r"--\s+python3?\b", line), line


def test_stdlib_probes_use_the_image_interpreter():
    python_execs = [line for line in _exec_lines() if "python" in line]
    assert len(python_execs) >= 3  # canaries count, tcp reachability, isolation probe
    for line in python_execs:
        assert f"-- {_IMAGE_PYTHON}" in line, line


def test_the_isolation_probe_runs_in_the_venv():
    (line,) = [
        line
        for line in _SCRIPT.splitlines()
        if "public-mcp-isolation-probe.py" in line and "exec" in line
    ]
    assert f"-- {_VENV_PYTHON} -" in line
    probe = (_ROOT / "scripts" / "k8s" / "public-mcp-isolation-probe.py").read_text()
    assert "import asyncpg" in probe  # why the venv is needed


# --- the probe pods' egress out of `mcp` --------------------------------------------------
# Namespace `mcp` is default-deny both ways (homelab helm/traefik_mcp_local, only DNS out), so a
# probe pod there is rejected on egress before it reaches the public pod. On kube-router a
# rejection reads as "connection refused", exactly like a closed port, which hid this in the
# first dark smoke: the labelled probe failed and the unlabelled "refused" proved nothing.

_EGRESS = _ROOT / "scripts" / "k8s" / "public-mcp-probe-egress.yaml"
_SMOKE = (_ROOT / "scripts" / "k8s" / "public-mcp-smoke.sh").read_text()
_MARKER = "butlers.tze.how/public-mcp-probe"
_FILTER_PROXY = "app.kubernetes.io/instance=mcp-butlers"


def test_the_probe_egress_allow_is_narrow():
    (policy,) = [d for d in yaml.safe_load_all(_EGRESS.read_text()) if d]
    assert policy["kind"] == "NetworkPolicy"
    assert policy["metadata"] == {
        "name": "butlers-public-mcp-probe-egress",
        "namespace": "mcp",
        "labels": {"app.kubernetes.io/part-of": "butlers-public-mcp-checks"},
    }
    spec = policy["spec"]
    # Only marked probe pods: no gateway pod carries the marker, so nothing else gains egress.
    assert spec["podSelector"] == {"matchLabels": {_MARKER: "true"}}
    assert spec["policyTypes"] == ["Egress"]
    assert "ingress" not in spec
    assert spec["egress"] == [
        {
            "to": [
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "butlers-public-dev"}
                    },
                    "podSelector": {
                        "matchLabels": {"app.kubernetes.io/component": "butlers-public-mcp"}
                    },
                }
            ],
            "ports": [{"protocol": "TCP", "port": 8080}],
        }
    ]


def _position(text: str, needle: str) -> int:
    index = text.find(needle)
    assert index >= 0, needle
    return index


def test_live_checks_allow_probe_egress_before_the_probes_and_remove_it_on_exit():
    apply = _position(_SCRIPT, "apply -f")
    assert "public-mcp-probe-egress.yaml" in _SCRIPT
    assert apply < _position(_SCRIPT, "probe pubmcp-probe-labelled")
    cleanup = _SCRIPT[_position(_SCRIPT, "cleanup()") :].split("\n", 1)[0]
    assert "delete networkpolicy butlers-public-mcp-probe-egress" in cleanup


def test_both_live_check_probes_get_egress_and_only_one_is_the_filter_proxy():
    # The unlabelled probe must reach the policy decision on our side to prove the refusal.
    assert f'LABELLED="{_FILTER_PROXY},{_MARKER}=true"' in _SCRIPT
    assert f'PLAIN="{_MARKER}=true"' in _SCRIPT
    probes = [line for line in _SCRIPT.splitlines() if line.startswith("probe pubmcp-probe-")]
    assert sorted(probes) == [
        'probe pubmcp-probe-labelled "$LABELLED"',
        'probe pubmcp-probe-plain "$PLAIN"',
    ]
    # The Service-selector guard checks the labelled probe's full label set.
    assert 'PROBE_LABELS="$LABELLED"' in _SCRIPT
    assert 'instance=butlers"' not in _SCRIPT


def test_the_smoke_probe_is_the_filter_proxy_label_with_egress_after_the_live_checks():
    # The live checks delete the allow on exit, so the smoke re-applies it afterwards.
    assert f"--labels={_FILTER_PROXY},{_MARKER}=true" in _SMOKE
    live = _position(_SMOKE, 'public-mcp-live-checks.sh" "$ENV_NAME"')
    assert _position(_SMOKE[live:], "apply -f") > 0
    cleanup = _SMOKE[_position(_SMOKE, "cleanup() {") : _position(_SMOKE, "trap cleanup EXIT")]
    assert "delete networkpolicy butlers-public-mcp-probe-egress" in cleanup
    assert '{"app.kubernetes.io/instance": "butlers"}' not in _SMOKE


def test_psql_variables_are_never_used_inside_dash_c():
    # psql interpolates :'var' only in script input; under -c the window printed "?s".
    for line in _SMOKE.splitlines():
        if ":'t0'" in line:
            assert " -c " not in line, line
