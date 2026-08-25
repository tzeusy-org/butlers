"""Guard: a test may not hand the wall clock to an injectable ``now=`` parameter.

``test_req_commitment_lifecycle_005_delivery_records_the_commitment_in_the_ledger``
passed ``now=datetime.now(UTC)`` into ``delivery_cycle`` and asserted
``skipped is False``. ``delivery_cycle`` consults the Owner Attention Policy,
which the core_160 migration seeds as 23:00-08:00 Asia/Singapore, so the
assertion was contractually false for 15:00-24:00 UTC: red for nine hours of
every day, green for the other fifteen (fixed in 4d5e78ec4).

That failure does not present as a flake. It is perfectly deterministic given
the time, so a rerun an hour later "fixes" it and the test survives — which is
how it reached main and took down two unrelated PRs. Nor can the nightly
faketime matrix find it: those legs shift the process clock by whole days
(+45d/+120d), which preserves the hour of day the assertion actually depends on.

So the only detector is a scan of the source for the shape. This guard is that
scan, and its rule is narrow: a *test* that spells ``now=<a live clock read>``
has thrown away the only thing the ``now`` parameter is for. ``now`` is this
codebase's canonical injected-clock parameter — ~60 signatures under ``src/``
declare ``now: datetime``, and every one of them exists so a caller can say
*which instant*. A test that answers "whichever instant the CI queue reached"
is either wrong or deliberate, and the guard cannot tell which; the author can,
so the escape hatch makes them say it.

Deliberate live-clock tests are real and the hatch is for them: a differential
test that compares a Postgres-computed band against a Python-computed one needs
both sides reading the same real clock, and a test whose production collaborator
reads the clock itself (a DB trigger, a coordinator with no injection point)
must sign its inputs at approximately that clock. Those declare
``# live-clock: <reason>`` and move on.

Scope and limits, stated so nobody mistakes a pass here for proof:
- ``tests/`` only. Production code reading its own clock is the normal case.
- Keyword ``now=`` only. This codebase passes it by keyword everywhere, and a
  positional scan would need per-callee signatures to know which argument it is.
- One level of local binding, so ``now = datetime.now(UTC)`` followed by
  ``f(now=now)`` is caught. Two hops through helper functions are not.
- It cannot know whether the callee is actually clock-gated; it asks the
  question at the only place the answer is available.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from butlers.testing.source_guard import (
    enclosing_statement,
    local_bindings,
    parent_map,
    pragma_declaration,
    scope_nodes,
    scopes,
)

pytestmark = pytest.mark.unit

_GUARDED_TEST_ROOTS = ("tests", "roster")
_INJECTED_CLOCK_KEYWORD = "now"
# The reason after the colon is mandatory (enforced by ``pragma_declaration``):
# a bare marker would let the guard be silenced without anyone articulating why
# this test cannot name its own instant, and that articulation is the point.
_LIVE_CLOCK_PRAGMA = "live-clock:"

# ``<module>.<attr>`` pairs that read the wall clock. Matched on the attribute
# owner's *last* name segment, so ``datetime.now``, ``dt.datetime.now`` and
# ``datetime.datetime.now`` all read alike. ``time.monotonic``/``perf_counter``
# are deliberately absent: they measure elapsed time, not a calendar position,
# and no ``now: datetime`` parameter takes one.
_LIVE_CLOCK_READS = frozenset(
    {
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("datetime", "today"),
        ("date", "today"),
        ("time", "time"),
        ("time", "time_ns"),
    }
)
_MAX_BINDING_HOPS = 2


def _attribute_owner(node: ast.AST) -> str | None:
    """The name a call's attribute hangs off: ``dt.datetime.now`` -> ``datetime``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_live_clock_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    owner = _attribute_owner(node.func.value)
    return owner is not None and (owner, node.func.attr) in _LIVE_CLOCK_READS


def _live_clock_source(
    node: ast.AST, bindings: dict[str, ast.AST], hops: int = 0
) -> ast.AST | None:
    """The node that reads the clock inside *node*, or ``None``.

    ``datetime.now(UTC) - timedelta(days=1)`` is still a live clock: offsetting
    an unknown instant leaves it unknown. A name is followed through local
    bindings so the finding can be reported where the clock is read rather than
    at each of the places the resulting value is passed.
    """
    if _is_live_clock_call(node):
        return node
    if isinstance(node, ast.BinOp):
        return _live_clock_source(node.left, bindings, hops) or _live_clock_source(
            node.right, bindings, hops
        )
    if isinstance(node, ast.Name) and hops < _MAX_BINDING_HOPS and node.id in bindings:
        bound = bindings[node.id]
        return bound if _live_clock_source(bound, bindings, hops + 1) is not None else None
    return None


