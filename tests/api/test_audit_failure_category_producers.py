"""Every credential-audit producer that can write a failure row names its cause.

Why a static enumeration instead of a behavioural test (bu-vhie6)
-----------------------------------------------------------------
``public.audit_log.failure_category`` is what lets a credential audit-error
group be identified by its **cause** rather than by the credential alone. That
guarantee is only worth anything if it holds for *every* producer: one endpoint
that writes ``result='error'`` without a category silently reopens the coarse
group it was meant to split, and no single passing endpoint test would notice.

So this file does not exercise one producer well. It enumerates all of them and
asserts a property of the whole set, which is the only evidence that supports
the claim "all producers write a category". It fails the moment someone adds a
new failure path without one -- the exact regression an example-based test
cannot see.

The census it pins
------------------
Credential-target audit rows (``target`` matching
``CREDENTIAL_TARGET_PATTERN``) have five writer helpers:

===================================  =========================================
``_write_credential_audit``          ``api/routers/secrets_v2.py``
``_write_system_audit``              ``api/routers/secrets_v2.py``
``_write_cli_audit``                 ``api/routers/secrets_v2.py``
``_emit_oauth_audit``                ``api/routers/oauth.py``
``audit_router.append`` (direct)     ``jobs/secrets_lifecycle.py``
===================================  =========================================

Only ``action="failed"`` rows become ``result='error'`` rows
(``credential_lifecycle_outcome``), and only those reach the grouping CTE. The
tests below resolve each call site's ``action`` -- through a local variable and
through a conditional expression, since two probe endpoints compute it as
``"verified" if probe_ok else "failed"`` -- and require ``failure_category`` on
exactly the sites that can produce a failure.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from butlers.api.models.audit import PROBE_FAILURE_VOCABULARY

_SRC = Path(__file__).resolve().parents[2] / "src" / "butlers"

_SECRETS_V2 = _SRC / "api" / "routers" / "secrets_v2.py"
_OAUTH = _SRC / "api" / "routers" / "oauth.py"
_LIFECYCLE = _SRC / "jobs" / "secrets_lifecycle.py"

#: The credential-audit writer helpers, by the name a call site uses.
_WRITER_NAMES = frozenset(
    {
        "_write_credential_audit",
        "_write_system_audit",
        "_write_cli_audit",
        "_emit_oauth_audit",
    }
)


class _CallSite:
    """One resolved credential-audit write."""

    def __init__(self, *, path: Path, lineno: int, func: str, enclosing: str) -> None:
        self.path = path
        self.lineno = lineno
        self.func = func
        self.enclosing = enclosing
        self.actions: set[str | None] = set()
        self.has_failure_category = False

    @property
    def where(self) -> str:
        return f"{self.path.name}:{self.lineno} ({self.enclosing} -> {self.func})"

    @property
    def can_fail(self) -> bool:
        """True when this site can write ``action='failed'``.

        ``None`` means the action could not be resolved to literals at all; that
        is treated as "can fail", so an unresolvable site fails the test rather
        than slipping through it.
        """
        return "failed" in self.actions or None in self.actions


def _literal_actions(node: ast.AST, assignments: dict[str, list[ast.AST]]) -> set[str | None]:
    """Resolve an ``action=`` argument to the set of literals it can take."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _literal_actions(node.body, assignments) | _literal_actions(node.orelse, assignments)
    if isinstance(node, ast.Name):
        bound = assignments.get(node.id)
        if not bound:
            return {None}
        resolved: set[str | None] = set()
        for value in bound:
            resolved |= _literal_actions(value, {})  # one hop; no constant chains exist
        return resolved
    return {None}


