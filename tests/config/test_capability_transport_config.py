"""Real TOML parsing: dark transport settings cannot activate or widen ENV reads."""

from pathlib import Path

import pytest

from butlers.config import ConfigError, load_config

pytestmark = pytest.mark.unit

BASE = '[butler]\nname="health"\nport=9100\n'
ENABLED = """
[butler.capability_broker_transport]
enabled=true
role="broker-control"
domain="switchboard"
endpoint="https://health.internal:9443/mcp"
[butler.capability_broker_transport.peer]
role="owning-service"
domain="health"
sans=["DNS:health.internal","URI:urn:butlers:health"]
[butler.capability_broker_transport.material]
ca_path="/unopened/test-ca"
certificate_path="/unopened/test-cert"
private_key_path="/unopened/test-key"
"""


def write(tmp_path: Path, text: str):
    (tmp_path / "butler.toml").write_text(BASE + text)
    return tmp_path


def test_literal_OFF_preserves_unused_missing_ENV_without_private_reads(tmp_path, monkeypatch):
    monkeypatch.delenv("BROKER_UNUSED_MISSING", raising=False)
    suffix = """
[butler.capability_broker_transport]
enabled=false
endpoint="${BROKER_UNUSED_MISSING}"
[butler.capability_broker_transport.material]
ca_path="${BROKER_UNUSED_MISSING}"
certificate_path="/never-open/cert"
private_key_path="/never-open/key"
"""
    original_stat = Path.stat
    original_open = Path.open

    def guard(original):
        def checked(path, *args, **kwargs):
            assert not str(path).startswith(("/never-open/", "${BROKER_UNUSED_MISSING}"))
            return original(path, *args, **kwargs)

        return checked

    monkeypatch.setattr(Path, "stat", guard(original_stat))
    monkeypatch.setattr(Path, "open", guard(original_open))
    cfg = load_config(write(tmp_path, suffix))
    transport = cfg.capability_broker_transport
    assert transport.enabled is False
    assert transport.endpoint == "${BROKER_UNUSED_MISSING}"
    assert transport.material.ca_path == "${BROKER_UNUSED_MISSING}"
    assert cfg.schedules == [] and cfg.name == "health"


def test_absent_and_implicit_OFF_are_typed_and_unrelated_ENV_still_refuses(tmp_path, monkeypatch):
    monkeypatch.delenv("BROKER_UNUSED_MISSING", raising=False)
    assert load_config(write(tmp_path, "")).capability_broker_transport.enabled is False
    implicit = '[butler.capability_broker_transport]\nendpoint="${BROKER_UNUSED_MISSING}"\n'
    assert load_config(write(tmp_path, implicit)).capability_broker_transport.enabled is False
    with pytest.raises(ConfigError):
        load_config(
            write(tmp_path, '[butler.logging]\nlevel="${BROKER_UNUSED_MISSING}"\n' + implicit)
        )


@pytest.mark.parametrize(
    "suffix",
    [
        "[butler.capability_broker_transport]\nenabled=0\n",
        '[butler.capability_broker_transport]\nenabled="false"\n',
        '[butler.capability_broker_transport]\nenabled=false\nunknown="x"\n',
        "[butler.capability_broker_transport]\nenabled=false\nendpoint=4\n",
        '[butler.capability_broker_transport]\nenabled=false\n[butler.capability_broker_transport.material]\nunknown="x"\n',
    ],
)
def test_OFF_never_erases_unknown_or_malformed_shape(tmp_path, suffix):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, suffix))


def test_enabled_maps_exact_roles_paths_and_endpoint_without_material_IO(tmp_path):
    cfg = load_config(write(tmp_path, ENABLED)).capability_broker_transport
    assert cfg.enabled is True and cfg.role == "broker-control"
    assert cfg.peer.domain == "health" and cfg.peer.sans == [
        "DNS:health.internal",
        "URI:urn:butlers:health",
    ]
    assert cfg.material.private_key_path == "/unopened/test-key"


@pytest.mark.parametrize(
    "old,new",
    [
        ("enabled=true", 'enabled="true"'),
        ('role="owning-service"', 'role="broker-control"'),
        ("https://health.internal:9443/mcp", "http://health.internal:9443/mcp"),
        ("https://health.internal:9443/mcp", "https://health.internal:9443/ordinary"),
        ("DNS:health.internal", "DNS:*.internal"),
        ('domain="health"', 'domain="health__override"'),
        ('private_key_path="/unopened/test-key"', 'private_key_path=""'),
    ],
)
def test_enabled_strict_endpoint_role_domain_SAN_and_material_shape_refuse(tmp_path, old, new):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, ENABLED.replace(old, new)))
