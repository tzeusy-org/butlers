"""Native backend OpenAPI/value/degraded-shape contracts.

TypeScript compiler/AST tooling owns client grammar and route/query parity.
"""

from __future__ import annotations

import ast
import re
import warnings
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[2]

_INGESTION_CONNECTORS = (
    _REPO_ROOT / "src" / "butlers" / "api" / "routers" / "ingestion_connectors.py"
)

_CROSS_SUMMARY_HANDLER = "get_cross_connector_summary_with_aggregates"


def _cross_summary_payload_shapes() -> list[set[str]]:
    """The two literal data shapes returned by the cross-summary handler.

    This is deliberately not a general Python response-schema extractor.  The
    endpoint is typed as ``ApiResponse[dict]``, so OpenAPI cannot name its
    fields; these two literals are the source of truth for this one interface.
    """
    tree = ast.parse(
        _INGESTION_CONNECTORS.read_text(encoding="utf-8"), filename=str(_INGESTION_CONNECTORS)
    )
    handlers = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == _CROSS_SUMMARY_HANDLER
    ]
    assert len(handlers) == 1, f"expected one {_CROSS_SUMMARY_HANDLER} handler"
    handler = handlers[0]

    bindings = {
        target.id: assignment.value
        for assignment in handler.body
        if isinstance(assignment, ast.Assign) and isinstance(assignment.value, ast.Dict)
        for target in assignment.targets
        if isinstance(target, ast.Name)
    }
    shapes: dict[str, set[str]] = {}
    for node in ast.walk(handler):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (
            isinstance(call.func, ast.Subscript)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "ApiResponse"
        ):
            continue
        data = next((keyword.value for keyword in call.keywords if keyword.arg == "data"), None)
        if isinstance(data, ast.Name):
            data = bindings.get(data.id)
        assert isinstance(data, ast.Dict), (
            f"{_CROSS_SUMMARY_HANDLER} must return a literal data dictionary "
            "or a local name bound to one"
        )
        assert all(
            isinstance(key, ast.Constant) and isinstance(key.value, str) for key in data.keys
        )
        shapes.setdefault(ast.dump(data), {key.value for key in data.keys})
    return list(shapes.values())


@pytest.fixture(scope="module")
def openapi_paths() -> dict[str, dict]:
    from butlers.api.app import create_app

    app = create_app()
    schema = app.openapi()
    return schema["paths"]