def injected_clock_findings(path: Path, source: str) -> list[str]:
    """Report every ``now=<live clock>`` argument in *source* lacking a declaration."""
    tree = ast.parse(source)
    lines = source.splitlines()
    parents = parent_map(tree)
    findings: dict[int, str] = {}
    for scope in scopes(tree):
        nodes = scope_nodes(scope)
        bindings = local_bindings(nodes)
        for node in nodes:
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != _INJECTED_CLOCK_KEYWORD:
                    continue
                clock = _live_clock_source(keyword.value, bindings)
                if clock is None:
                    continue
                # Report at the statement that reads the clock — the binding for
                # the two-statement form — so one declaration covers every call
                # fed by it, and it sits where the pinned instant would go.
                statement = enclosing_statement(clock, parents)
                marker, reason = pragma_declaration(statement, lines, _LIVE_CLOCK_PRAGMA)
                if marker and reason:
                    continue
                detail = (
                    f" (its '# {_LIVE_CLOCK_PRAGMA}' comment states no reason)" if marker else ""
                )
                call = ast.unparse(node.func)
                findings[statement.lineno] = (
                    f"{path}:{statement.lineno} reaches {call}(now=...) on line "
                    f"{node.lineno} with a live clock{detail}"
                )
    return [findings[lineno] for lineno in sorted(findings)]


def _guarded_test_sources() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[2]
    return sorted(
        path
        for root in _GUARDED_TEST_ROOTS
        for path in (repo_root / root).rglob("test_*.py")
        if "node_modules" not in path.parts
    )


def test_no_test_passes_a_live_clock_to_an_injected_now_parameter():
    """A test must name the instant it is asserting about, or say why it cannot."""
    findings: list[str] = []
    for path in _guarded_test_sources():
        source = path.read_text(encoding="utf-8")
        # The detector needs a `now=` keyword spelled in the source, so a file
        # without one cannot produce a finding.
        if f"{_INJECTED_CLOCK_KEYWORD}=" not in source:
            continue
        findings.extend(injected_clock_findings(path, source))

    assert findings == [], (
        "Tests pass a live clock into an injectable `now=` parameter. Pin the "
        "instant (and, where the instant's meaning depends on persisted state "
        "such as the Owner Attention Policy, pin that state too) so the "
        "assertion means the same thing at every hour of the day. A test that "
        "genuinely needs the real clock keeps it and says why with a "
        f"'# {_LIVE_CLOCK_PRAGMA} <reason>' comment — the reason is required, "
        "and must sit on the marker line:\n" + "\n".join(findings)
    )


# ---------------------------------------------------------------------------
# The guard's own red demonstrations. A guard nobody has seen fail is a guard
# nobody knows works, and each of these is a shape that reached main once.
# ---------------------------------------------------------------------------

_INLINE_LIVE_CLOCK = """
async def test_delivery_records_the_commitment(pool):
    result = await delivery_cycle(pool, notify_fn=notify, now=datetime.now(UTC))
    assert result["skipped"] is False
"""

_BOUND_LIVE_CLOCK = """
def test_is_stale_false_when_recently_verified():
    now = datetime.now(UTC)
    target = _make_target(last_verified=now - timedelta(minutes=5))
    assert _is_stale(target, staleness_s=3600, now=now) is False
"""

_MULTILINE_LIVE_CLOCK = """
def test_band_matches(pool):
    expected = staleness_band(
        store="identity",
        observed_at=observed,
        now=datetime.now(UTC) - timedelta(seconds=1),
    )
"""


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("inline", _INLINE_LIVE_CLOCK),
        ("bound-to-a-local-name", _BOUND_LIVE_CLOCK),
        ("multiline-and-offset", _MULTILINE_LIVE_CLOCK),
    ],
)
def test_guard_fires_on_a_live_clock(label, source):
    findings = injected_clock_findings(Path("tests/test_example.py"), textwrap.dedent(source))
    assert len(findings) == 1, f"{label}: {findings}"
    assert "with a live clock" in findings[0]


def test_guard_accepts_a_pinned_instant():
    source = textwrap.dedent("""
        async def test_delivery_records_the_commitment(pool):
            delivery_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
            result = await delivery_cycle(pool, notify_fn=notify, now=delivery_at)
            assert result["skipped"] is False
    """)
    assert injected_clock_findings(Path("tests/test_example.py"), source) == []


def test_guard_accepts_a_declared_live_clock():
    source = textwrap.dedent("""
        def test_sql_band_matches_python(pool):
            # live-clock: the SQL side reads Postgres now(), so the Python side
            # must read the same real clock for the comparison to mean anything.
            now = datetime.now(UTC)
            assert sql_band(pool) == staleness_band(observed_at=observed, now=now)
    """)
    assert injected_clock_findings(Path("tests/test_example.py"), source) == []


def test_a_declaration_without_a_reason_does_not_excuse_the_live_clock():
    source = textwrap.dedent("""
        def test_sql_band_matches_python(pool):
            now = datetime.now(UTC)  # live-clock:
            assert sql_band(pool) == staleness_band(observed_at=observed, now=now)
    """)
    findings = injected_clock_findings(Path("tests/test_example.py"), source)
    assert len(findings) == 1
    assert "states no reason" in findings[0]


def test_guard_ignores_a_live_clock_that_is_not_an_injected_instant():
    """Seeding a row with the real clock is not the defect; asserting on a
    clock-selected branch is. Only the ``now=`` argument is in scope."""
    source = textwrap.dedent("""
        def test_recent_row_is_returned(pool):
            observed = datetime.now(UTC) - timedelta(days=5)
            pool.execute("INSERT INTO facts (observed_at) VALUES ($1)", observed)
            assert fetch_recent(pool) == [observed]
    """)
    assert injected_clock_findings(Path("tests/test_example.py"), source) == []
