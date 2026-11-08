"""bu-dhloi1.15 / RFC 0038 S7: the reviewed egress profile file fails closed.

deploy/egress-profiles.toml is the only place egress widens (owner decision bu-l8y3fn = B).
These tests execute the parser and renderer the chart depends on; the rendered policy
union is evaluated in tests/scripts/test_chart_egress_profiles.py.
"""

from __future__ import annotations

import json

import pytest

from butlers.egress.profiles import (
    PROFILES_PATH,
    RENDERED_PATH,
    SCHEMA,
    ProfileError,
    load_profiles,
    main,
    parse_profiles,
    render,
)

pytestmark = pytest.mark.unit

_VALID = f"""
schema = "{SCHEMA}"

[workloads.alpha]
summary = "a"
gateway_port = 3101
internal = [{{ component = "beta", port = 41100, end_port = 41112 }}]
sites = [{{ site = "database", port = 5432 }}]
external = ["api.example-provider.com:443"]

[workloads.beta]
summary = "b"
"""


def _with(workload: str) -> str:
    return f'schema = "{SCHEMA}"\n\n[workloads.alpha]\nsummary = "a"\n{workload}\n'


def test_the_committed_profiles_parse_and_their_rendering_is_current():
    rendered = render(load_profiles())
    assert RENDERED_PATH.read_text(encoding="utf-8") == rendered, (
        "deploy/helm/butlers/files/egress-profiles.json is stale: run `make egress-profiles`"
    )
    assert json.loads(rendered)["schema"] == SCHEMA


def test_a_valid_profile_renders_every_declared_route():
    workload = json.loads(render(parse_profiles(_VALID)))["workloads"]["alpha"]
    assert workload["gateway_port"] == 3101
    assert workload["internal"] == [
        {"component": "beta", "port": 41100, "end_port": 41112, "protocol": "TCP"}
    ]
    assert workload["sites"] == [
        {"site": "database", "port": 5432, "protocol": "TCP", "optional": False}
    ]
    assert workload["external"] == [{"host": "api.example-provider.com", "port": 443}]
    assert workload["held"] is None


def test_the_held_analysis_host_is_absent_rather_than_admitted():
    # bu-l8y3fn q1: the still-held analysis host gets no profile at all.
    assert not any("analysis" in name for name in load_profiles().workloads)


def test_a_held_profile_admits_nothing():
    profiles = load_profiles()
    held = [p for p in profiles.workloads.values() if p.held]
    assert held, "the committed file is expected to hold at least the Telethon connector"
    for profile in held:
        assert (profile.internal, profile.sites, profile.external) == ((), (), ())
        assert profile.gateway_port is None


@pytest.mark.parametrize(
    "host",
    [
        "203.0.113.5",
        "*.googleapis.com",
        "API.example-provider.com",
        "api.example-provider.com.",
        "localhost",
        "intranet",
        "postgres-dev-rw.postgres-dev.svc.cluster.local",
        "butlers-up.svc",
        "device.tail1234.ts.net",
        "printer.local",
        "nas.lan",
        "metadata.google.internal",
        "router.home.arpa",
        "[2001:db8::1]",
        "api.example-provider.com:443",
        "under_score.example-provider.com",
        "metadata.google.internal\n",
        "device.tail1234.ts.net\n",
        "api.example-provider.com\n",
        "api.example-provider.com\r\n",
        "api.example-provider.com\t",
        "api.example-\nprovider.com",
    ],
)
def test_an_external_entry_that_is_not_an_exact_public_name_is_refused(host):
    with pytest.raises(ProfileError, match=r"external\[0\]"):
        parse_profiles(_with(f"gateway_port = 3101\nexternal = [{json.dumps(f'{host}:443')} ]"))


