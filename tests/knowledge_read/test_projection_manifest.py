"""The exposure decision stays explicit, closed and independent of live discovery."""

from dataclasses import FrozenInstanceError
from importlib.resources import files

import pytest

from butlers.knowledge_read.manifest import ManifestError, credential, load_manifest


def test_packaged_manifest_is_complete_immutable_and_derives_graph_keys():
    manifest = load_manifest()
    assert len(manifest.tables) == 46
    assert sum(len(t.columns) for t in manifest.tables) == 469
    assert manifest.digest == load_manifest().digest
    tables = {t.source: t for t in manifest.tables}
    facts = [
        t
        for t in manifest.tables
        if t.domain not in ("finance", "health") and t.source.endswith((".facts", ".entity_facts"))
    ]
    keys = tuple(
        sorted(
            f"{t.source}:{p}" for t in facts for c, ps in t.filters if c == "predicate" for p in ps
        )
    )
    assert dict(tables["public.entity_graph_edges"].filters)["publish_key"] == keys
    assert dict(tables["public.entity_graph_withheld_counts"].filters)["source_key"] == tuple(
        sorted(t.source for t in facts)
    )
    assert tables["finance.facts"].filters == (("validity", ("active",)),)
    assert tables["health.facts"].filters == (("validity", ("active",)),)
    with pytest.raises(FrozenInstanceError):
        manifest.tables = ()
    for table in manifest.tables:
        assert set(table.key) <= set(table.identity) <= set(table.columns)
        assert not any(credential(c) for c in table.columns)
        assert not any(t in ("json", "jsonb", "vector") for _, t in table.types)
        assert table.publication_sql().startswith('TABLE "')


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        ("version = 1", "version = 2", "version"),
        ('domain = "knowledge"', 'domain = "unknown"', "domain"),
        ('source = "general.facts"', 'source = "general.state"', "private"),
        ('"content"', '"password"', "forbidden"),
        ('"text"', '"jsonb"', "types"),
        ('"sensitivity" = ["normal"]', '"sensitivity" = ["sensitive"]', "filters"),
        ('"validity" = ["active", "fading"]', '"validity" = ["active"]', "filters"),
        ('derived = "edges"', 'derived = "counts"', "derived"),
        ('key = ["id"]', 'key = ["missing"]', "key"),
    ],
)
def test_closed_manifest_refuses_positioned_policy_mutations(old, new, reason):
    data = files("butlers.knowledge_read").joinpath("projection.toml").read_text()
    assert old in data
    mutated = data.replace(old, new, 1)
    with pytest.raises(ManifestError, match=reason):
        load_manifest(mutated.encode())
    assert load_manifest(data.encode()).tables


def test_credential_segments_keep_allowed_neighbors():
    assert credential("metadata.apiKey") and credential("metadata.access_token")
    assert credential("metadata.pin")
    assert not credential("metadata.fingerprint") and not credential("shipping")


def test_credential_guard_has_a_type_consistent_control():
    data = files("butlers.knowledge_read").joinpath("projection.toml").read_text()
    # Change both the selected column and its declared type key. The only violated
    # boundary is the credential name, not a later missing/type declaration error.
    assert data.count('"state"') == 2
    with pytest.raises(ManifestError, match="forbidden column"):
        load_manifest(data.replace('"state"', '"password"').encode())
    assert (
        "state"
        in next(
            t for t in load_manifest(data.encode()).tables if t.source == "mcp_surface.controls"
        ).columns
    )


def test_marks_and_entity_flags_cannot_expand_privacy():
    data = files("butlers.knowledge_read").joinpath("projection.toml").read_text()
    cases = (
        ("metadata.external_ref", "metadata.password", "unsafe identifier mark"),
        ('"deleted" = [false]', '"deleted" = [true]', "entity privacy"),
        ('"tombstoned" = [false]', '"tombstoned" = [true]', "episode privacy"),
        ('"deleted" = [false]', '"deleted" = ["false"]', "literal type"),
    )
    for old, new, reason in cases:
        assert old in data
        with pytest.raises(ManifestError, match=reason):
            load_manifest(data.replace(old, new, 1).encode())
    assert load_manifest(data.encode()).tables