def test_openapi_schema_has_no_duplicate_operation_ids():
    """Every router is mounted once so generated client contracts stay unambiguous."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from butlers.api.app import create_app

        create_app().openapi()

    duplicate_operation_ids = [
        str(warning.message)
        for warning in caught
        if "Duplicate Operation ID" in str(warning.message)
    ]
    assert duplicate_operation_ids == []


def test_legacy_switchboard_connector_namespace_is_absent(
    openapi_paths: dict[str, dict],
):
    """No compatibility route may preserve the retired connector namespace."""
    legacy_paths = sorted(
        path for path in openapi_paths if path.startswith("/api/switchboard/connectors")
    )
    assert legacy_paths == []


def test_openapi_schema_is_non_trivial(openapi_paths: dict[str, dict]):
    assert len(openapi_paths) >= 50, (
        f"Only {len(openapi_paths)} OpenAPI paths found — create_app().openapi() "
        "may not be mounting the full router set."
    )


_LIMIT_LITERAL_PINS: list[tuple[str, str, str, str, str]] = [
    # label, frontend path (under frontend/src), regex, OpenAPI path, param
    (
        "circles-groups",
        "components/relationship/CirclesPage.tsx",
        r"\bFETCH_LIMIT\s*=\s*(\d+)",
        "/api/relationship/groups",
        "limit",
    ),
    (
        "gantt-episodes",
        "components/chronicles/GanttSwimlane.tsx",
        r"limit:\s*(\d+)",
        "/api/chronicler/episodes",
        "limit",
    ),
    (
        "drilldown-episodes",
        "components/chronicles/ChroniclesDrilldownPanel.tsx",
        r"overlaps_end:[^,]*,\s*limit:\s*(\d+)",
        "/api/chronicler/episodes",
        "limit",
    ),
    (
        "drilldown-events",
        "components/chronicles/ChroniclesDrilldownPanel.tsx",
        r"until:[^,]*,\s*limit:\s*(\d+)",
        "/api/chronicler/events",
        "limit",
    ),
    (
        "measurement-chart",
        "components/health/MeasurementChart.tsx",
        r"limit:\s*(\d+)",
        "/api/health/measurements",
        "limit",
    ),
    (
        "symptom-conditions",
        "components/health/SymptomTracker.tsx",
        r"useConditions\(\{\s*limit:\s*(\d+)",
        "/api/health/conditions",
        "limit",
    ),
    (
        "entities-search",
        "components/relationship/EntitiesIndexPage.tsx",
        r"\bSEARCH_RESULT_LIMIT\s*=\s*(\d+)",
        "/api/relationship/entities/search",
        "limit",
    ),
    (
        "general-collections",
        "components/general/ButlerGeneralEntitiesTab.tsx",
        r"\bDROPDOWN_FETCH_LIMIT\s*=\s*(\d+)",
        "/api/general/collections",
        "limit",
    ),
    (
        "priority-sender-candidates",
        "components/ingestion/filters/FiltersPipeline.tsx",
        r"\bPRIORITY_CANDIDATE_LIMIT\s*=\s*(\d+)",
        "/api/relationship/entities",
        "limit",
    ),
    (
        "entity-facts-initial",
        "lib/entity-detail-query.ts",
        r"\bENTITY_DETAIL_INITIAL_FACTS_LIMIT\s*=\s*(\d+)",
        "/api/relationship/entities/{entity_id}/facts",
        "limit",
    ),
]


@pytest.mark.parametrize(
    ("label", "fe_relpath", "literal_regex", "api_path", "param_name"),
    _LIMIT_LITERAL_PINS,
    ids=[e[0] for e in _LIMIT_LITERAL_PINS],
)
def test_frontend_limit_literal_within_backend_ceiling(
    label: str,
    fe_relpath: str,
    literal_regex: str,
    api_path: str,
    param_name: str,
    openapi_paths: dict[str, dict],
):
    fe_file = _REPO_ROOT / "frontend" / "src" / Path(fe_relpath)
    assert fe_file.exists(), f"{label}: {fe_relpath} not found — update or remove this pin"
    text = fe_file.read_text(encoding="utf-8")

    matches = re.findall(literal_regex, text)
    assert matches, (
        f"{label}: literal regex {literal_regex!r} no longer matches in {fe_relpath} — "
        "the frontend structure changed. Re-audit this literal against its endpoint's "
        "declared ceiling and update the pin (do not just delete it)."
    )
    literals = [int(m) for m in matches]

    op = openapi_paths.get(api_path, {}).get("get")
    assert op is not None, f"{label}: GET {api_path} is no longer mounted"
    # Defensive: `parameters` may be absent or explicitly null; a param may
    # carry `content` instead of `schema` — either way fall through to the
    # clear "no maximum" assertion below rather than raising KeyError.
    param = next((p for p in (op.get("parameters") or []) if p["name"] == param_name), None)
    assert param is not None, (
        f"{label}: GET {api_path} no longer declares a `{param_name}` query param"
    )
    maximum = param.get("schema", {}).get("maximum")
    assert maximum is not None, (
        f"{label}: `{param_name}` on GET {api_path} no longer declares a maximum (le)"
    )

    worst = max(literals)
    assert worst <= maximum, (
        f"{label}: {fe_relpath} passes {param_name}={worst} to GET {api_path}, exceeding "
        f"its declared ceiling ({maximum}) — every real load will 422 (the bu-hmdqz.5 / "
        "PR #3173 CirclesPage regression class). Lower the frontend literal or raise the "
        "backend `le`."
    )


def test_cross_summary_backend_payload_shapes_match() -> None:
    """The success and degraded backend payloads expose the same fields."""
    payload_shapes = _cross_summary_payload_shapes()
    assert len(payload_shapes) == 2, (
        f"{_CROSS_SUMMARY_HANDLER} no longer has its two literal payload shapes; "
        "re-audit this guard against the handler before trusting it"
    )
    assert len({frozenset(fields) for fields in payload_shapes}) == 1, (
        f"{_CROSS_SUMMARY_HANDLER} success and zero/degraded payload fields diverged: "
        f"{payload_shapes}"
    )

    assert payload_shapes[0] == {
        "total_connectors",
        "connectors_online",
        "connectors_stale",
        "connectors_offline",
        "connectors_unclassified",
        "total_messages_ingested",
        "total_messages_failed",
        "overall_error_rate_pct",
        "aggregates_available",
    }
