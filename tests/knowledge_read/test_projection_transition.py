"""Closed already-applied JSON and the shared owner/request transition."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from butlers.knowledge_read.manifest import (
    Manifest,
    ManifestError,
    decode_applied_manifest,
    load_manifest,
    manifest_transition,
)
from butlers.migrations.public_mcp_owner import (
    PUBLISHER_GRANTS_SQL,
    PUBLISHER_SECURITY_SQL,
    render_owner_program,
    subscriber_plan,
)


def test_actual_canonical_json_roundtrips_without_changing_digest():
    target = load_manifest()
    restored = decode_applied_manifest(target.canonical)
    assert restored == target
    assert restored.canonical == target.canonical
    assert restored.digest == target.digest
    assert len(restored.tables) == 47
    assert manifest_transition(restored, target).rebuild is False
    assert manifest_transition(restored, target).narrowing is False


@pytest.mark.parametrize(
    "mutation",
    [
        "root-extra",
        "table-extra",
        "duplicate-field",
        "duplicate-type",
        "foreign-source",
        "jsonb",
        "inconsistent-graph",
        "mixed-filter",
        "unknown-version",
        "not-canonical",
    ],
)
def test_applied_json_refuses_unknown_or_inconsistent_state(mutation):
    raw = load_manifest().canonical
    obj = json.loads(raw)
    if mutation == "root-extra":
        obj["ignored"] = True
    elif mutation == "table-extra":
        obj["tables"][0]["ignored"] = True
    elif mutation == "duplicate-field":
        raw = raw.replace('"version":1', '"version":1,"version":1')
    elif mutation == "duplicate-type":
        obj["tables"][0]["types"].append(obj["tables"][0]["types"][0])
    elif mutation == "foreign-source":
        obj["tables"][0]["source"] = []
    elif mutation == "jsonb":
        obj["tables"][0]["types"][0][1] = "jsonb"
    elif mutation == "inconsistent-graph":
        next(x for x in obj["tables"] if x["source"] == "public.entity_graph_edges")["filters"] = []
    elif mutation == "mixed-filter":
        next(x for x in obj["tables"] if x["source"] == "public.entities")["filters"][0][1] = [0]
    elif mutation == "unknown-version":
        obj["version"] = 2
    elif mutation == "not-canonical":
        raw = " " + raw
    if mutation not in {"duplicate-field", "not-canonical"}:
        raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ManifestError):
        decode_applied_manifest(raw)


def test_rebuild_and_narrowing_are_distinct_with_table_addition_companion():
    target = load_manifest()
    finance = next(t for t in target.tables if t.source == "finance.accounts")
    previous = Manifest(tuple(t for t in target.tables if t != finance))
    assert manifest_transition(previous, target).rebuild is False
    assert manifest_transition(previous, target).direction == "widening"
    assert manifest_transition(target, previous).rebuild is True
    assert manifest_transition(target, previous).direction == "narrowing"
    assert manifest_transition(None, target).rebuild is True
    assert manifest_transition(None, target).direction == "widening"

    # Existing-table column addition needs recopy, unlike table-only addition.
    narrower = replace(
        finance,
        columns=tuple(c for c in finance.columns if c != "last_four"),
        types=tuple(x for x in finance.types if x[0] != "last_four"),
        identifiers=tuple(c for c in finance.identifiers if c != "last_four"),
    )
    old = Manifest(tuple(narrower if t == finance else t for t in target.tables))
    assert manifest_transition(old, target).rebuild is True
    assert manifest_transition(old, target).narrowing is False
    assert manifest_transition(target, old).narrowing is True

    fact = next(t for t in target.tables if t.source == "general.facts")
    filters = tuple((c, values[:-1] if c == "predicate" else values) for c, values in fact.filters)
    tight = Manifest(tuple(replace(t, filters=filters) if t == fact else t for t in target.tables))
    assert manifest_transition(target, tight).rebuild is True
    assert manifest_transition(target, tight).narrowing is True
    assert manifest_transition(tight, target).rebuild is True
    assert manifest_transition(tight, target).narrowing is False


def test_closed_read_fields_preserve_required_exclusions_and_exact_decimal_text():
    target = load_manifest()
    plans = {p.table.source: p for p in subscriber_plan(target)}
    assert len(plans) == 47
    entities = {f.name for f in plans["public.entities"].read_fields}
    assert "canonical_name" in entities
    assert not entities & {"deleted", "unidentified", "placeholder_name"}
    graph = {f.name: f.expression for f in plans["public.entity_graph_edges"].read_fields}
    assert "subject_entity_id" in graph and "source_domain" in graph
    assert "publish_key" not in graph
    assert "general.facts" in graph["source_domain"]
    assert "'knowledge'" in graph["source_domain"]
    transaction = plans["finance.transactions"]
    amount = next(f for f in transaction.read_fields if f.name == "amount")
    assert amount.type == "text" and amount.expression == '"amount"::text'
    assert transaction.view_where == "deleted_at IS NULL"
    entity_fact = plans["relationship.entity_facts"]
    assert entity_fact.extras[0].name == "object_entity_id"
    assert "object_kind='entity'" in entity_fact.extras[0].expression
    assert any(f.name == "object_entity_id" for f in entity_fact.read_fields)
    # Subscriber-local declarations do not alter the source exposure decision.
    assert target.canonical == load_manifest().canonical
    assert (
        "object_entity_id"
        not in next(t for t in target.tables if t.source == "relationship.entity_facts").columns
    )


def test_rendering_is_deterministic_and_requires_no_connection_or_environment(monkeypatch):
    import os
    import socket

    target = load_manifest()

    def forbidden(*args, **kwargs):
        raise AssertionError("rendering must be connection/environment free")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    first = render_owner_program(target)
    second = render_owner_program(target)
    assert first == second
    assert target.digest in first and target.canonical in first
    assert "host=/controller/run dbname=butlers user=butlers_projection_publisher" in first
    assert "password_required=false" in first
    assert first.index("DELETE FROM mcp_local.manifest_applied") < first.index("DROP SUBSCRIPTION")
    assert first.index("SET ROLE mcp_projection_owner") < first.index("RESET ROLE")
    transfer = first.index("ALTER SUBSCRIPTION butlers_public_sub OWNER")
    assert first.rfind("RESET ROLE;", 0, transfer) > first.index(
        "CREATE OR REPLACE FUNCTION mcp_inbox"
    )
    assert first.rindex("INSERT INTO mcp_local.manifest_applied") > first.index(
        "REFRESH PUBLICATION"
    )
    assert "CASCADE" not in first


def test_bootstrap_and_owner_share_exact_publisher_security_and_grants():
    source = (Path(__file__).resolve().parents[2] / "scripts/init-db.sql").read_text()
    for name, expected in (("SECURITY", PUBLISHER_SECURITY_SQL), ("GRANTS", PUBLISHER_GRANTS_SQL)):
        actual = source.split(f"-- PUBLIC_MCP_PUBLISHER_{name}_BEGIN\n", 1)[1].split(
            f"\n    -- PUBLIC_MCP_PUBLISHER_{name}_END", 1
        )[0]
        assert actual == expected


def test_every_view_naming_an_entity_hides_rows_about_an_unpublished_one():
    plans = {p.table.source: p for p in subscriber_plan(load_manifest())}
    expected = {
        "public.entity_graph_edges": ("subject_entity_id", "object_entity_id"),
        "public.entity_graph_withheld_counts": ("entity_id",),
        "relationship.entity_facts": ("subject", "object_entity_id"),
        **{
            source: ("entity_id", "object_entity_id")
            for source in plans
            if source.endswith(".facts")
        },
    }
    assert len(expected) == 10
    for source, plan in plans.items():
        for column in expected.get(source, ()):
            assert (
                f'("{column}" IS NULL OR EXISTS (SELECT FROM public.entities AS e '
                f'WHERE e.id = "{column}"))'
            ) in plan.view_where
        if source not in expected:
            assert "public.entities" not in plan.view_where
    assert plans["finance.transactions"].view_where == "deleted_at IS NULL"


def test_a_fact_entry_that_cannot_name_its_entities_is_refused():
    target = load_manifest()
    fact = next(t for t in target.tables if t.source == "general.facts")
    columns = tuple(c for c in fact.columns if c != "object_entity_id")
    types = tuple((c, t) for c, t in fact.types if c != "object_entity_id")
    narrowed = replace(fact, columns=columns, types=types)
    manifest = Manifest(tuple(narrowed if t == fact else t for t in target.tables))
    with pytest.raises(ManifestError, match="facts must publish object_entity_id"):
        subscriber_plan(manifest)


@pytest.mark.parametrize(
    ("dropped", "refused"),
    [({"object_kind"}, True), ({"object", "object_kind"}, False)],
    ids=["object-without-its-kind", "neither"],
)
def test_entity_facts_cannot_publish_an_object_it_cannot_hide(dropped, refused):
    target = load_manifest()
    facts = next(t for t in target.tables if t.source == "relationship.entity_facts")
    narrowed = replace(
        facts,
        columns=tuple(c for c in facts.columns if c not in dropped),
        types=tuple(p for p in facts.types if p[0] not in dropped),
    )
    manifest = Manifest(tuple(narrowed if t == facts else t for t in target.tables))
    if refused:
        with pytest.raises(ManifestError, match="object published without its entity reference"):
            subscriber_plan(manifest)
    else:
        plan = next(p for p in subscriber_plan(manifest) if p.table == narrowed)
        assert plan.view_where == (
            '("subject" IS NULL OR EXISTS (SELECT FROM public.entities AS e WHERE e.id = "subject"))'
        )