@pytest.mark.parametrize(
    ("workload", "match"),
    [
        ('gateway_port = 3101\nexternal = ["api.example-provider.com"]', "hostname:port"),
        ('gateway_port = 3101\nexternal = ["api.example-provider.com:0"]', "hostname:port"),
        ('gateway_port = 3101\nexternal = ["api.example-provider.com:0443"]', "hostname:port"),
        ('gateway_port = 3101\nexternal = ["api.example-provider.com:²"]', "hostname:port"),
        ('gateway_port = 3101\nexternal = ["api.example-provider.com:70000"]', "not a port in"),
        ('external = ["api.example-provider.com:443"]', "need a gateway_port"),
        ("gateway_port = 3101", "without external"),
        ('gateway_port = 80\nexternal = ["api.example-provider.com:443"]', "1024-65535"),
        ('gateway_port = true\nexternal = ["api.example-provider.com:443"]', "gateway_port"),
        (
            'gateway_port = 3101\nexternal = ["api.example-provider.com:443", '
            '"api.example-provider.com:443"]',
            "duplicate",
        ),
        ('internal = [{ component = "beta", port = true }]', "not a port in"),
        ('internal = [{ component = "beta", port = 41100, end_port = 41000 }]', "below"),
        ('internal = [{ component = "beta", port = 41100, protocol = "SCTP" }]', "protocol"),
        ('internal = [{ component = "Beta", port = 41100 }]', "component/site name"),
        ('internal = [{ component = "beta\\n", port = 41100 }]', "component/site name"),
        ('sites = [{ site = "database\\n", port = 5432 }]', "component/site name"),
        ('sites = [{ site = "data\\tbase", port = 5432 }]', "component/site name"),
        ('internal = [{ component = "beta", port = 41100, cidr = "0.0.0.0/0" }]', "unknown"),
        ('sites = [{ site = "database", port = 5432, optional = "yes" }]', "optional"),
        ('sites = [{ site = "database", port = 5432, cidr = "10.0.0.0/8" }]', "unknown"),
        ('held = "reason"\ninternal = [{ component = "beta", port = 1 }]', "admits nothing"),
        ('held = ""', "held"),
        ('widen = "anything"', "unknown keys"),
        ('dynamic = [{ source = "X_URL" }]', "reason"),
    ],
)
def test_an_invalid_workload_profile_is_refused(workload, match):
    with pytest.raises(ProfileError, match=match):
        parse_profiles(_with(workload))


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ('schema = "butlers-egress-profiles/v0"\n[workloads.alpha]\nsummary = "a"', "schema"),
        (f'schema = "{SCHEMA}"', "workloads"),
        (f'schema = "{SCHEMA}"\nruntime_widening = true\n[workloads.a]\nsummary = "a"', "unknown"),
        (f'schema = "{SCHEMA}"\n[workloads.alpha]\n', "summary"),
        ("schema = ", "TOML"),
        (f'schema = "{SCHEMA}"\n[workloads."alpha\\n"]\nsummary = "a"', "component/site name"),
    ],
)
def test_an_invalid_document_is_refused(text, match):
    with pytest.raises(ProfileError, match=match):
        parse_profiles(text)


def test_a_shared_gateway_port_is_refused_because_it_is_the_workload_identity():
    text = _VALID + (
        '\n[workloads.gamma]\nsummary = "g"\ngateway_port = 3101\n'
        'external = ["api.example-provider.com:443"]\n'
    )
    with pytest.raises(ProfileError, match="workload identity"):
        parse_profiles(text)


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        pytest.param(
            '\n[workloads.gamma]\nsummary = "g"\n'
            'internal = [{ component = "egress-gateway", port = 3101 }]\n',
            "only through gateway_port",
            id="gateway-as-internal-peer-borrows-another-identity",
        ),
        pytest.param(
            '\n[workloads.egress-gateway]\nsummary = "g"\n',
            "not a profiled workload",
            id="gateway-as-workload",
        ),
        pytest.param(
            '\n[workloads.gamma]\nsummary = "g"\n'
            'internal = [{ component = "undeclared", port = 1, end_port = 65535 }]\n',
            "not a declared workload",
            id="undeclared-peer",
        ),
    ],
)
def test_a_peer_outside_the_declared_workloads_is_refused(extra, match):
    with pytest.raises(ProfileError, match=match):
        parse_profiles(_VALID + extra)


def test_check_mode_fails_on_a_stale_rendering_and_passes_once_rendered(tmp_path, capsys):
    output = tmp_path / "egress-profiles.json"
    output.write_text("{}\n", encoding="utf-8")
    args = ["--profiles", str(PROFILES_PATH), "--output", str(output)]
    assert main([*args, "--check"]) == 1
    assert "stale" in capsys.readouterr().err
    assert main(args) == 0
    assert main([*args, "--check"]) == 0


def test_an_invalid_profile_file_renders_nothing(tmp_path, capsys):
    profiles = tmp_path / "egress-profiles.toml"
    profiles.write_text(_with('external = ["203.0.113.5:443"]'), encoding="utf-8")
    output = tmp_path / "egress-profiles.json"
    assert main(["--profiles", str(profiles), "--output", str(output)]) == 1
    assert "invalid" in capsys.readouterr().err
    assert not output.exists()
