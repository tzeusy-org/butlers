"""Keep synchronous core guards out of session-scoped asyncio marking."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TARGETS = (
    "tests/core/test_core_state.py",
    "tests/core/test_core_sessions.py",
    "tests/core/test_core_scheduler.py",
)


def _assignment_value(statement: ast.stmt, name: str) -> ast.expr | None:
    if isinstance(statement, ast.Assign):
        if any(isinstance(target, ast.Name) and target.id == name for target in statement.targets):
            return statement.value
    elif isinstance(statement, ast.AnnAssign):
        if isinstance(statement.target, ast.Name) and statement.target.id == name:
            return statement.value
    return None


def _assigned_values(tree: ast.Module, name: str) -> list[ast.expr]:
    return [
        value
        for statement in tree.body
        if (value := _assignment_value(statement, name)) is not None
    ]


def _assigned_value(tree: ast.Module, name: str, relative_path: str) -> ast.expr:
    values = _assigned_values(tree, name)
    assert len(values) == 1, (relative_path, name)
    return values[0]


def _is_pytest_mark(node: ast.expr, name: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == name
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


def _is_asyncio_decorator(
    decorator: ast.expr,
    asyncio_aliases: set[str] | frozenset[str] = frozenset({"_asyncio_session"}),
) -> bool:
    marker = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (isinstance(marker, ast.Name) and marker.id in asyncio_aliases) or _is_pytest_mark(
        marker, "asyncio"
    )


def _asyncio_alias_names(tree: ast.Module) -> set[str]:
    aliases = {"_asyncio_session"}
    changed = True
    while changed:
        changed = False
        for statement in tree.body:
            if isinstance(statement, ast.Assign):
                targets = statement.targets
                value = statement.value
            elif isinstance(statement, ast.AnnAssign):
                targets = (statement.target,)
                value = statement.value
            else:
                continue
            if value is None or not _is_asyncio_decorator(value, aliases):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
    return aliases


def _is_session_asyncio_decorator(decorator: ast.expr, *, alias_defined: bool) -> bool:
    if isinstance(decorator, ast.Name):
        return decorator.id == "_asyncio_session" and alias_defined
    if not isinstance(decorator, ast.Call) or not _is_pytest_mark(decorator.func, "asyncio"):
        return False
    if decorator.args or len(decorator.keywords) != 1:
        return False
    loop_scope = decorator.keywords[0]
    return (
        loop_scope.arg == "loop_scope"
        and isinstance(loop_scope.value, ast.Constant)
        and loop_scope.value.value == "session"
    )


def _contains_asyncio_marker(node: ast.expr, asyncio_aliases: set[str]) -> bool:
    if _is_asyncio_decorator(node, asyncio_aliases):
        return True
    return isinstance(node, ast.List | ast.Tuple | ast.Set) and any(
        _contains_asyncio_marker(element, asyncio_aliases) for element in node.elts
    )


def _is_pytestmark_subscript(node: ast.expr) -> bool:
    return isinstance(node, ast.Subscript) and (
        (isinstance(node.value, ast.Name) and node.value.id == "pytestmark")
        or _is_pytestmark_subscript(node.value)
    )


def _is_pytestmark_mutation(statement: ast.stmt) -> bool:
    if isinstance(statement, ast.Assign):
        return any(_is_pytestmark_subscript(target) for target in statement.targets)
    if isinstance(statement, ast.AnnAssign):
        return _is_pytestmark_subscript(statement.target)
    if isinstance(statement, ast.AugAssign):
        return (
            isinstance(statement.target, ast.Name) and statement.target.id == "pytestmark"
        ) or _is_pytestmark_subscript(statement.target)
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        return False
    callee = statement.value.func
    return (
        isinstance(callee, ast.Attribute)
        and isinstance(callee.value, ast.Name)
        and callee.value.id == "pytestmark"
    )


def _assert_static_module_pytestmark(tree: ast.Module, relative_path: str) -> None:
    for statement in tree.body:
        if _is_pytestmark_mutation(statement):
            raise AssertionError((relative_path, "module pytestmark mutation"))


def _assert_marker_topology(tree: ast.Module, relative_path: str) -> None:
    """Verify session-loop, integration, and Docker marker semantics structurally."""
    _assert_static_module_pytestmark(tree, relative_path)
    asyncio_aliases = _asyncio_alias_names(tree)
    module_marks = _assigned_value(tree, "pytestmark", relative_path)
    assert isinstance(module_marks, ast.List | ast.Tuple), (relative_path, "module pytestmark")
    assert not any(isinstance(node, ast.Starred) for node in ast.walk(module_marks)), (
        relative_path,
        "module pytestmark unpacking",
    )
    assert any(_is_pytest_mark(mark, "integration") for mark in module_marks.elts), (
        relative_path,
        "integration marker",
    )

    docker_skip = next(
        (
            mark
            for mark in module_marks.elts
            if isinstance(mark, ast.Call) and _is_pytest_mark(mark.func, "skipif")
        ),
        None,
    )
    assert docker_skip is not None, (relative_path, "Docker skip marker")
    assert len(docker_skip.args) == 1
    condition = docker_skip.args[0]
    assert (
        isinstance(condition, ast.UnaryOp)
        and isinstance(condition.op, ast.Not)
        and isinstance(condition.operand, ast.Name)
        and condition.operand.id == "docker_available"
    ), (relative_path, "Docker skip marker")
    assert not any(_contains_asyncio_marker(mark, asyncio_aliases) for mark in module_marks.elts), (
        relative_path,
        "module asyncio marker",
    )

    alias_values = _assigned_values(tree, "_asyncio_session")
    assert len(alias_values) <= 1, (relative_path, "session-loop alias")
    alias_defined = bool(alias_values)
    if alias_values:
        asyncio_marker = alias_values[0]
        assert isinstance(asyncio_marker, ast.Call) and _is_pytest_mark(
            asyncio_marker.func, "asyncio"
        ), (relative_path, "session-loop alias")
        assert not asyncio_marker.args
        assert len(asyncio_marker.keywords) == 1
        loop_scope = asyncio_marker.keywords[0]
        assert (
            loop_scope.arg == "loop_scope"
            and isinstance(loop_scope.value, ast.Constant)
            and loop_scope.value.value == "session"
        ), (relative_path, "session-loop alias")

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        class_pytestmarks = []
        for statement in node.body:
            value = _assignment_value(statement, "pytestmark")
            if value is not None:
                class_pytestmarks.append(value)
            if _is_pytestmark_mutation(statement):
                raise AssertionError((relative_path, node.name, "class pytestmark mutation"))
        assert not any(
            _contains_asyncio_marker(marker, asyncio_aliases)
            for marker in node.decorator_list + class_pytestmarks
        ), (relative_path, node.name, "asyncio marker on test class")

    tests = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    ]
    assert tests
    for test in tests:
        asyncio_markers = [
            decorator
            for decorator in test.decorator_list
            if _is_asyncio_decorator(decorator, asyncio_aliases)
        ]
        if isinstance(test, ast.AsyncFunctionDef):
            assert len(asyncio_markers) == 1 and _is_session_asyncio_decorator(
                asyncio_markers[0], alias_defined=alias_defined
            ), (relative_path, test.name, "exactly one session-loop marker")
        else:
            assert not asyncio_markers, (relative_path, test.name, "asyncio marker on sync test")


def test_session_asyncio_marker_is_explicit_on_async_core_tests_only() -> None:
    """Module marks must not warn on static guards that execute synchronously."""
    for relative_path in _TARGETS:
        tree = ast.parse((_REPO_ROOT / relative_path).read_text(encoding="utf-8"))
        _assert_marker_topology(tree, relative_path)


def test_marker_topology_rejects_alias_and_module_mark_regressions() -> None:
    """The guard must reject weakened loop or integration/Docker semantics."""
    relative_path = _TARGETS[0]
    source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")

    weakened_alias = source.replace(
        '_asyncio_session = pytest.mark.asyncio(loop_scope="session")',
        "_asyncio_session = pytest.mark.unit",
        1,
    )
    with pytest.raises(AssertionError, match="session-loop alias"):
        _assert_marker_topology(ast.parse(weakened_alias), relative_path)

    removed_module_marks = source.replace(
        "pytestmark = [\n"
        "    pytest.mark.integration,\n"
        '    pytest.mark.skipif(not docker_available, reason="Docker not available"),\n'
        "]",
        "pytestmark = []",
        1,
    )
    with pytest.raises(AssertionError, match="integration marker"):
        _assert_marker_topology(ast.parse(removed_module_marks), relative_path)

    class_asyncio_marker = source.replace(
        "class TestDecodeJsonb:",
        '@pytest.mark.asyncio(loop_scope="session")\nclass TestDecodeJsonb:',
        1,
    )
    with pytest.raises(AssertionError, match="asyncio marker on test class"):
        _assert_marker_topology(ast.parse(class_asyncio_marker), relative_path)


def test_marker_topology_rejects_direct_asyncio_marker_on_sync_test() -> None:
    """A direct asyncio decorator must not evade the synchronous-test guard."""
    relative_path = _TARGETS[1]
    source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
    direct_session_markers = source.replace(
        '_asyncio_session = pytest.mark.asyncio(loop_scope="session")\n',
        "",
        1,
    ).replace(
        "@_asyncio_session",
        '@pytest.mark.asyncio(loop_scope="session")',
    )
    _assert_marker_topology(ast.parse(direct_session_markers), relative_path)

    conflicting_markers = source.replace(
        "@_asyncio_session\nasync def test_session_create_and_get(pool):",
        '@pytest.mark.asyncio(loop_scope="function")\n'
        "@_asyncio_session\nasync def test_session_create_and_get(pool):",
        1,
    )
    with pytest.raises(AssertionError, match="session-loop marker"):
        _assert_marker_topology(ast.parse(conflicting_markers), relative_path)

    direct_sync_marker = source.replace(
        "def test_no_delete_or_truncate_in_sessions_module():",
        '@pytest.mark.asyncio(loop_scope="session")\n'
        "def test_no_delete_or_truncate_in_sessions_module():",
        1,
    )

    with pytest.raises(AssertionError, match="asyncio marker on sync test"):
        _assert_marker_topology(ast.parse(direct_sync_marker), relative_path)


def test_marker_topology_rejects_module_asyncio_alias() -> None:
    """A module-level asyncio alias must not mark synchronous tests implicitly."""
    relative_path = _TARGETS[0]
    source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
    module_alias = source.replace(
        'pytest.mark.skipif(not docker_available, reason="Docker not available"),\n]',
        'pytest.mark.skipif(not docker_available, reason="Docker not available"),\n'
        "    _asyncio_session,\n"
        "]",
        1,
    )

    with pytest.raises(AssertionError, match="module asyncio marker"):
        _assert_marker_topology(ast.parse(module_alias), relative_path)

    indirect_module_alias = source.replace(
        '_asyncio_session = pytest.mark.asyncio(loop_scope="session")',
        '_asyncio_session = pytest.mark.asyncio(loop_scope="session")\n'
        "module_asyncio = _asyncio_session",
        1,
    ).replace(
        'pytest.mark.skipif(not docker_available, reason="Docker not available"),\n]',
        'pytest.mark.skipif(not docker_available, reason="Docker not available"),\n'
        "    module_asyncio,\n"
        "]",
        1,
    )
    with pytest.raises(AssertionError, match="module asyncio marker"):
        _assert_marker_topology(ast.parse(indirect_module_alias), relative_path)

    annotated_module_alias = source.replace(
        '_asyncio_session = pytest.mark.asyncio(loop_scope="session")',
        '_asyncio_session = pytest.mark.asyncio(loop_scope="session")\n'
        "module_asyncio: pytest.MarkDecorator = _asyncio_session",
        1,
    ).replace(
        'pytest.mark.skipif(not docker_available, reason="Docker not available"),\n]',
        'pytest.mark.skipif(not docker_available, reason="Docker not available"),\n'
        "    module_asyncio,\n"
        "]",
        1,
    )
    with pytest.raises(AssertionError, match="module asyncio marker"):
        _assert_marker_topology(ast.parse(annotated_module_alias), relative_path)

    augmented_module_marks = source.replace(
        '_asyncio_session = pytest.mark.asyncio(loop_scope="session")',
        '_asyncio_session = pytest.mark.asyncio(loop_scope="session")\n'
        "pytestmark += [_asyncio_session]",
        1,
    )
    with pytest.raises(AssertionError, match="module pytestmark mutation"):
        _assert_marker_topology(ast.parse(augmented_module_marks), relative_path)


@pytest.mark.parametrize(
    "assignment",
    (
        "pytestmark = [pytest.mark.integration, "
        'pytest.mark.skipif(not docker_available, reason="Docker not available"), '
        "_asyncio_session]",
        "pytestmark: list = [pytest.mark.integration, "
        'pytest.mark.skipif(not docker_available, reason="Docker not available"), '
        "_asyncio_session]",
    ),
    ids=("direct-assignment", "annotated-assignment"),
)
def test_marker_topology_rejects_module_pytestmark_assignments(assignment: str) -> None:
    """Static module assignments must not leak asyncio to synchronous tests."""
    relative_path = _TARGETS[0]
    source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
    original_assignment = (
        "pytestmark = [\n"
        "    pytest.mark.integration,\n"
        '    pytest.mark.skipif(not docker_available, reason="Docker not available"),\n'
        "]"
    )
    assigned_marks = source.replace(original_assignment, assignment, 1)

    with pytest.raises(AssertionError, match="module asyncio marker"):
        _assert_marker_topology(ast.parse(assigned_marks), relative_path)


@pytest.mark.parametrize(
    "mutation",
    (
        "pytestmark += [_asyncio_session]",
        "pytestmark.append(_asyncio_session)",
        "pytestmark[0] = _asyncio_session",
        "pytestmark[0:0] = [_asyncio_session]",
    ),
    ids=("augmented-assignment", "append-mutation", "index-assignment", "slice-assignment"),
)
def test_marker_topology_rejects_module_pytestmark_mutations(mutation: str) -> None:
    """Runtime writes must not mutate module marks after static inspection."""
    relative_path = _TARGETS[0]
    source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
    mutated_marks = source.replace(
        '_asyncio_session = pytest.mark.asyncio(loop_scope="session")',
        '_asyncio_session = pytest.mark.asyncio(loop_scope="session")\n' + mutation,
        1,
    )

    with pytest.raises(AssertionError, match="module pytestmark mutation"):
        _assert_marker_topology(ast.parse(mutated_marks), relative_path)


@pytest.mark.parametrize(
    ("class_mark", "error_message"),
    (
        ("pytestmark = [_asyncio_session]", "asyncio marker on test class"),
        ("pytestmark: list = [_asyncio_session]", "asyncio marker on test class"),
        (
            "pytestmark = [pytest.mark.unit]\n    pytestmark += [_asyncio_session]",
            "class pytestmark mutation",
        ),
        ("pytestmark.append(_asyncio_session)", "class pytestmark mutation"),
        ("pytestmark[0] = _asyncio_session", "class pytestmark mutation"),
        ("pytestmark[0:0] = [_asyncio_session]", "class pytestmark mutation"),
    ),
    ids=(
        "direct-assignment",
        "annotated-assignment",
        "augmented-assignment",
        "append-mutation",
        "index-assignment",
        "slice-assignment",
    ),
)
def test_marker_topology_rejects_class_pytestmark_mutations(
    class_mark: str, error_message: str
) -> None:
    """Class-body marks must not implicitly make synchronous methods async."""
    relative_path = _TARGETS[0]
    source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
    class_marks = source.replace(
        "class TestDecodeJsonb:",
        f"class TestDecodeJsonb:\n    {class_mark}",
        1,
    )
    with pytest.raises(AssertionError, match=error_message):
        _assert_marker_topology(ast.parse(class_marks), relative_path)
