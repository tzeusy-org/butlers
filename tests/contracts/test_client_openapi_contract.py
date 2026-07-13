"""Contract test: frontend/src/api/client.ts vs the live FastAPI OpenAPI schema.

bu-hmdqz.5 — "un-break /entities/circles and gate FE params against the
backend OpenAPI contract."

WHY
---
``CirclesPage.tsx``'s ``FETCH_LIMIT = 500`` exceeded the backend's
``GET /api/relationship/groups`` ``limit`` ceiling (``le=200``,
``roster/relationship/api/router.py:555``), 422-ing the route on *every*
real load. It was structurally invisible because ``CirclesPage.test.tsx``
fully mocks ``useGroups`` — no unit test ever sends a real query string
through a real route-shape check. The same class of drift (a client
referencing a path or param the live backend no longer declares) left a
whole butler-detail tab rendering only error lines against deleted routes
for three weeks before anyone noticed (see docs/redesigns/2026-07-12-jarvis-
pursuit.md move 5 evidence).

This test closes that gap at the layer where it is cheap to check: every
``apiFetch(...)`` call in ``frontend/src/api/client.ts`` is statically
resolved to a path template and a set of query-parameter names, then
checked against ``create_app().openapi()`` — the schema FastAPI itself
would enforce at request time. No frontend build, no running backend, no
Docker: this is a pure static/introspection check.

Scope and design
-----------------
The scanner in this file is a small, self-contained, defensive TypeScript
*fragment* parser — NOT a general TS/JS parser. It understands exactly the
patterns ``client.ts`` actually uses (verified empirically: it resolves
443/443 functions that call ``apiFetch`` with zero unresolved call sites at
the time this test was written):

- string / template-literal literal first arguments to ``apiFetch<T>(...)``,
  including ``${...}`` interpolation (each interpolation collapses to one
  path-segment wildcard — we cannot know the *value*, only that a dynamic
  segment exists there);
- a first argument that is a local ``const`` (resolved via simple textual
  lookup within the same function body, including one level of ternary);
- query-parameter names set via ``.set("name", ...)`` on a
  ``URLSearchParams``, either inline or via a shared ``*SearchParams(...)``
  helper (``client.ts``'s own naming convention — 13 such helpers exist).

What this test does NOT do: resolve arbitrary caller-supplied numeric
*values* (e.g. a page component's own ``limit: 500`` literal) back through
a hook layer to its client.ts function — that requires a real call-graph
across ``frontend/src/hooks/*`` and every page/component, which is out of
scope for this contract (see the value-constraint test below for the one
concrete case this bug class was fixed for, and the per-literal pins in
``_LIMIT_LITERAL_PINS`` below). The follow-up audit of the other hardcoded
``limit:`` literals across ``frontend/src`` (``GanttSwimlane.tsx``,
``ChroniclesDrilldownPanel.tsx``, ``SymptomTracker.tsx``,
``MeasurementChart.tsx``, ``ManualRefreshButton.tsx``, and the at-bound
named constants) was completed in bu-5ela3: no production literal exceeded
its endpoint's ``le``, and every zero-headroom literal is now pinned in
``_LIMIT_LITERAL_PINS`` so a future ``le`` tightening (or a bumped literal)
fails CI instead of 422-ing every load. Two calls to the dead
``GET /api/relationship/contacts`` path (``FiltersPipeline.tsx``,
``EntityDetailPage.tsx``) 404 rather than 422 — a different drift class,
tracked via the dead-path allowlist, not the pins here.

Known, tracked exceptions
--------------------------
- The dead ``/relationship/contacts*`` family (11 client.ts functions) —
  ``public.contacts`` was DROPped (core_134, bu-y6o7q) and its router was
  fully removed, so these routes 404 live. Excising the client fns / hooks /
  ``ButlerRelationshipContactsTab`` that still consume them is deferred to
  the contact-era excision cluster (epic bu-oluyt) — this test documents the
  drift instead of silently ignoring it (see ``KNOWN_DEAD_PATH_FUNCTIONS``).
- Five pre-existing "extra query param the backend does not declare" cases
  (``getSessions``, ``getSessionAggregate``, ``getButlerSessions``,
  ``getButlerNotifications``, ``getGeneralCollections``) — see
  ``KNOWN_UNDECLARED_QUERY_PARAMS`` for the per-function rationale. FastAPI
  silently ignores undeclared query params (no 422), so these are latent
  cruft rather than a live break; flagged as a discovered follow-up rather
  than fixed inline here.
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

# tests/contracts/test_client_openapi_contract.py -> tests/ -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLIENT_TS = _REPO_ROOT / "frontend" / "src" / "api" / "client.ts"

# Wildcard marker substituted for each `${...}` template interpolation.
_WILDCARD = "\x00"

# Guards the scanner itself: if fewer than this many apiFetch call sites
# resolve, the parser has regressed (silently) and every assertion below
# would be vacuous. At the time of writing, 443/443 resolve. Kept close to
# that count (not a round number like 400) so a single newly-unparseable
# call site — one function silently dropped by a future authoring pattern —
# trips this guard instead of hiding inside slack (PR #3173 review: a wide
# buffer here would let coverage rot without failing).
_MIN_RESOLVED_FUNCTIONS = 430

# ---------------------------------------------------------------------------
# Known, tracked contract exceptions
# ---------------------------------------------------------------------------

# The dead /relationship/contacts* family (bu-oluyt contact-era excision
# cluster). Keyed by client.ts export name -> the OpenAPI-normalized path
# template it resolves to (informational; asserted against below).
KNOWN_DEAD_PATH_FUNCTIONS: dict[str, str] = {
    "getContacts": "/relationship/contacts",
    "getContact": f"/relationship/contacts/{_WILDCARD}",
    "patchContact": f"/relationship/contacts/{_WILDCARD}",
    "deleteContact": f"/relationship/contacts/{_WILDCARD}",
    "archiveContact": f"/relationship/contacts/{_WILDCARD}/archive",
    "unarchiveContact": f"/relationship/contacts/{_WILDCARD}/unarchive",
    "createContactInfo": f"/relationship/contacts/{_WILDCARD}/contact-info",
    "deleteContactInfo": f"/relationship/contacts/{_WILDCARD}/contact-info/{_WILDCARD}",
    "patchContactInfo": f"/relationship/contacts/{_WILDCARD}/contact-info/{_WILDCARD}",
    "getContactInteractions": f"/relationship/contacts/{_WILDCARD}/interactions{_WILDCARD}",
    "getOverdueContacts": f"/relationship/contacts/overdue{_WILDCARD}",
}

# Pre-existing "sends a query param the backend does not declare" drift,
# discovered by this test's first run (not introduced by bu-hmdqz.5).
# FastAPI ignores undeclared query params rather than 422-ing, so these are
# latent cruft, not a live break. Rationale per entry:
KNOWN_UNDECLARED_QUERY_PARAMS: dict[str, set[str]] = {
    # sessionSearchParams() is a single shared builder feeding THREE routes
    # with different pagination models (keyset-cursor for /sessions and
    # /sessions/aggregate; offset for /butlers/{name}/sessions) plus a
    # butler-scoped route where `butler` is redundant (implied by the path).
    # It unconditionally sets both `offset` and `cursor` regardless of which
    # route consumes it, so each individual route sees one param it never
    # declared.
    "getSessions": {"offset", "include_trigger_breakdown"},
    "getSessionAggregate": {"cursor", "offset", "limit"},
    "getButlerSessions": {"cursor", "butler", "include_trigger_breakdown"},
    # getButlerNotifications hits a butler-scoped path
    # (/butlers/{name}/notifications); `butler` is implied by the path and
    # never actually needed in the query string.
    "getButlerNotifications": {"butler"},
    # getGeneralCollections sends `q` unconditionally, but
    # GET /api/general/collections does not declare a `q` query param —
    # the backend silently ignores it (the collections search box may not
    # actually filter server-side). Worth auditing separately.
    "getGeneralCollections": {"q"},
}


# ---------------------------------------------------------------------------
# Minimal, defensive TypeScript-fragment scanner
#
# This is NOT a general JS/TS parser. It implements just enough of a
# character-level scan (string/template-literal/comment-aware bracket
# matching) to resolve client.ts's own consistent authoring patterns. See
# the module docstring for exactly what it does and does not resolve.
# ---------------------------------------------------------------------------


def _skip_string(text: str, i: int, quote: str) -> int:
    i += 1
    n = len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return n


def _skip_template(text: str, i: int) -> int:
    i += 1
    n = len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "`":
            return i + 1
        if text[i] == "$" and i + 1 < n and text[i + 1] == "{":
            close = _find_matching(text, i + 1, "{", "}")
            i = close + 1
            continue
        i += 1
    return n


def _find_matching(text: str, open_idx: int, open_ch: str, close_ch: str) -> int:
    """Return the index of the bracket matching *open_ch* at *open_idx*.

    String literals, template literals, and comments are skipped whole so
    brackets inside them never desync the depth count.
    """
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            nl = text.find("\n", i)
            i = nl if nl != -1 else n
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = end + 2 if end != -1 else n
        elif c in "\"'":
            i = _skip_string(text, i, c)
        elif c == "`":
            i = _skip_template(text, i)
        elif c == open_ch:
            depth += 1
            i += 1
        elif c == close_ch:
            depth -= 1
            i += 1
            if depth == 0:
                return i - 1
        else:
            i += 1
    raise ValueError(f"unbalanced {open_ch}{close_ch} starting at {open_idx}")


_FUNC_START_RE = re.compile(r"(?:export (?:async )?|async )?\bfunction (\w+)\s*\(")


def _extract_functions(text: str) -> dict[str, str]:
    """Map every top-level (exported or helper) function name -> body text."""
    out: dict[str, str] = {}
    for m in _FUNC_START_RE.finditer(text):
        name = m.group(1)
        paren_open = m.end() - 1
        try:
            paren_close = _find_matching(text, paren_open, "(", ")")
            brace_open = text.index("{", paren_close + 1)
        except (ValueError, IndexError):
            continue
        # A bodyless declaration (TS overload signature / ambient `declare
        # function`) ends in `;` before any `{` of its own — the next `{`
        # `text.index` finds belongs to a *later* declaration. Skip rather
        # than misattribute that unrelated body to this name.
        if ";" in text[paren_close + 1 : brace_open]:
            continue
        try:
            brace_close = _find_matching(text, brace_open, "{", "}")
        except ValueError:
            continue
        out[name] = text[brace_open + 1 : brace_close]
    return out


def _split_top_level(text: str, sep: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            nl = text.find("\n", i)
            i = nl if nl != -1 else n
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = end + 2 if end != -1 else n
        elif c in "\"'":
            i = _skip_string(text, i, c)
        elif c == "`":
            i = _skip_template(text, i)
        elif c in "([{":
            depth += 1
            i += 1
        elif c in ")]}":
            depth -= 1
            i += 1
        elif depth == 0 and text.startswith(sep, i):
            parts.append(text[start:i])
            i += len(sep)
            start = i
        else:
            i += 1
    parts.append(text[start:i])
    return [p.strip() for p in parts if p.strip()]


def _split_ternary(expr: str) -> tuple[str, str, str] | None:
    depth = 0
    i = 0
    n = len(expr)
    qmark_idx = None
    while i < n:
        c = expr[i]
        if c in "\"'":
            i = _skip_string(expr, i, c)
        elif c == "`":
            i = _skip_template(expr, i)
        elif c in "([{":
            depth += 1
            i += 1
        elif c in ")]}":
            depth -= 1
            i += 1
        elif depth == 0 and c == "?" and expr[i : i + 2] not in ("??", "?."):
            qmark_idx = i
            break
        else:
            i += 1
    if qmark_idx is None:
        return None
    depth = 0
    i = qmark_idx + 1
    colon_idx = None
    while i < n:
        c = expr[i]
        if c in "\"'":
            i = _skip_string(expr, i, c)
        elif c == "`":
            i = _skip_template(expr, i)
        elif c in "([{":
            depth += 1
            i += 1
        elif c in ")]}":
            depth -= 1
            i += 1
        elif depth == 0 and c == ":":
            colon_idx = i
            break
        else:
            i += 1
    if colon_idx is None:
        return None
    return expr[:qmark_idx], expr[qmark_idx + 1 : colon_idx], expr[colon_idx + 1 :]


def _parse_template_literal(expr: str) -> str:
    inner = expr[1:-1]
    out: list[str] = []
    i = 0
    n = len(inner)
    while i < n:
        if inner[i] == "\\":
            out.append(inner[i : i + 2])
            i += 2
            continue
        if inner[i] == "$" and i + 1 < n and inner[i + 1] == "{":
            close = _find_matching(inner, i + 1, "{", "}")
            out.append(_WILDCARD)
            i = close + 1
            continue
        out.append(inner[i])
        i += 1
    return "".join(out)


def _resolve_path_expr(expr: str, local_consts: dict[str, str], depth: int = 0) -> list[str]:
    """Resolve a first-argument expression to candidate path templates.

    Handles string/template literals, one level of ternary (both branches
    are returned as candidates), and bare identifiers resolved against
    ``local_consts`` (this function's own ``const`` assignments).
    """
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")"):
        try:
            close = _find_matching(expr, 0, "(", ")")
        except ValueError:
            break
        if close != len(expr) - 1:
            break
        expr = expr[1:-1].strip()
    if depth > 6 or not expr:
        return []
    ternary = _split_ternary(expr)
    if ternary:
        _, a, b = ternary
        return _resolve_path_expr(a, local_consts, depth + 1) + _resolve_path_expr(
            b, local_consts, depth + 1
        )
    if expr[0] in "\"'" and expr[-1] == expr[0]:
        return [expr[1:-1]]
    if expr[0] == "`" and expr[-1] == "`":
        return [_parse_template_literal(expr)]
    if re.fullmatch(r"\w+", expr):
        if expr in local_consts:
            return _resolve_path_expr(local_consts[expr], local_consts, depth + 1)
        return []
    return []


_CONST_ASSIGN_RE = re.compile(r"\bconst\s+(\w+)\s*(?::\s*[^=]+)?=\s*")


def _extract_local_consts(body: str) -> dict[str, str]:
    consts: dict[str, str] = {}
    for m in _CONST_ASSIGN_RE.finditer(body):
        name = m.group(1)
        rhs_start = m.end()
        depth = 0
        i = rhs_start
        n = len(body)
        end = n
        while i < n:
            c = body[i]
            if body.startswith("//", i):
                nl = body.find("\n", i)
                i = nl if nl != -1 else n
                continue
            if body.startswith("/*", i):
                e = body.find("*/", i + 2)
                i = e + 2 if e != -1 else n
                continue
            if c in "\"'":
                i = _skip_string(body, i, c)
                continue
            if c == "`":
                i = _skip_template(body, i)
                continue
            if c in "([{":
                depth += 1
                i += 1
                continue
            if c in ")]}":
                depth -= 1
                i += 1
                continue
            if c == ";" and depth == 0:
                end = i
                break
            i += 1
        consts[name] = body[rhs_start:end].strip()
    return consts


_SEARCHPARAMS_CALL_RE = re.compile(r"\b(\w*SearchParams)\s*\(")
_SET_NAME_RE = re.compile(r"\.set\(\s*[\"']([A-Za-z0-9_]+)[\"']")
_APIFETCH_CALL_RE = re.compile(r"\bapiFetch\s*(<|\()")
_METHOD_RE = re.compile(r'method:\s*["\'](\w+)["\']')


def _extract_query_param_names(body: str, helper_bodies: dict[str, str]) -> set[str]:
    names = set(_SET_NAME_RE.findall(body))
    for helper in _SEARCHPARAMS_CALL_RE.findall(body):
        if helper in helper_bodies:
            names |= set(_SET_NAME_RE.findall(helper_bodies[helper]))
    return names


def _find_apifetch_call_args(body: str) -> list[str]:
    """Return the raw argument-list text of each apiFetch(...) call in *body*."""
    out = []
    for m in _APIFETCH_CALL_RE.finditer(body):
        i = m.end() - 1
        if body[i] == "<":
            depth = 1
            j = i + 1
            n = len(body)
            while j < n and depth > 0:
                if body[j] == "<":
                    depth += 1
                elif body[j] == ">":
                    depth -= 1
                j += 1
            while j < len(body) and body[j] != "(":
                j += 1
            if j >= len(body):
                continue
            i = j
        close = _find_matching(body, i, "(", ")")
        out.append(body[i + 1 : close])
    return out


class FunctionContract:
    """A resolved client.ts endpoint function: its path template(s), HTTP
    method, and the query-param names it sends."""

    __slots__ = ("name", "paths", "method", "query_names")

    def __init__(self, name: str, paths: list[str], method: str, query_names: set[str]):
        self.name = name
        self.paths = paths
        self.method = method
        self.query_names = query_names


def _scan_client_ts(text: str) -> dict[str, FunctionContract]:
    functions = _extract_functions(text)
    helper_bodies = {n: b for n, b in functions.items() if n.endswith("SearchParams")}

    contracts: dict[str, FunctionContract] = {}
    for name, body in functions.items():
        if name.endswith("SearchParams"):
            continue
        calls = _find_apifetch_call_args(body)
        if not calls:
            continue
        args = _split_top_level(calls[0], ",")
        if not args:
            continue
        local_consts = _extract_local_consts(body)
        candidates = _resolve_path_expr(args[0], local_consts)
        if not candidates:
            continue
        method = "get"
        if len(args) > 1:
            m = _METHOD_RE.search(args[1])
            if m:
                method = m.group(1).lower()
        # Path portion only (strip any literal query-string suffix).
        paths = list(dict.fromkeys(c.split("?")[0] for c in candidates))
        query_names = _extract_query_param_names(body, helper_bodies)
        contracts[name] = FunctionContract(name, paths, method, query_names)
    return contracts


def _path_matches(fe_template: str, api_path: str) -> bool:
    """Segment-wise match: a FE wildcard segment matches anything; an
    OpenAPI ``{param}`` segment matches anything; otherwise segments must be
    literally equal."""
    fe_full = "/api" + fe_template
    fe_segs = fe_full.split("/")
    api_segs = api_path.split("/")
    if len(fe_segs) != len(api_segs):
        return False
    for f, a in zip(fe_segs, api_segs):
        if _WILDCARD in f:
            continue
        if a.startswith("{") and a.endswith("}"):
            continue
        if f != a:
            return False
    return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client_ts_text() -> str:
    assert _CLIENT_TS.exists(), f"client.ts not found at {_CLIENT_TS}"
    return _CLIENT_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def fe_contracts(client_ts_text: str) -> dict[str, FunctionContract]:
    return _scan_client_ts(client_ts_text)


@pytest.fixture(scope="module")
def openapi_paths() -> dict[str, dict]:
    with warnings.catch_warnings():
        # FastAPI warns about pre-existing duplicate operation IDs in the
        # ingestion connectors router (unrelated to this contract) — not
        # this test's concern.
        warnings.simplefilter("ignore")
        from butlers.api.app import create_app

        app = create_app()
        schema = app.openapi()
    return schema["paths"]


# ---------------------------------------------------------------------------
# Sanity: the scanner itself resolves the vast majority of call sites.
# ---------------------------------------------------------------------------


def test_scanner_resolves_most_apifetch_call_sites(fe_contracts: dict[str, FunctionContract]):
    assert len(fe_contracts) >= _MIN_RESOLVED_FUNCTIONS, (
        f"Only resolved {len(fe_contracts)} apiFetch call sites "
        f"(expected >= {_MIN_RESOLVED_FUNCTIONS}). The client.ts scanner may "
        "have regressed — check for new authoring patterns it doesn't "
        "understand yet before trusting the assertions below."
    )


def test_openapi_schema_is_non_trivial(openapi_paths: dict[str, dict]):
    assert len(openapi_paths) >= 50, (
        f"Only {len(openapi_paths)} OpenAPI paths found — create_app().openapi() "
        "may not be mounting the full router set."
    )


# ---------------------------------------------------------------------------
# Check A: every FE path resolves to a live backend route (or is a
# documented, tracked exception).
# ---------------------------------------------------------------------------


def test_every_client_ts_path_matches_a_live_route_or_is_tracked_dead(
    fe_contracts: dict[str, FunctionContract], openapi_paths: dict[str, dict]
):
    api_path_list = list(openapi_paths.keys())
    offenders: list[str] = []
    for name, contract in fe_contracts.items():
        for template in contract.paths:
            if any(_path_matches(template, p) for p in api_path_list):
                continue
            if name in KNOWN_DEAD_PATH_FUNCTIONS:
                continue
            offenders.append(f"{name}: {template!r}")

    assert offenders == [], (
        "client.ts calls a path that does not exist in the live FastAPI "
        "OpenAPI schema (would 404 at runtime). If this is intentionally "
        "dead pending excision, add it to KNOWN_DEAD_PATH_FUNCTIONS with a "
        "rationale; otherwise fix the path or the route. Offenders:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


def test_known_dead_paths_are_still_actually_dead(
    fe_contracts: dict[str, FunctionContract], openapi_paths: dict[str, dict]
):
    """Anti-rot: if a tracked-dead route is ever re-mounted, the allowlist
    entry must be removed (not left stale, silently hiding a live route)."""
    api_path_list = list(openapi_paths.keys())
    still_alive = []
    for name in KNOWN_DEAD_PATH_FUNCTIONS:
        contract = fe_contracts.get(name)
        assert contract is not None, (
            f"{name} is in KNOWN_DEAD_PATH_FUNCTIONS but the scanner no "
            "longer finds it in client.ts — remove the stale allowlist entry."
        )
        for template in contract.paths:
            if any(_path_matches(template, p) for p in api_path_list):
                still_alive.append(name)
    assert still_alive == [], (
        "The following functions are allowlisted as dead but now match a "
        f"live route — remove them from KNOWN_DEAD_PATH_FUNCTIONS: {still_alive}"
    )


# ---------------------------------------------------------------------------
# Check B: every query param name client.ts sends is declared by the
# matched backend operation (or is a documented, tracked exception).
# ---------------------------------------------------------------------------


def _declared_query_params(
    contract: FunctionContract, openapi_paths: dict[str, dict]
) -> set[str] | None:
    """Union of declared query-param names across every live path this
    contract's templates match, restricted to its HTTP method. Returns None
    if no live path matched at all (already reported by Check A)."""
    declared: set[str] = set()
    matched_any = False
    for template in contract.paths:
        for api_path, operations in openapi_paths.items():
            if not _path_matches(template, api_path):
                continue
            op = operations.get(contract.method)
            if op is None:
                continue
            matched_any = True
            for param in op.get("parameters", []):
                if param.get("in") == "query":
                    declared.add(param["name"])
    return declared if matched_any else None


def test_every_client_ts_query_param_is_declared_or_tracked(
    fe_contracts: dict[str, FunctionContract], openapi_paths: dict[str, dict]
):
    offenders: list[str] = []
    for name, contract in fe_contracts.items():
        if not contract.query_names:
            continue
        declared = _declared_query_params(contract, openapi_paths)
        if declared is None:
            continue  # unmatched path already reported by Check A
        undeclared = contract.query_names - declared
        tracked = KNOWN_UNDECLARED_QUERY_PARAMS.get(name, set())
        unexpected = undeclared - tracked
        if unexpected:
            offenders.append(f"{name}: {sorted(unexpected)} (declared={sorted(declared)})")

    assert offenders == [], (
        "client.ts sends a query param name the matched backend operation "
        "does not declare (FastAPI silently ignores it, but this is drift — "
        "a stale param, a rename that didn't land both sides, or a genuine "
        "typo). If intentional and tracked, add it to "
        "KNOWN_UNDECLARED_QUERY_PARAMS with a rationale. Offenders:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


def test_known_undeclared_query_params_are_still_undeclared(
    fe_contracts: dict[str, FunctionContract], openapi_paths: dict[str, dict]
):
    """Anti-rot: if the backend starts declaring one of these params, the
    allowlist entry must shrink (not linger claiming drift that no longer
    exists)."""
    now_declared: list[str] = []
    for name, tracked in KNOWN_UNDECLARED_QUERY_PARAMS.items():
        contract = fe_contracts.get(name)
        assert contract is not None, (
            f"{name} is in KNOWN_UNDECLARED_QUERY_PARAMS but the scanner no "
            "longer finds it in client.ts — remove the stale allowlist entry."
        )
        declared = _declared_query_params(contract, openapi_paths) or set()
        newly_declared = tracked & declared
        if newly_declared:
            now_declared.append(f"{name}: {sorted(newly_declared)}")
    assert now_declared == [], (
        "The following tracked-undeclared query params are now declared by "
        "the backend — shrink their KNOWN_UNDECLARED_QUERY_PARAMS entry: \n"
        + "\n".join(f"  {o}" for o in now_declared)
    )


# ---------------------------------------------------------------------------
# Check C: value-constraint regression guard.
#
# This is the exact bug class bu-hmdqz.5 fixed: a client-side numeric
# literal exceeding a backend Query(..., le=N) ceiling, 422-ing every real
# request. General call-graph resolution of arbitrary FE literals is out of
# scope for the static scanner (see module docstring), so each known
# hardcoded pagination literal is pinned to its endpoint by hand (from the
# bu-5ela3 audit) and checked against the live OpenAPI `maximum`.
#
# This closes the class for every ZERO-HEADROOM literal — one whose value
# already equals its endpoint's ceiling (CirclesPage `limit:200`==200, the
# chronicler/health `limit:500`==500, etc.). A future `le` tightening OR a
# bumped FE literal fails CI here instead of 422-ing every page load. The
# bu-5ela3 audit confirmed no production literal currently exceeds its bound;
# these pins keep it that way.
#
# Each entry: (label, fe_relpath, literal_regex, api_path, param_name).
# `literal_regex` MUST capture the numeric literal in group 1 and match at
# least once — if the FE structure changes so it stops matching, the test
# fails LOUD (prompting a re-audit) rather than silently passing. Multiple
# matches are all checked (the worst/largest must satisfy the bound).
# ---------------------------------------------------------------------------

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
        "manual-refresh-events",
        "components/chronicles/ManualRefreshButton.tsx",
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
        "components/butler-detail/ButlerGeneralEntitiesTab.tsx",
        r"\bDROPDOWN_FETCH_LIMIT\s*=\s*(\d+)",
        "/api/general/collections",
        "limit",
    ),
    (
        "entity-facts-initial",
        "pages/EntityDetailPage.tsx",
        r"\bFACTS_INITIAL_LIMIT\s*=\s*(\d+)",
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


# ---------------------------------------------------------------------------
# Anti-vacuity: the scanner's core primitives behave as documented.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fe_template", "api_path", "expected"),
    [
        ("/relationship/groups", "/api/relationship/groups", True),
        (f"/relationship/groups/{_WILDCARD}", "/api/relationship/groups/{group_id}", True),
        (
            f"/relationship/groups/{_WILDCARD}/members",
            "/api/relationship/groups/{group_id}/members",
            True,
        ),
        ("/relationship/contacts", "/api/relationship/groups", False),
        (f"/relationship/contacts/{_WILDCARD}", "/api/relationship/contacts", False),
    ],
)
def test_path_matches_examples(fe_template: str, api_path: str, expected: bool):
    assert _path_matches(fe_template, api_path) is expected


def test_scan_finds_the_circles_groups_call(client_ts_text: str):
    contracts = _scan_client_ts(client_ts_text)
    assert "getGroups" in contracts
    assert contracts["getGroups"].paths == ["/relationship/groups"]
    assert contracts["getGroups"].query_names >= {"offset", "limit"}


def test_extract_functions_skips_bodyless_overload_signature():
    """Regression guard (PR #3173 review): a TS overload signature or
    ambient `declare function` ends in `;` before any `{` of its own. Naively
    scanning forward for the next `{` would steal the *following, unrelated*
    function's body and misattribute it under the bodyless name — e.g. a
    dead `alpha` overload silently inheriting `beta`'s real apiFetch call,
    fabricating a contract entry for an endpoint `alpha` never actually
    calls. The bodyless signature must be skipped entirely, and the
    following function must resolve to its own body only."""
    text = (
        "function alpha(x: string): void;\n"
        "function beta(y: number): void {\n"
        "  apiFetch('/beta-path');\n"
        "}\n"
    )
    functions = _extract_functions(text)
    assert "alpha" not in functions, "bodyless 'alpha' must not resolve — it has no body of its own"
    assert functions["beta"] == "\n  apiFetch('/beta-path');\n"


def test_extract_local_consts_stops_at_statement_semicolon_not_body_end():
    """The const extractor terminates on the first top-level `;`. Confirm it
    does not run past the true end of a single-line const assignment into
    unrelated statements that follow in the same function body (client.ts
    consistently semicolon-terminates local consts feeding apiFetch calls;
    see module docstring for the documented scope limit)."""
    body = 'const path = someCond ? "/a" : "/b"; apiFetch(path);'
    consts = _extract_local_consts(body)
    assert consts["path"] == 'someCond ? "/a" : "/b"'
