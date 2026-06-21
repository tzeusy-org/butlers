"""Tests for the ``relationship_lookup`` butler-facing read tool.

Binding spec:
    openspec/changes/entity-v3-lifecycle-and-depth/specs/relationship-entity-lookup/spec.md

Covers every spec scenario:
- exactly-one-arg validation (both / neither raise ValueError)
- deterministic entity_ref resolution + ranking order (prefix > contact > substring > predicate)
- ambiguous reference returns candidates with entity=null, no facts
- miss is a structured value, not an exception
- fact read shape: identity rows before narrative rows, full provenance + staleness
- staleness bands derive read-time (fresh / aging / stale)
- read-only: a SELECT-only fake pool proves zero writes
- docstring budget (<= 300 whitespace tokens) on the registered MCP tool
- in-session-only schedule guardrail (empty allowlist, scans roster butler.toml seeds)
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from butlers.tools.relationship.relationship_lookup import (
    _validate_args,
    relationship_lookup,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fake pool — dispatches fetch/fetchrow by matching the SQL text.
#
# It records every executed statement so a test can assert NO write ever ran
# (read-only contract). Any execute/INSERT/UPDATE/DELETE fails the test.
# ---------------------------------------------------------------------------


class _Row(dict):
    """A dict that also supports ``row["key"]`` like an asyncpg Record."""


class FakePool:
    def __init__(self, handlers: list[tuple[str, Any]]):
        # handlers: ordered list of (substring, result) — first matching substring wins.
        self._handlers = handlers
        self.statements: list[str] = []

    def _resolve(self, query: str) -> Any:
        self.statements.append(query)
        lowered = " ".join(query.split())
        # Hard guard: the lookup path must never mutate.
        for verb in ("insert ", "update ", "delete ", " merge "):
            assert verb not in lowered.lower(), f"lookup issued a write: {verb!r} in {query!r}"
        for needle, result in self._handlers:
            if needle in query:
                return result
        raise AssertionError(f"No fake handler matched query:\n{query}")

    async def fetch(self, query: str, *args: Any) -> Any:
        result = self._resolve(query)
        return result if isinstance(result, list) else []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        result = self._resolve(query)
        return result if not isinstance(result, list) else (result[0] if result else None)

    async def execute(self, query: str, *args: Any) -> None:  # pragma: no cover - guard
        raise AssertionError(f"lookup must not execute writes; got: {query}")


def _header_row(
    *,
    entity_id: uuid.UUID,
    canonical_name: str = "Northwind Plumbing",
    entity_type: str = "organization",
    aliases: list[str] | None = None,
    roles: list[str] | None = None,
    tier: int | None = None,
) -> _Row:
    return _Row(
        id=entity_id,
        canonical_name=canonical_name,
        entity_type=entity_type,
        aliases=aliases or [],
        roles=roles or [],
        tier=tier,
    )


def _state_row(
    *,
    is_unidentified: bool = False,
    is_dup_flagged: bool = False,
    has_fresh_fact: bool = True,
    shares_contact: bool = False,
) -> _Row:
    return _Row(
        is_unidentified=is_unidentified,
        is_dup_flagged=is_dup_flagged,
        has_fresh_fact=has_fresh_fact,
        shares_contact=shares_contact,
    )


def _identity_fact_row(
    *,
    predicate: str = "has-email",
    object: str = "ops@northwind.test",
    object_kind: str = "literal",
    src: str = "relationship",
    conf: float = 1.0,
    verified: bool = False,
    primary: bool | None = True,
    observed_at: datetime | None = None,
    last_seen: datetime | None = None,
    staleness_band: str = "fresh",
) -> _Row:
    return _Row(
        predicate=predicate,
        object=object,
        object_kind=object_kind,
        src=src,
        conf=conf,
        verified=verified,
        primary=primary,
        observed_at=observed_at or datetime.now(UTC),
        last_seen=last_seen,
        staleness_band=staleness_band,
    )


def _narrative_fact_row(
    *,
    predicate: str = "prefers",
    object: str = "morning calls",
    object_kind: str = "literal",
    src: str = "memory",
    conf: float = 0.8,
    observed_at: datetime | None = None,
    staleness_band: str = "aging",
) -> _Row:
    return _Row(
        predicate=predicate,
        object=object,
        object_kind=object_kind,
        src=src,
        conf=conf,
        observed_at=observed_at or datetime.now(UTC),
        staleness_band=staleness_band,
    )


def _recency_row(
    *, last_seen: datetime | None = None, last_interaction_at: datetime | None = None
) -> _Row:
    return _Row(last_seen=last_seen, last_interaction_at=last_interaction_at)


def _pool_for_entity(
    entity_id: uuid.UUID,
    *,
    header: _Row | None = None,
    state: _Row | None = None,
    identity_facts: list[_Row] | None = None,
    narrative_facts: list[_Row] | None = None,
    recency: _Row | None = None,
    band: str = "fresh",
) -> FakePool:
    """Build a fake pool wired for a successful entity_id lookup."""
    return FakePool(
        [
            # _classify_state — distinctive: selects is_unidentified etc. (checked first).
            ("is_unidentified", state or _state_row()),
            # _fetch_entity_header — distinctive: dunbar_tier_override subselect + canonical_name.
            ("dunbar_tier_override", header or _header_row(entity_id=entity_id)),
            # _fetch_identity_facts — distinctive: subject filter on entity_facts.
            ("WHERE f.subject  = $1", identity_facts or []),
            # _fetch_narrative_facts — distinctive: content AS object on facts table.
            ("f.content     AS object", narrative_facts or []),
            # _fetch_recency main row — distinctive: interaction_% predicate.
            ("interaction_%", recency or _recency_row()),
            # _fetch_recency band derivation
            ("THEN 'fresh'", _Row(band=band)),
        ]
    )


# ---------------------------------------------------------------------------
# Argument validation — exactly one of entity_id / entity_ref
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "make_id,ref,raises_match",
    [
        # exactly-one-required: both → reject; neither/blank-ref → reject.
        (True, "Alice", "not both"),
        (False, None, "exactly one"),
        (False, "   ", "exactly one"),
        # accepts exactly one of id / ref.
        (True, None, None),
        (False, "Alice", None),
    ],
    ids=["both", "neither", "blank-ref", "only-id", "only-ref"],
)
def test_validate_args(make_id, ref, raises_match):
    entity_id = uuid.uuid4() if make_id else None
    if raises_match is None:
        _validate_args(entity_id, ref)  # no raise
    else:
        with pytest.raises(ValueError, match=raises_match):
            _validate_args(entity_id, ref)


async def test_lookup_raises_when_both_args_given():
    pool = FakePool([])
    with pytest.raises(ValueError):
        await relationship_lookup(pool, entity_id=uuid.uuid4(), entity_ref="x")


async def test_lookup_raises_when_neither_arg_given():
    pool = FakePool([])
    with pytest.raises(ValueError):
        await relationship_lookup(pool)


# ---------------------------------------------------------------------------
# entity_id lookup — fact read shape, identity-before-narrative ordering
# ---------------------------------------------------------------------------


async def test_lookup_by_id_returns_entity_header_and_layered_facts():
    eid = uuid.uuid4()
    pool = _pool_for_entity(
        eid,
        identity_facts=[_identity_fact_row(predicate="has-email")],
        narrative_facts=[_narrative_fact_row(predicate="prefers")],
        recency=_recency_row(last_seen=datetime.now(UTC)),
    )
    result = await relationship_lookup(pool, entity_id=eid)

    assert result["entity"]["id"] == str(eid)
    assert result["entity"]["canonical_name"] == "Northwind Plumbing"
    assert set(result["entity"]) == {
        "id",
        "canonical_name",
        "entity_type",
        "aliases",
        "roles",
        "tier",
        "state",
    }
    # entity_id lookups carry no resolution block.
    assert result["resolution"] is None

    # Identity facts ordered before narrative facts.
    assert [f["store"] for f in result["facts"]] == ["identity", "narrative"]

    identity = result["facts"][0]
    for field in (
        "src",
        "conf",
        "verified",
        "primary",
        "observed_at",
        "last_seen",
        "staleness_band",
    ):
        assert field in identity, f"identity fact missing provenance field {field!r}"
    assert identity["store"] == "identity"

    narrative = result["facts"][1]
    assert narrative["store"] == "narrative"
    # Narrative rows have no last_seen column — field omitted.
    assert "last_seen" not in narrative
    assert narrative["verified"] is None


async def test_lookup_by_id_missing_entity_is_structured_miss():
    eid = uuid.uuid4()
    pool = FakePool([("dunbar_tier_override", None)])
    result = await relationship_lookup(pool, entity_id=eid)
    assert result["entity"] is None
    assert result["facts"] == []
    assert result["recency"] is None
    # Spec: a miss MUST carry a structured resolution block on the id path too —
    # never a bare ``resolution: None`` (relationship-entity-lookup §"Miss is a
    # value, not an error": {entity: null, resolution: {ambiguous: false,
    # candidates: []}}).
    assert result["resolution"] == {
        "matched_on": None,
        "score": None,
        "ambiguous": False,
        "candidates": [],
    }


async def test_lookup_tier_null_unless_override_pinned():
    eid = uuid.uuid4()
    pool = _pool_for_entity(eid, header=_header_row(entity_id=eid, tier=None))
    result = await relationship_lookup(pool, entity_id=eid)
    assert result["entity"]["tier"] is None

    pool2 = _pool_for_entity(eid, header=_header_row(entity_id=eid, tier=5))
    result2 = await relationship_lookup(pool2, entity_id=eid)
    assert result2["entity"]["tier"] == 5


# ---------------------------------------------------------------------------
# Staleness bands — read-time derivation surfaces on each fact
# ---------------------------------------------------------------------------


async def test_lookup_surfaces_staleness_bands_per_fact():
    eid = uuid.uuid4()
    pool = _pool_for_entity(
        eid,
        identity_facts=[
            _identity_fact_row(predicate="has-email", staleness_band="fresh"),
            _identity_fact_row(predicate="has-phone", staleness_band="stale"),
        ],
        narrative_facts=[_narrative_fact_row(staleness_band="aging")],
    )
    result = await relationship_lookup(pool, entity_id=eid)
    bands = [f["staleness_band"] for f in result["facts"]]
    assert bands == ["fresh", "stale", "aging"]
    for f in result["facts"]:
        assert f["staleness_band"] in {"fresh", "aging", "stale"}


# ---------------------------------------------------------------------------
# entity_ref resolution — deterministic ranking
# ---------------------------------------------------------------------------


def _resolve_pool(rank_rows: list[_Row], *, target: uuid.UUID | None = None) -> FakePool:
    """Fake pool for an entity_ref lookup with a ranking query then header reads."""
    handlers: list[tuple[str, Any]] = [
        ("WITH ranked AS", rank_rows),
    ]
    if target is not None:
        handlers += [
            ("is_unidentified", _state_row()),
            ("dunbar_tier_override", _header_row(entity_id=target)),
            ("WHERE f.subject  = $1", []),
            ("f.content     AS object", []),
            ("interaction_%", _recency_row()),
            ("THEN 'fresh'", _Row(band="fresh")),
        ]
    return FakePool(handlers)


def _rank_row(*, entity_id: uuid.UUID, canonical_name: str, score: int, match_kind: str) -> _Row:
    return _Row(
        entity_id=entity_id,
        canonical_name=canonical_name,
        score=score,
        match_kind=match_kind,
        last_seen=None,
        tier=None,
    )


async def test_ref_resolution_unique_top_score_resolves():
    target = uuid.uuid4()
    other = uuid.uuid4()
    rank_rows = [
        _rank_row(
            entity_id=target, canonical_name="Northwind Plumbing", score=100, match_kind="prefix"
        ),
        _rank_row(
            entity_id=other, canonical_name="Northwind Supply", score=50, match_kind="substring"
        ),
    ]
    pool = _resolve_pool(rank_rows, target=target)
    result = await relationship_lookup(pool, entity_ref="Northwind Plumbing")

    assert result["entity"]["id"] == str(target)
    assert result["resolution"]["ambiguous"] is False
    assert result["resolution"]["matched_on"] == "prefix"
    assert result["resolution"]["score"] == 100
    # candidates included (top 3)
    assert result["resolution"]["candidates"][0]["id"] == str(target)


async def test_ref_resolution_ranking_order_prefix_beats_contact_substring_predicate():
    """The ranking SQL orders score DESC; assert the constants reflect the search ranking."""
    import importlib

    rlu = importlib.import_module("butlers.tools.relationship.relationship_lookup")

    # The ranking is contract; the exact constant values are not.
    assert rlu._SCORE_PREFIX > rlu._SCORE_CONTACT_FACT > rlu._SCORE_SUBSTRING > rlu._SCORE_PREDICATE


async def test_ranking_sql_includes_last_seen_then_tier_tiebreak():
    """The resolution ORDER BY must tie-break on last_seen DESC then tier ASC."""
    import importlib
    import inspect

    rlu = importlib.import_module("butlers.tools.relationship.relationship_lookup")

    src = inspect.getsource(rlu._resolve_ref)
    order = src[src.index("ORDER BY") :]
    # score first, then last_seen DESC, then tier ASC.
    assert re.search(r"ORDER BY\s+score DESC", order)
    assert "last_seen DESC" in order
    assert "tier ASC" in order
    assert order.index("last_seen DESC") < order.index("tier ASC")


async def test_ref_resolution_ambiguous_returns_candidates_no_facts():
    a, b = uuid.uuid4(), uuid.uuid4()
    rank_rows = [
        _rank_row(entity_id=a, canonical_name="Alex Kim", score=100, match_kind="prefix"),
        _rank_row(entity_id=b, canonical_name="Alex King", score=100, match_kind="prefix"),
    ]
    pool = _resolve_pool(rank_rows)  # no target — must not read facts
    result = await relationship_lookup(pool, entity_ref="Alex")

    assert result["entity"] is None
    assert result["facts"] == []
    assert result["resolution"]["ambiguous"] is True
    assert len(result["resolution"]["candidates"]) == 2
    # No fact query should have run for any candidate.
    assert not any("WHERE f.subject  = $1" in s for s in pool.statements)
    assert not any("WHERE f.entity_id = $1" in s for s in pool.statements)


async def test_ref_resolution_caps_candidates_at_three():
    rows = [
        _rank_row(entity_id=uuid.uuid4(), canonical_name=f"E{i}", score=100, match_kind="prefix")
        for i in range(5)
    ]
    pool = _resolve_pool(rows)
    result = await relationship_lookup(pool, entity_ref="E")
    assert result["resolution"]["ambiguous"] is True
    assert len(result["resolution"]["candidates"]) == 3


# ---------------------------------------------------------------------------
# Miss is a value, not an error
# ---------------------------------------------------------------------------


async def test_ref_miss_returns_structured_value_not_exception():
    pool = _resolve_pool([])  # ranking returns nothing
    result = await relationship_lookup(pool, entity_ref="zzz-no-such-entity")
    assert result["entity"] is None
    assert result["facts"] == []
    assert result["resolution"]["ambiguous"] is False
    assert result["resolution"]["candidates"] == []


# ---------------------------------------------------------------------------
# Read-only contract — the fake pool fails on any write verb.
# ---------------------------------------------------------------------------


async def test_lookup_issues_no_writes():
    eid = uuid.uuid4()
    pool = _pool_for_entity(
        eid,
        identity_facts=[_identity_fact_row()],
        narrative_facts=[_narrative_fact_row()],
    )
    await relationship_lookup(pool, entity_id=eid)
    # FakePool asserts no INSERT/UPDATE/DELETE/MERGE ran; double-check here too.
    joined = " ".join(pool.statements).lower()
    for verb in ("insert ", "update ", "delete "):
        assert verb not in joined


# ---------------------------------------------------------------------------
# Docstring budget — the registered MCP tool docstring must be <= 300 tokens
# and state the read-only + in-session-only constraints.
# ---------------------------------------------------------------------------


def _registered_tool_docstring() -> str:
    import ast

    tools_src = (_REPO_ROOT / "roster" / "relationship" / "modules" / "tools.py").read_text()
    tree = ast.parse(tools_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "relationship_lookup":
            ds = ast.get_docstring(node)
            if ds is not None:
                return ds
    raise AssertionError(
        "relationship_lookup MCP tool not found in roster/relationship/modules/tools.py"
    )


def test_mcp_tool_docstring_within_300_token_budget():
    ds = _registered_tool_docstring()
    token_count = len(ds.split())
    assert token_count <= 300, f"docstring has {token_count} whitespace tokens (budget 300)"


def test_mcp_tool_docstring_states_constraints():
    ds = _registered_tool_docstring().lower()
    assert "read-only" in ds
    assert "in-session-only" in ds


# ---------------------------------------------------------------------------
# In-session-only schedule guardrail — empty allowlist.
#
# No scheduled-task seed or roster cron/scheduled-prompt may invoke the tool.
# The lookup spec says to scan "scheduled-task seed definitions and roster
# cron/scheduled-prompt files" for the literal ``relationship_lookup``. That
# scope is BOTH:
#   - roster/*/butler.toml          — the [[butler.schedule]] seed definitions
#   - roster/*/.agents/skills/**/SKILL.md — the *bodies* of scheduled prompts
#       (e.g. relationship-maintenance, upcoming-dates), which a scheduled
#       session reads and could be told to call relationship_lookup from.
# A skill instructing a scheduled session to call the lookup is exactly the
# bypass the empty allowlist must reject, so both globs are scanned.
# ---------------------------------------------------------------------------

#: Empty allowlist — any occurrence is a violation requiring a Phase D cost review.
_SCHEDULE_LOOKUP_ALLOWLIST: frozenset[str] = frozenset()


def _scheduling_source_paths() -> list[Path]:
    """All roster files that define or carry scheduled-session instructions.

    Globs are anchored on ``.agents`` (not the ``.claude -> .agents`` compat
    symlink) so each SKILL.md is counted exactly once.
    """
    roster = _REPO_ROOT / "roster"
    paths = list(roster.glob("*/butler.toml"))
    paths += list(roster.glob("*/.agents/skills/**/SKILL.md"))
    return sorted(set(paths))


def test_no_scheduled_seed_invokes_relationship_lookup():
    offenders: list[str] = []
    for path in _scheduling_source_paths():
        text = path.read_text()
        if "relationship_lookup" in text:
            rel = str(path.relative_to(_REPO_ROOT))
            if rel not in _SCHEDULE_LOOKUP_ALLOWLIST:
                offenders.append(rel)
    assert not offenders, (
        "relationship_lookup is referenced in scheduled-task seed files "
        f"{offenders}; the allowlist is empty by design (Phase D amendment 1 — "
        "the per-call LLM cost lives at the caller). Adding a schedule around "
        "the lookup (in a butler.toml seed OR a scheduled-prompt SKILL.md body) "
        "requires a new LLM-cost review, not a code change."
    )


def test_schedule_scan_globs_cover_butler_toml_and_skill_prompts():
    """The scan scope must include both seed TOMLs and scheduled-prompt SKILL.md files.

    Guards against a future narrowing that drops the skill/prompt glob and thus
    re-opens the bypass where a SKILL.md tells a scheduled session to call the tool.
    """
    paths = _scheduling_source_paths()
    suffixes = {p.name for p in paths}
    assert "butler.toml" in suffixes, "schedule scan must cover roster butler.toml seeds"
    assert any(p.name == "SKILL.md" for p in paths), (
        "schedule scan must cover roster scheduled-prompt SKILL.md bodies"
    )
    # The relationship butler's scheduled-prompt skills must be in scope.
    rels = {str(p.relative_to(_REPO_ROOT)) for p in paths}
    assert "roster/relationship/.agents/skills/relationship-maintenance/SKILL.md" in rels
    assert "roster/relationship/.agents/skills/upcoming-dates/SKILL.md" in rels


def test_schedule_scan_catches_lookup_literal_in_a_skill_prompt(tmp_path, monkeypatch):
    """Synthetic red: a SKILL.md whose body invokes relationship_lookup is caught.

    Builds a throwaway roster tree containing a scheduled-prompt SKILL.md that tells
    a session to call ``relationship_lookup``, points the scan at it, and asserts the
    widened glob flags it. The real roster tree stays green (no real skill violates).
    """
    fake_skill = (
        tmp_path / "roster" / "relationship" / ".agents" / "skills" / "bad-schedule" / "SKILL.md"
    )
    fake_skill.parent.mkdir(parents=True, exist_ok=True)
    fake_skill.write_text(
        "# Bad scheduled prompt\n\n"
        "When this scheduled session fires, call relationship_lookup(entity_ref=...).\n"
    )
    monkeypatch.setattr(
        "tests.relationship.test_relationship_lookup._REPO_ROOT", tmp_path, raising=False
    )

    offenders: list[str] = []
    for path in _scheduling_source_paths():
        if "relationship_lookup" in path.read_text():
            offenders.append(str(path.relative_to(tmp_path)))
    assert offenders == ["roster/relationship/.agents/skills/bad-schedule/SKILL.md"], (
        f"widened glob must catch a SKILL.md invoking the lookup; got {offenders}"
    )
