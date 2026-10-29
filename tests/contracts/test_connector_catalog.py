"""REQ-connector-base-spec-009: typed identity projections preserve transport boundaries."""

from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
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
    from butlers.connectors.catalog import connector_descriptor

    for row in CONNECTOR_DESCRIPTORS:
        path = ROOT / "src" / (row.module.replace(".", "/") + ".py")
        if row.connector_type == "live_listener":
            path = ROOT / "src/butlers/connectors/live_listener/config.py"
        tree = ast.parse(path.read_text())
        binding = next(
            n
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_DESCRIPTOR" for t in n.targets)
        )
        assert isinstance(binding.value, ast.Call)
        assert ast.unparse(binding.value.func) == "connector_descriptor"
        assert len(binding.value.args) == 1
        assert isinstance(binding.value.args[0], ast.Constant)
        assert binding.value.args[0].value == row.connector_type
        owned = connector_descriptor(binding.value.args[0].value)
        for field in ("channel", "provider"):
            actual = {
                getattr(owned, field)
                for n in ast.walk(tree)
                if isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id == "_DESCRIPTOR"
                and n.attr == field
            }
            assert actual == {getattr(row, field)}, (row.connector_type, field, actual)
        assert set(row.discovery_profile()) == {
            "connector_type",
            "channel",
            "provider",
            "display_name",
        }


# Only the existing wire vocabularies remain literal populations.
# These are not permission for a new list in an exempted file. A
# changed/new owner or changed labels must fail until that mechanism is derived.
_EXISTING_LITERALS = {
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


def test_selector_namespaces_retain_all_shared_and_unsupported_alternatives() -> None:
    from typing import get_args

    from butlers.connectors.catalog import resolve_selector
    from butlers.tools.switchboard.contracts import SourceChannel, SourceProvider

    for namespace, selectors in (
        ("provider", get_args(SourceProvider)),
        ("channel", get_args(SourceChannel)),
    ):
        for selector in selectors:
            result = resolve_selector(namespace, selector)
            index = 1 if namespace == "provider" else 0
            assert set(result.pairs) == {p for p in ingress_pairs() if p[index] == selector}
            assert result.kind != "unknown"
            assert set(result.unsupported_pairs) == set(result.pairs) & NON_CONNECTOR_INGRESS_PAIRS
    assert resolve_selector("provider", "telegram").members == (
        "telegram_bot",
        "telegram_user_client",
    )
    wellness = resolve_selector("channel", "wellness")
    assert set(wellness.members) == {"google_health", "home_assistant"}
    assert wellness.kind == "group"
    email = resolve_selector("channel", "email")
    assert email.members == ("gmail",)
    assert email.unsupported_pairs == (("email", "imap"),)
    assert email.kind == "group"
    for selector in canonical_connector_types():
        result = resolve_selector("connector", selector)
        assert result.kind == "single" and result.members == (selector,)
    for alias, member in (("whatsapp", "whatsapp_user_client"), ("live-listener", "live_listener")):
        assert resolve_selector("connector", alias).kind == "alias"
        assert resolve_selector("connector", alias).members == (member,)
        assert alias not in canonical_connector_types()
    for retired in ("discord", "discord_user"):
        assert resolve_selector("connector", retired).kind == "retired"
    for value in ("unknown", "__proto__", "constructor", "spotify_user_client", "wellness"):
        assert resolve_selector("connector", value).kind == "unknown"
    for provider in ("imap", "internal", "slack"):
        assert resolve_selector("provider", provider).kind == "unsupported"
        assert not resolve_selector("provider", provider).members


def test_generated_recovery_projection_matches_catalog_and_refuses_stale_output(tmp_path) -> None:
    import importlib.util

    from butlers.connectors.catalog import public_recovery_capabilities

    path = ROOT / "scripts/render_connector_catalog.py"
    spec = importlib.util.spec_from_file_location("render_connector_catalog", path)
    assert spec is not None and spec.loader is not None
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    assert subprocess.run([sys.executable, str(path), "--check"], check=False).returncode == 0
    output = renderer.OUTPUT
    assert output.read_text() == renderer.render()
    # Exercise the real check path against a disposable stale artifact.
    stale = tmp_path / "stale.ts"
    stale.write_text(output.read_text() + "// stale\n")
    renderer.OUTPUT = stale
    from unittest.mock import patch

    with patch.object(sys, "argv", [str(path), "--check"]):
        assert renderer.main() == 1
    assert (
        not {"telegram", "activitywatch", "live_listener", "discord", "__proto__", "constructor"}
        & public_recovery_capabilities().keys()
    )


@pytest.mark.parametrize("key", ["telegram", "live-listener", "discord", "internal", "unknown"])
def test_row_lookup_never_promotes_alias_or_unsupported_identity(key: str) -> None:
    from butlers.connectors.catalog import connector_descriptor

    with pytest.raises(KeyError):
        connector_descriptor(key)
    for row in CONNECTOR_DESCRIPTORS:
        assert connector_descriptor(row.connector_type) is row


def test_retired_row_cannot_become_runtime_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from butlers.connectors import catalog

    row = catalog.CONNECTOR_DESCRIPTORS[0]
    monkeypatch.setattr(catalog, "CONNECTOR_DESCRIPTORS", (replace(row, retired=True),))
    with pytest.raises(KeyError):
        catalog.connector_descriptor(row.connector_type)


def test_static_identity_env_is_absent_from_runtime_and_compose() -> None:
    forbidden = {"CONNECTOR_PROVIDER", "CONNECTOR_CHANNEL"}
    for path in (ROOT / "src/butlers/connectors").rglob("*.py"):
        strings = {
            n.value
            for n in ast.walk(ast.parse(path.read_text()))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        assert not forbidden & strings, path
    compose = (ROOT / "docker-compose.yml").read_text()
    assert not any(f"      {key}:" in compose for key in forbidden)
