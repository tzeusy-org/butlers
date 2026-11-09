"""RFC0038 S7 / bu-dhloi1.15 AC4: source accounting never grants runtime egress."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "egress_coverage", ROOT / "scripts/check_egress_profile_coverage.py"
)
coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(coverage)
pytestmark = pytest.mark.unit


@pytest.fixture
def source(tmp_path):
    (tmp_path / "src/butlers").mkdir(parents=True)
    (tmp_path / "deploy").mkdir()
    (tmp_path / "roster").mkdir()
    (tmp_path / "src/butlers/client.py").write_text(
        'BASE = "https://api.provider.com/v1"\n'
        "def resolve():\n"
        '    return os.environ.get("CUSTOM_BASE_URL", BASE)\n'
        "client = factory(base_url=resolve())\n"
    )
    (tmp_path / coverage.PROFILES).write_text(
        'schema = "butlers-egress-profiles/v1"\n[workloads.alpha]\n'
        'external = ["api.provider.com:443"]\n'
        'sites = [{site = "database", port = 5432}]\n'
        'dynamic = [{source = "CUSTOM_BASE_URL", reason = "Only the declared origin may pass"},'
        '{source = "src/butlers/client.py:resolve", reason = "Named owning factory"}]\n'
    )
    inventory = {
        "schema": coverage.SCHEMA,
        "declarations": {},
        "bundles": {},
        "topology": {},
        "vendor_resolvers": {},
        "local_resolvers": {},
        "bundle_policy": json.loads((ROOT / coverage.INVENTORY).read_text())["bundle_policy"],
    }
    for key, observed in coverage.declarations(tmp_path).items():
        inventory["declarations"][key] = {
            "observed": observed,
            "class": "external" if observed["kind"] == "url" else "dynamic",
            "workloads": ["alpha"],
            "reason": "Owned test provider base; configured overrides grant no new route.",
            "sites": [],
            "resolver": None
            if observed["kind"] == "url"
            else {
                "path": "src/butlers/client.py",
                "symbol": "resolve",
                "source": observed["config"] or "src/butlers/client.py:resolve",
                "sha256": coverage.resolver_digest(tmp_path, "src/butlers/client.py", "resolve"),
            },
        }
    return tmp_path, inventory


def errors(source):
    return coverage.check(*source)[0]


def test_current_inventory_binds_discovery_and_reports_required_gaps():
    inventory = json.loads((ROOT / coverage.INVENTORY).read_text())
    failures, summary = coverage.check(ROOT, inventory)
    assert failures == []
    assert summary["declarations"] > 200
    gaps = summary["blocked_declarations"]
    assert any("owner_address_geocoding.py" in g["declaration"] for g in gaps)
    assert any("LIVE_LISTENER_TRANSCRIPTION_URL" in g["reason"] for g in gaps)
    assert summary["bundle_inputs"] > 0


def test_declared_static_and_dynamic_factory_are_healthy_without_network(source):
    assert errors(source) == []
    # A dynamic factory's existence is accounted, never interpreted as a new host grant.
    assert any(r["class"] == "dynamic" for r in source[1]["declarations"].values())


@pytest.mark.parametrize("new_file", [False, True])
def test_new_static_declaration_is_not_implicitly_covered(source, new_file):
    root, _ = source
    path = root / "src/butlers" / ("new_client.py" if new_file else "client.py")
    with path.open("a") as out:
        out.write('NEXT = "https://api.provider.com/new-feature"\n')
    assert any("unreviewed declaration" in e for e in errors(source))


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("api.provider.com", "undeclared.provider.com"),
        ("CUSTOM_BASE_URL", "NEW_BASE_URL"),
        ("base_url=resolve()", "base_url=other_provider()"),
    ],
)
def test_host_override_or_factory_expression_drift_requires_review(source, before, after):
    path = source[0] / "src/butlers/client.py"
    path.write_text(path.read_text().replace(before, after))
    assert any("unreviewed declaration" in e for e in errors(source))
    assert any("stale declaration" in e for e in errors(source))


def test_count_and_removed_declarations_cannot_go_quiet(source):
    path = source[0] / "src/butlers/client.py"
    original = path.read_text()
    path.write_text(
        original.replace('"CUSTOM_BASE_URL", BASE', '"CUSTOM_BASE_URL", "CUSTOM_BASE_URL"')
    )
    assert any("count/content drift" in e for e in errors(source))
    path.write_text("")
    assert any("stale declaration" in e for e in errors(source))
    assert any("zero declarations" in e for e in errors(source))


@pytest.mark.parametrize("condition", ["missing-host", "unknown-workload", "missing-site", "held"])
def test_reviewed_reachability_must_match_actual_profile(source, condition):
    root, inventory = source
    row = next(r for r in inventory["declarations"].values() if r["class"] == "external")
    if condition == "unknown-workload":
        row["workloads"] = ["unknown"]
    elif condition == "missing-site":
        row["class"] = "site"
        row["sites"] = ["unlisted-site"]
    else:
        path = root / coverage.PROFILES
        path.write_text(path.read_text().replace('external = ["api.provider.com:443"]', ""))
        if condition == "held":
            path.write_text(path.read_text() + 'held = "not admitted"\n')
    assert errors(source)


def test_blocked_required_caller_cannot_silently_gain_a_route(source):
    root, inventory = source
    row = next(r for r in inventory["declarations"].values() if r["class"] == "external")
    row["class"] = "blocked"
    row["reason"] = "Required compatibility is incomplete; caller is not admitted."
    profile = root / coverage.PROFILES
    original = profile.read_text()
    profile.write_text(original.replace('external = ["api.provider.com:443"]', "external = []"))
    assert errors(source) == []
    profile.write_text(original)
    assert any("blocked declaration was admitted" in e for e in errors(source))


def test_toml_template_and_vendor_changes_are_not_invisible(source):
    root, inventory = source
    config = root / "roster/alpha/butler.toml"
    config.parent.mkdir()
    config.write_text('url = "https://api.provider.com/v1"\n')
    assert any("unreviewed declaration" in e for e in errors(source))
    config.unlink()
    template = root / "deploy/new-role.yaml"
    template.write_text('command: ["curl", "https://api.provider.com/v1"]\n')
    assert any("unreviewed declaration" in e for e in errors(source))
    template.unlink()
    bundle = root / "deploy/runtime-clis/opencode/series.json"
    bundle.parent.mkdir(parents=True)
    bundle.write_text('{"commit":"changed"}\n')
    assert any("bundle drift" in e for e in errors(source))
    inventory["bundles"] = coverage.bundle_pins(root)
    assert "missing/unknown named vendor resolver family" in errors(source)


def test_topology_and_reference_classifications_are_bound(source):
    root, inventory = source
    path = root / "src/butlers/client.py"
    path.write_text(path.read_text() + '\n"""https://example.org/docstring-like-expression"""\n')
    assert any("unreviewed declaration" in e for e in errors(source))
    path.write_text(
        '"""https://example.org/actual-module-docstring"""\n' + path.read_text().split('\n"""')[0]
    )
    assert errors(source) == []
    config = root / "roster/alpha/butler.toml"
    config.parent.mkdir()
    config.write_text("[modules]\ncalendar = {}\n")
    assert any("topology drift" in e for e in errors(source))
    inventory["topology"] = coverage.topology_pins(root)
    assert errors(source) == []


