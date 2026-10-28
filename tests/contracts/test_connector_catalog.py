"""REQ-connector-base-spec-009: typed identity projections preserve transport boundaries."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import FrozenInstanceError
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from butlers.connectors.catalog import (
    CONNECTOR_DESCRIPTORS,
    NON_CONNECTOR_INGRESS_PAIRS,
    canonical_connector_types,
    discovery_connectors,
    helm_connectors,
    ingress_pairs,
    scrub_connector_labels,
)
from butlers.tools.switchboard.connector.heartbeat import (
    VALID_CONNECTOR_TYPES,
    ConnectorIdentityV1,
)
from butlers.tools.switchboard.contracts import (
    _ALLOWED_PROVIDERS_BY_CHANNEL,
    IngestSourceV1,
)

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_catalog_projects_distinct_namespaces_without_registering_aliases() -> None:
    keys = canonical_connector_types()
    assert len(keys) == len(CONNECTOR_DESCRIPTORS) == 13
    assert keys == VALID_CONNECTOR_TYPES
    assert {row.connector_type for row in discovery_connectors()} == keys
    assert {row.connector_type for row in helm_connectors()} == keys - {"live_listener"}
    assert scrub_connector_labels() == keys | {"telegram", "telegram_user", "live-listener"}
    assert NON_CONNECTOR_INGRESS_PAIRS.isdisjoint(
        {(row.channel, row.provider) for row in CONNECTOR_DESCRIPTORS}
    )
    for identity in (
        "telegram",
        "telegram_user",
        "live-listener",
        "discord",
        "discord_user",
        "slack",
    ):
        with pytest.raises(ValidationError, match="Unknown connector_type"):
            ConnectorIdentityV1(
                connector_type=identity, endpoint_identity="synthetic", instance_id=UUID(int=0)
            )
    live = next(row for row in CONNECTOR_DESCRIPTORS if row.connector_type == "live_listener")
    assert live.stored_event_type == "live-listener"
    assert not any(row.retired for row in CONNECTOR_DESCRIPTORS)
    with pytest.raises(FrozenInstanceError):
        live.provider = "caller-controlled"  # type: ignore[misc]


def test_catalog_preserves_every_existing_accepted_ingress_pair() -> None:
    expected = frozenset(
        (channel, provider)
        for channel, providers in _ALLOWED_PROVIDERS_BY_CHANNEL.items()
        for provider in providers
    )
    assert len(expected) == 19
    assert ingress_pairs() == expected
    for channel, provider in ingress_pairs():
        assert (
            IngestSourceV1(
                channel=channel, provider=provider, endpoint_identity="synthetic"
            ).provider
            == provider
        )
    for channel, provider in (("discord", "discord"), ("email", "google"), ("gaming", "gmail")):
        with pytest.raises(ValidationError):
            IngestSourceV1(channel=channel, provider=provider, endpoint_identity="synthetic")


def test_discovery_pairs_match_actual_connector_emission_defaults() -> None:
    for row in CONNECTOR_DESCRIPTORS:
        path = ROOT / "src" / (row.module.replace(".", "/") + ".py")
        if row.connector_type == "live_listener":
            path = ROOT / "src/butlers/connectors/live_listener/config.py"
        tree = ast.parse(path.read_text())
        constants = {
            n.targets[0].id: n.value.value
            for n in tree.body
            if isinstance(n, ast.Assign)
            and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.Constant)
        }
        for field in ("channel", "provider"):
            constant = constants.get(f"_CONNECTOR_{field.upper()}")
            if constant is not None:
                actual = {constant}
            else:
                actual = {
                    n.args[1].value
                    for n in ast.walk(tree)
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "get"
                    and len(n.args) > 1
                    and isinstance(n.args[0], ast.Constant)
                    and n.args[0].value == f"CONNECTOR_{field.upper()}"
                    and isinstance(n.args[1], ast.Constant)
                }
            assert actual == {getattr(row, field)}, (row.connector_type, field, actual)
        assert set(row.discovery_profile()) == {
            "connector_type",
            "channel",
            "provider",
            "display_name",
        }


# These exact literal populations are deferred S2 consumers or the existing
# wire vocabularies, not permission for a new list in an exempted file. A
# changed/new owner or changed labels must fail until that mechanism is derived.
_EXISTING_LITERALS = {
    (
        "src/butlers/api/oauth_scope_registry.py",
        "APPLICABILITY_MATRIX",
        "Dict",
    ): "0636b7c7b714fea93e81b7193b911925c49fcaf7e4d842bba921779e8b7749aa",
    (
        "src/butlers/api/routers/channel_defaults.py",
        "_REQUIRED_KEYS",
        "Dict",
    ): "c547916c9ee1d099dba491905a2ea408e80aa071685013e7ebe943910f259ceb",
    (
        "roster/switchboard/tools/contracts.py",
        "_ALLOWED_PROVIDERS_BY_CHANNEL",
        "Dict",
    ): "5402b6e3886ff75d8a7de292f79272e4dbfc970216673aac3ea00b314ed70358",
    (
        "roster/switchboard/tools/contracts.py",
        "SourceChannel",
        "Tuple",
    ): "5402b6e3886ff75d8a7de292f79272e4dbfc970216673aac3ea00b314ed70358",
    (
        "roster/switchboard/tools/contracts.py",
        "SourceProvider",
        "Tuple",
    ): "a3c015957c37e36c8e92452dff953836d5e0fbb9bdb4f050a22d17430d04bcb2",
    (
        "roster/switchboard/tools/connector/heartbeat.py",
        "VALID_CONNECTOR_TYPES",
        "Set",
    ): "dd2ee200a58870e54d227d463c0d5b5ed75fdbc5b7aa5ab545d2b13cedad96ac",
}


def _identity_literals(source: str) -> list[tuple[str, str, str]]:
    tree = ast.parse(source)
    owners = {}
    for node in ast.walk(tree):
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else ([node.target] if isinstance(node, ast.AnnAssign) else [])
        )
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        if names and node.value is not None:
            for child in ast.walk(node.value):
                owners[id(child)] = names[0]
    result = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
            values = node.elts
        elif isinstance(node, ast.Dict):
            values = node.keys
        else:
            continue
        labels = {
            v.value for v in values if isinstance(v, ast.Constant) and isinstance(v.value, str)
        }
        if len(labels & canonical_connector_types()) >= 6:
            digest = hashlib.sha256("\0".join(sorted(labels)).encode()).hexdigest()
            result.append((owners.get(id(node), "<expression>"), type(node).__name__, digest))
    return result


def test_no_new_large_hardcoded_connector_identity_populations() -> None:
    for parent in (ROOT / "src", ROOT / "roster"):
        for path in parent.rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            if "tests" in path.parts or "migrations" in path.parts:
                continue
            for owner, kind, digest in _identity_literals(path.read_text()):
                assert _EXISTING_LITERALS.get((relative, owner, kind)) == digest, (
                    relative,
                    owner,
                    kind,
                    "derive connector identities from catalog.py",
                )
    # Same labels moved/copied under another owner are still a new mechanism.
    source = "COPIED = ['gmail','steam','spotify','owntracks','google_drive','google_health']"
    found = _identity_literals(source)
    assert len(found) == 1 and found[0][0] == "COPIED"
    assert ("src/butlers/api/oauth_scope_registry.py", *found[0][:2]) not in _EXISTING_LITERALS
    assert _identity_literals("CATEGORIES = ['one','two','three','four','five','six']") == []