def _module_scope(tree: ast.Module) -> dict[str, list[ast.AST]]:
    """Module-level string constants, so a positional ``action`` still resolves.

    ``jobs/secrets_lifecycle`` passes its action as the module constant
    ``_LIFECYCLE_NOTIFIED_ACTION``; without this its site would resolve to
    "unknown" and be treated as a failure producer.
    """
    scope: dict[str, list[ast.AST]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    scope.setdefault(target.id, []).append(node.value)
    return scope


def _collect(path: Path) -> list[_CallSite]:
    """Return every credential-audit *production* site in *path*.

    A helper's own ``append`` call is plumbing, not a producer: it forwards
    whatever its caller passed. Those are excluded here and covered instead by
    :func:`test_each_writer_helper_forwards_the_category`, so the counts below
    stay a census of endpoints rather than of endpoints plus their plumbing.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_scope = _module_scope(tree)
    sites: list[_CallSite] = []

    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if func.name in _WRITER_NAMES:
            continue
        # Every value ever assigned to a local name in this function, so an
        # ``action=audit_action`` argument can be resolved to its literals.
        assignments = dict(module_scope)
        for node in ast.walk(func):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assignments.setdefault(target.id, []).append(node.value)

        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in _WRITER_NAMES and name != "append":
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords}
            if name == "append":
                # ``audit_router.append`` is generic; only the credential-target
                # calls belong to this census, and every one of them names its
                # target by keyword.
                if "target" not in keywords:
                    continue
            action_node = keywords.get("action")
            if action_node is None and name == "append" and len(node.args) >= 3:
                action_node = node.args[2]
            site = _CallSite(path=path, lineno=node.lineno, func=name, enclosing=func.name)
            site.actions = (
                _literal_actions(action_node, assignments) if action_node is not None else {None}
            )
            site.has_failure_category = "failure_category" in keywords
            sites.append(site)

    return sites


@pytest.fixture(scope="module")
def call_sites() -> list[_CallSite]:
    return _collect(_SECRETS_V2) + _collect(_OAUTH) + _collect(_LIFECYCLE)


def test_the_census_is_the_size_this_module_documents(call_sites: list[_CallSite]) -> None:
    """A count, so a new producer file cannot be quietly left out of the sweep.

    The enumeration below is only as good as its reach: if ``_collect`` stops
    finding call sites (a helper renamed, a keyword turned positional) every
    other assertion here passes vacuously over an empty list.
    """
    by_file = {path.name: 0 for path in (_SECRETS_V2, _OAUTH, _LIFECYCLE)}
    for site in call_sites:
        by_file[site.path.name] += 1

    assert by_file == {
        "secrets_v2.py": 15,
        "oauth.py": 11,
        "secrets_lifecycle.py": 1,
    }, f"the credential-audit call-site census moved: {by_file}"


def test_every_failure_producer_persists_a_category(call_sites: list[_CallSite]) -> None:
    """The claim AC1 makes, asserted over the whole producer set.

    A ``result='error'`` credential row with no ``failure_category`` groups
    under the uncategorised title, folding back together with every other
    uncategorised cause on that credential -- which is the coarse grouping
    bu-vhie6 exists to end.
    """
    missing = [site.where for site in call_sites if site.can_fail and not site.has_failure_category]
    assert not missing, (
        "these credential-audit writes can produce a failure row but persist no "
        f"failure_category: {missing}"
    )


def test_success_only_producers_stay_uncategorised(call_sites: list[_CallSite]) -> None:
    """The written decision, pinned: NULL for a success row is correct, not a gap.

    A category on a success row would be meaningless at best and, since the
    grouping CTE reads only ``result='error'``, invisible at worst. Asserting
    the negative keeps the column's domain honest instead of letting a future
    change spray a default across every audit write.
    """
    stray = [site.where for site in call_sites if not site.can_fail and site.has_failure_category]
    assert not stray, f"a success-only credential audit write carries a category: {stray}"

    # And the success-only set is not empty, so the assertion above is not
    # passing over nothing.
    success_only = [site for site in call_sites if not site.can_fail]
    assert len(success_only) == 17, (
        f"the success-only producer count moved: {[s.where for s in success_only]}"
    )


def test_failure_producers_are_the_ten_sites_the_docs_name(call_sites: list[_CallSite]) -> None:
    """Which sites can fail, by name, so the docstring census stays checkable."""
    failing = sorted((site.path.name, site.enclosing) for site in call_sites if site.can_fail)
    assert failing == [
        ("oauth.py", "_google_callback_from_state"),
        ("oauth.py", "_google_callback_from_state"),
        ("oauth.py", "_google_callback_from_state"),
        ("oauth.py", "_google_callback_from_state"),
        ("oauth.py", "oauth_provider_callback"),
        ("oauth.py", "oauth_provider_callback"),
        ("oauth.py", "oauth_provider_callback"),
        ("oauth.py", "oauth_provider_callback"),
        ("secrets_v2.py", "probe_system_credential"),
        ("secrets_v2.py", "probe_user_credential"),
    ], f"the credential-failure producer set moved: {failing}"


def test_literal_categories_are_vocabulary_members() -> None:
    """No producer may hand over a raw token, an HTTP code, or provider text.

    Only the OAuth sites pass literals (the probe endpoints pass an already
    derived variable, covered by ``clamp_failure_category``'s own tests), so
    this reads them straight out of the source.
    """
    tree = ast.parse(_OAUTH.read_text(encoding="utf-8"))
    literals = [
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword)
        and node.arg == "failure_category"
        and isinstance(node.value, ast.Constant)
    ]
    assert len(literals) == 8, f"expected eight OAuth failure categories, found {literals}"
    assert all(value in PROBE_FAILURE_VOCABULARY for value in literals), (
        f"a producer persists a non-vocabulary failure category: {literals}"
    )


def test_each_writer_helper_forwards_the_category() -> None:
    """The plumbing half: a helper must pass its parameter through to ``append``.

    ``_collect`` deliberately skips the helpers' own ``append`` calls, so
    without this a helper could accept ``failure_category`` and drop it on the
    floor while every producer test above still passed.
    """
    forwarding: dict[str, bool] = {}
    for path in (_SECRETS_V2, _OAUTH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if func.name not in _WRITER_NAMES:
                continue
            accepts = any(
                arg.arg == "failure_category" for arg in (*func.args.args, *func.args.kwonlyargs)
            )
            forwards = any(
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "append"
                and any(
                    kw.arg == "failure_category"
                    and isinstance(kw.value, ast.Name)
                    and kw.value.id == "failure_category"
                    for kw in node.keywords
                )
                for node in ast.walk(func)
            )
            forwarding[func.name] = accepts and forwards

    assert forwarding == {
        "_write_credential_audit": True,
        "_write_system_audit": True,
        "_write_cli_audit": True,
        "_emit_oauth_audit": True,
    }, f"a credential-audit writer helper does not forward its category: {forwarding}"