def test_malformed_review_or_empty_discovery_refuses(source):
    root, inventory = source
    invalid = copy.deepcopy(inventory)
    invalid["skip"] = True
    assert coverage.check(root, invalid)[0] == ["invalid coverage inventory schema/keys"]
    row = next(iter(inventory["declarations"].values()))
    row["class"] = "unclassified"
    assert any("disposition" in e for e in errors(source))


def test_cli_has_positive_terminator_and_refuses_unreviewed_source(source):
    root, inventory = source
    path = root / coverage.INVENTORY
    path.parent.mkdir()
    path.write_text(json.dumps(inventory))
    command = [
        sys.executable,
        str(ROOT / "scripts/check_egress_profile_coverage.py"),
        "--root",
        str(root),
    ]
    healthy = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert healthy.returncode == 0
    assert json.loads(healthy.stdout)["status"] == "SOURCE_ACCOUNTING_ONLY"
    (root / "src/butlers/new.py").write_text('BASE = "https://new.provider.com"\n')
    refused = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert refused.returncode == 1
    assert "unreviewed declaration" in refused.stderr
    assert "SOURCE_ACCOUNTING_ONLY" not in refused.stdout


@pytest.mark.parametrize(
    "change", ["profile-source", "resolver-source", "resolver-symbol", "resolver-body"]
)
def test_dynamic_resolution_requires_its_real_owner_and_profile_declaration(source, change):
    root, inventory = source
    row = next(r for r in inventory["declarations"].values() if r["class"] == "dynamic")
    if change == "profile-source":
        p = root / coverage.PROFILES
        p.write_text(p.read_text().replace(row["resolver"]["source"], "TYPO_SOURCE"))
    elif change == "resolver-source":
        row["resolver"]["source"] = "TYPO_SOURCE"
    elif change == "resolver-symbol":
        row["resolver"]["symbol"] = "missing_resolver"
    else:
        p = root / "src/butlers/client.py"
        p.write_text(p.read_text().replace("return os.environ", "return other.environ"))
    assert any("resolver" in e for e in errors(source))


def test_ci_and_local_required_paths_use_the_same_guard():
    import yaml

    makefile = (ROOT / "Makefile").read_text()
    assert (
        "check-egress-profile-coverage" in makefile.split("check-guards:", 1)[1].split("\n", 1)[0]
    )
    assert (
        "check-egress-profile-coverage:\n\tpython3 scripts/check_egress_profile_coverage.py"
        in makefile
    )
    guards = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["guards"]
    step = next(s for s in guards["steps"] if s.get("id") == "egress_profile_coverage")
    assert step["run"] == "python3 scripts/check_egress_profile_coverage.py"
    assert step["if"] == "${{ !cancelled() }}"
    finalizer = next(s for s in guards["steps"] if s.get("name") == "Fail if any guard failed")
    assert (
        "egress_profile_coverage=${{ steps.egress_profile_coverage.outcome }}"
        in finalizer["env"]["GUARD_OUTCOMES"]
    )


@pytest.mark.parametrize(
    "change",
    ["missing-family", "series-identity", "patched-owner", "config", "profile-source", "catalogue"],
)
def test_vendor_resolution_families_are_named_bound_and_deny_by_default(change):
    inventory = json.loads((ROOT / coverage.INVENTORY).read_text())
    import tomllib

    profiles = tomllib.loads((ROOT / coverage.PROFILES).read_text())["workloads"]
    rows = inventory["vendor_resolvers"]
    healthy, gaps = coverage.vendor_resolvers(ROOT, rows, profiles)
    assert healthy == []
    assert any(g["resolver"] == "gemini:code-assist" for g in gaps)
    if change == "missing-family":
        del rows["opencode:plugin-tools"]
    elif change == "series-identity":
        rows["codex:auth"]["archive_sha256"] = "0" * 64
    elif change == "patched-owner":
        rows["opencode:http-ws"]["source"]["sha256"] = "0" * 64
    elif change == "config":
        rows["google-auth-library:adc"]["config"] = []
    elif change == "profile-source":
        profiles["session-host"]["dynamic"] = []
    else:
        rows["opencode:catalog"]["catalogue"]["models"] = 1
    assert coverage.vendor_resolvers(ROOT, rows, profiles)[0]
