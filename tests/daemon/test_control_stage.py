"""Key-absent Switchboard startup and its refusal-only network boundary."""

from __future__ import annotations

import ast
import asyncio
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp import FastMCP

from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


async def test_key_absent_session_listener_refuses_while_service_keeps_serving():
    from butlers.control import SessionRefusalListener

    listener = SessionRefusalListener(host="127.0.0.1", port=0)
    service = ButlerDaemon._build_mcp_http_app(FastMCP("switchboard"), butler_name="switchboard")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=service), base_url="http://service"
    ) as current:
        await listener.start()
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{listener.port}") as session:
                health = await session.get("/health")
                assert health.status_code == 503
                assert health.content == b'{"status":"unavailable"}'
                for method, path in (
                    ("GET", "/"),
                    ("POST", "/health"),
                    ("HEAD", "/health"),
                    ("POST", "/mcp"),
                    ("GET", "/sse"),
                    ("POST", "/goal"),
                    ("GET", "/internal/control-plane/identity"),
                ):
                    response = await session.request(method, path)
                    assert response.status_code == 404
                    assert not response.is_redirect
                assert (await current.get("/health")).json() == {"status": "ok"}
                assert (await current.get("/health")).status_code == 200
        finally:
            await listener.close()
        assert listener.task.done()
        # A stopped refusal server must not leave a listener or task behind.
        with pytest.raises(httpx.ConnectError):
            async with httpx.AsyncClient(timeout=1) as client:
                await client.get(f"http://127.0.0.1:{listener.port}/health")


async def test_key_absent_startup_refuses_private_configuration_without_reading_values(monkeypatch):
    from butlers.control import (
        KEY_ABSENT_STAGE,
        StageConfigurationError,
        check_private_key_absent,
        check_stored_private_key_absent,
    )

    class NamesOnly(Mapping):
        def __init__(self, names):
            self.names = names

        def __iter__(self):
            return iter(self.names)

        def __len__(self):
            return len(self.names)

        def __getitem__(self, key):
            raise AssertionError("private configuration values must never be read")

    for key in (
        "GOAL_BROKER_SIGNING_KEY",
        "GOAL_BROKER_SIGNING_KEY_FILE",
        "GOAL_BROKER_SIGNING_KEY_PATH",
    ):
        with pytest.raises(StageConfigurationError, match="private-key configuration is forbidden"):
            check_private_key_absent(KEY_ABSENT_STAGE, environment=NamesOnly([key]))
    with pytest.raises(StageConfigurationError, match="unsupported control profile"):
        check_private_key_absent("ISOLATED_CANDIDATE", environment=NamesOnly([]))
    # Presence only: no material, file fixture, file opening or decoding.
    monkeypatch.setattr(Path, "iterdir", lambda self: iter([Path("GOAL_BROKER_SIGNING_KEY")]))
    with pytest.raises(StageConfigurationError, match="private-key configuration is forbidden"):
        check_private_key_absent(KEY_ABSENT_STAGE, environment=NamesOnly([]))
    monkeypatch.setattr(Path, "iterdir", lambda self: iter([]))
    check_private_key_absent(KEY_ABSENT_STAGE, environment=NamesOnly([]))

    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=[True, True])
    with pytest.raises(StageConfigurationError, match="private-key configuration is forbidden"):
        await check_stored_private_key_absent(pool)
    assert all("secret_value" not in call.args[0] for call in pool.fetchval.call_args_list)
    assert "secret_key" in pool.fetchval.call_args_list[-1].args[0]
    pool.fetchval = AsyncMock(side_effect=RuntimeError("metadata unavailable"))
    with pytest.raises(StageConfigurationError, match="cannot verify private-key absence"):
        await check_stored_private_key_absent(pool)

    # Exercise the public control command's actual pre-start boundary.
    from click.testing import CliRunner

    from butlers.cli import cli

    monkeypatch.setenv("GOAL_BROKER_SIGNING_KEY", "")
    start = AsyncMock()
    monkeypatch.setattr(ButlerDaemon, "start", start)
    result = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        ["control", "--profile", KEY_ABSENT_STAGE, "--config", "roster/switchboard"],
    )
    assert result.exit_code != 0
    assert "private-key configuration is forbidden" in str(result.exception)
    start.assert_not_awaited()


def test_key_absent_stage_source_has_no_private_material_resolution_or_mount():
    root = Path(__file__).resolve().parents[2]
    stage = ast.parse((root / "src/butlers/control.py").read_text())
    # The stage is deliberately incapable of obtaining a signer: no credential
    # resolver, cryptography import, file read, or environment-value lookup.
    forbidden = {
        "read",
        "read_text",
        "read_bytes",
        "open",
        "getenv",
        "resolve",
        "load",
        "load_secret",
    }
    for node in ast.walk(stage):
        if isinstance(node, (ast.ImportFrom, ast.Import)):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
            )
            assert not any(
                word in name for name in names for word in ("credential", "crypto", "signer")
            )
        if isinstance(node, ast.Subscript):
            assert not (isinstance(node.value, ast.Attribute) and node.value.attr == "environ")
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            assert name not in forbidden
            assert not (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "environ"
            )
    import yaml

    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    assert "butlers-control" in compose["services"]
    for name, service in compose["services"].items():
        assert "goal_broker_signing_key" not in yaml.safe_dump(service).lower(), name
    assert (
        "goal_broker_signing_key"
        not in (root / "docker-compose.control-stage.yml").read_text().lower()
    )
    tracked = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=root, timeout=10)
        .decode()
        .split("\0")
    )
    # Tracked sources only: never traverse an operator's ignored local values.
    sources = tracked + ["deploy/helm/butlers/templates/butlers-control.yaml"]
    for name in sources:
        assert "GOAL_BROKER_SIGNING_KEY" not in name
        if name.startswith("deploy/helm/butlers/") and name.endswith(".yaml"):
            assert "goal_broker_signing_key" not in (root / name).read_text().lower(), name

    _assert_tracked_key_absence(root)


# This is a lexical source ratchet, not a Python interpreter or a call graph.
# Unknown generic keys remain unknown; known key names cannot escape through an
# opaque call. Imports are resolved from tracked syntax, never executed.
_KEY_NAME = re.compile(r"GOAL_BROKER_SIGNING_KEY(?:_[A-Z0-9_]+)?", re.IGNORECASE)
_METADATA_QUERY = re.compile(
    r"SELECT EXISTS \(SELECT 1 FROM (?:switchboard|public)\.butler_secrets "
    r"WHERE starts_with\(UPPER\(secret_key\), \$1::text\)\)",
    re.IGNORECASE,
)


class _KeyNameScope:
    def __init__(self, parent=None):
        self.parent = parent
        self.bindings = {}

    def lookup(self, name):
        if name in self.bindings:
            return self.bindings[name], self
        return self.parent.lookup(name) if self.parent else ([], self)


def _assert_tracked_key_absence(root):
    try:
        tracked = (
            subprocess.check_output(
                ["git", "ls-files", "-z"], cwd=root, timeout=10, stderr=subprocess.PIPE
            )
            .decode()
            .split("\0")
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise AssertionError("private-key scan unavailable tracked inventory") from None
    trees, modules, node_scopes, default_scopes = {}, {}, {}, {}
    for name in filter(None, tracked):
        assert not _KEY_NAME.search(name), f"{name}: private-key tracked path"
        selected = name.startswith("src/") and name.endswith(".py")
        chart = name.startswith("deploy/helm/butlers/") and name.endswith((".yaml", ".yml", ".tpl"))
        if not (selected or chart):
            continue
        path = root / name
        # Do not follow a tracked symlink (including an ancestor) to ignored or
        # operator-owned content. Read only ordinary selected tracked files.
        assert not any(p.is_symlink() for p in (path, *path.parents) if p != root.parent), (
            f"{name}: private-key scan symlink"
        )
        try:
            text = path.read_text()
        except (OSError, UnicodeError):
            raise AssertionError(f"{name}: private-key scan unreadable") from None
        if chart:
            assert not _KEY_NAME.search(text), f"{name}: private-key chart reference"
        else:
            try:
                trees[name] = ast.parse(text)
            except SyntaxError:
                raise AssertionError(f"{name}: private-key scan invalid Python") from None
            module = name[4:-3].replace("/", ".").removesuffix(".__init__")
            modules[module] = _KeyNameScope()

    def sequence(node, scope, seen=frozenset()):
        if isinstance(node, (ast.Tuple, ast.List)):
            return node.elts
        if isinstance(node, ast.Name):
            definitions, owner = scope.lookup(node.id)
            marker = (id(owner), node.id)
            if marker not in seen:
                return [
                    item
                    for value in definitions
                    if isinstance(value, ast.AST)
                    for item in sequence(value, owner, seen | {marker})
                ]
        return []

    def bind(target, value, scope):
        if isinstance(target, ast.Name):
            scope.bindings.setdefault(target.id, []).append(value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            # Bind only the recoverable lexical prefix; opaque values may yield no items.
            for child, item in zip(target.elts, sequence(value, scope), strict=False):
                bind(child, item, scope)

    def collect(node, scope, module, package):
        node_scopes[node] = scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            if hasattr(node, "name"):
                scope.bindings.setdefault(node.name, [])
            local = _KeyNameScope(scope)
            if hasattr(node, "args"):
                args = node.args
                positional = [*args.posonlyargs, *args.args]
                for arg in [*positional, *args.kwonlyargs, args.vararg, args.kwarg]:
                    if arg is not None:
                        local.bindings[arg.arg] = []
                # Defaults are evaluated in the defining scope, before the
                # parameter shadows an identically named enclosing constant.
                # No defaults means [-0:] keeps positional args but maps no pairs.
                pairs = list(zip(positional[-len(args.defaults) :], args.defaults, strict=False))
                pairs.extend(zip(args.kwonlyargs, args.kw_defaults, strict=True))
                for arg, default in pairs:
                    if default is not None:
                        local.bindings[arg.arg] = [default]
                        default_scopes[default] = scope
            for child in ast.iter_child_nodes(node):
                if child is getattr(node, "args", None):
                    node_scopes[child] = local
                    for argument in ast.iter_child_nodes(child):
                        collect(argument, default_scopes.get(argument, local), module, package)
                else:
                    collect(child, local, module, package)
            return
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if node.value is not None:
                    bind(target, node.value, scope)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if isinstance(node, ast.Import):
                    scope.bindings[alias.asname or alias.name.split(".")[0]] = [
                        (alias.name if alias.asname else alias.name.split(".")[0], "")
                    ]
                else:
                    prefix = package.split(".")
                    parent = ".".join(prefix[: len(prefix) - node.level + 1]) if node.level else ""
                    imported = ".".join(filter(None, (parent, node.module)))
                    scope.bindings[alias.asname or alias.name] = [(imported, alias.name)]
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            rows = sequence(node.iter, scope)
            if isinstance(node.target, (ast.Tuple, ast.List)):
                for index, target in enumerate(node.target.elts):
                    column = [
                        row.elts[index]
                        for row in rows
                        if isinstance(row, (ast.Tuple, ast.List)) and len(row.elts) > index
                    ]
                    bind(target, ast.Tuple(elts=column), scope)
            else:
                bind(node.target, ast.Tuple(elts=rows), scope)
        for child in ast.iter_child_nodes(node):
            collect(child, scope, module, package)

    # All modules exist before import aliases are followed, including cycles.
    for name, tree in trees.items():
        module = name[4:-3].replace("/", ".").removesuffix(".__init__")
        package = module if name.endswith("/__init__.py") else module.rpartition(".")[0]
        collect(tree, modules[module], module, package)

    def imported_name(node, scope):
        if isinstance(node, ast.Name):
            definitions, _ = scope.lookup(node.id)
            return [item for item in definitions if isinstance(item, tuple)]
        if isinstance(node, ast.Attribute):
            return [
                (".".join(filter(None, (mod, symbol))), node.attr)
                for mod, symbol in imported_name(node.value, scope)
            ]
        return []

    def combine(left, right, separator=""):
        if len(left) * len(right) > 64:
            # Do not expand unrelated generated SQL into an interpreter. A
            # known key or key-name fragment cannot be discarded at this bound.
            fragments = left | right
            assert not any(
                _KEY_NAME.search(part) or (part and part.upper() in "GOAL_BROKER_SIGNING_KEY")
                for part in fragments
            ), "private-key scan constant branch bound"
            return set()  # unsupported unrelated expression, not key taint
        return {a + separator + b for a in left for b in right}

    def values(node, scope, seen=frozenset()):
        marker = (id(node), id(scope))
        if marker in seen:
            return set()
        seen = seen | {marker}
        if isinstance(node, ast.Constant):
            return {node.value} if isinstance(node.value, str) else set()
        if isinstance(node, ast.Name):
            definitions, owner = scope.lookup(node.id)
            binding = (id(owner), node.id)
            if binding in seen:
                return set()
            seen = seen | {binding}
            result = set()
            for definition in definitions:
                if isinstance(definition, ast.AST):
                    result.update(values(definition, default_scopes.get(definition, owner), seen))
                else:
                    module, symbol = definition
                    if symbol and module in modules:
                        result.update(values(ast.Name(id=symbol), modules[module], seen))
            return result
        if isinstance(node, ast.Attribute):
            result = set()
            for module, symbol in imported_name(node, scope):
                if module in modules:
                    result.update(values(ast.Name(id=symbol), modules[module], seen))
            return result
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
            left, right = values(node.left, scope, seen), values(node.right, scope, seen)
            # Keep known fragments even if the other operand is dynamic.
            if left and right:
                return combine(left, right, "/" if isinstance(node.op, ast.Div) else "")
            return left | right
        if isinstance(node, ast.Call):
            method = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            path_builder = any(
                module == "pathlib" and symbol in {"Path", "PurePath", "PosixPath"}
                for module, symbol in imported_name(node.func, scope)
            )
            if path_builder or method == "str":
                return set().union(*(values(arg, scope, seen) for arg in node.args))
            if method == "joinpath" and isinstance(node.func, ast.Attribute):
                return values(node.func.value, scope, seen) | set().union(
                    *(values(arg, scope, seen) for arg in node.args)
                )
            if isinstance(node.func, ast.Attribute) and method in {"upper", "lower", "resolve"}:
                return values(node.func.value, scope, seen)
            return set()
        if isinstance(node, ast.FormattedValue):
            return values(node.value, scope, seen)
        if isinstance(node, ast.JoinedStr):
            result = {""}
            for child in node.values:
                part = values(child, scope, seen)
                if part:
                    result = combine(result, part)
            return result
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return set().union(
                *(values(child, scope, seen) for child in ast.iter_child_nodes(node))
            )
        return set().union(*(values(child, scope, seen) for child in ast.iter_child_nodes(node)))

    for name, tree in trees.items():
        for node in ast.walk(tree):
            scope = node_scopes[node]
            if isinstance(node, ast.Subscript) and any(
                _KEY_NAME.search(s) for s in values(node.slice, scope)
            ):
                raise AssertionError(f"{name}:{node.lineno}: private-key value subscript")
            if (
                isinstance(node, ast.Return)
                and node.value is not None
                and any(_KEY_NAME.search(value) for value in values(node.value, scope))
            ):
                raise AssertionError(f"{name}:{node.lineno}: private-key opaque name escape")
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(
                    not isinstance(target, (ast.Name, ast.Tuple, ast.List)) for target in targets
                ) and any(_KEY_NAME.search(value) for value in values(node.value, scope)):
                    raise AssertionError(f"{name}:{node.lineno}: private-key opaque name binding")
            if not isinstance(node, ast.Call):
                continue
            arguments = [*node.args, *(arg.value for arg in node.keywords)]
            receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
            tainted_args = any(_KEY_NAME.search(s) for arg in arguments for s in values(arg, scope))
            tainted_receiver = receiver is not None and any(
                _KEY_NAME.search(s) for s in values(receiver, scope)
            )
            if not (tainted_args or tainted_receiver):
                continue
            method = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            # Name transforms/comparisons and path construction are not reads.
            path_builder = any(
                module == "pathlib" and symbol in {"Path", "PurePath", "PosixPath"}
                for module, symbol in imported_name(node.func, scope)
            )
            builtin_string = (
                isinstance(node.func, ast.Name)
                and node.func.id == "str"
                and "str" not in scope.lookup("str")[1].bindings
            )
            name_operation = isinstance(node.func, ast.Attribute) and method in {
                "startswith",
                "endswith",
                "upper",
                "lower",
                "joinpath",
            }
            if path_builder or builtin_string or name_operation:
                continue
            if method == "resolve" and tainted_receiver and not arguments:
                continue  # canonicalizing a labelled path does not read material
            query = values(node.args[0], scope) if node.args else set()
            if method == "fetchval" and query and all(_METADATA_QUERY.fullmatch(q) for q in query):
                continue  # exact existing SELECT EXISTS(secret_key) metadata
            raise AssertionError(f"{name}:{node.lineno}: private-key value resolution")


def test_key_absent_guard_rejects_tracked_resolution_mutations(tmp_path, monkeypatch):
    # A single species exercises the SAME helper as the real source consumer.
    # Fixture code is parsed, never imported or executed; no key material exists.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=10)
    target = tmp_path / "src/sample.py"
    target.parent.mkdir()
    key_ids = target.with_name("key_ids.py")
    key_ids.write_text('KEY = "GOAL_BROKER_" + "SIGNING_KEY"\n')
    target.with_name("reexport.py").write_text("from key_ids import KEY as exported\n")
    target.with_name("cycle_a.py").write_text("from cycle_b import KEY\n")
    target.with_name("cycle_b.py").write_text("from cycle_a import KEY\n")
    control = tmp_path / "src/butlers/control.py"
    control.parent.mkdir()
    original_control = (Path(__file__).resolve().parents[2] / "src/butlers/control.py").read_text()
    control.write_text(original_control)
    chart = tmp_path / "deploy/helm/butlers/templates/_helpers.tpl"
    chart.parent.mkdir(parents=True)
    chart.write_text("{{/* harmless helper */}}\n")
    target.write_text("# restored harmless source\n")
    (tmp_path / "docker-compose.yml").write_text("services:\n  butlers-control: {}\n")
    (tmp_path / "docker-compose.control-stage.yml").write_text("# no private material\n")
    (chart.parent / "butlers-control.yaml").write_text("# no private material\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, timeout=10)
    _assert_tracked_key_absence(tmp_path)

    unsafe = (
        'def load(store, key="GOAL_BROKER_SIGNING_KEY"):\n return store.resolve(key)\nload(store)\n',
        'KEY = "GOAL_BROKER_SIGNING_KEY"\ndef load(store, key=KEY):\n return store.resolve(key)\nload(store)\n',
        'def load(store, *, key="GOAL_BROKER_SIGNING_KEY"):\n return store.resolve(key)\nload(store)\n',
        'KEY = "GOAL_BROKER_SIGNING_KEY"\ndef load(store, *, key=KEY):\n return store.resolve(key)\nload(store)\n',
        'KEY = "GOAL_BROKER_SIGNING_KEY"\ndef load(store, KEY=KEY, /):\n return store.resolve(KEY)\nload(store)\n',
        'KEY = "GOAL_BROKER_SIGNING_KEY"\nload = lambda store, key=KEY: store.resolve(key)\nload(store)\n',
        'async def load(store):\n return await store.resolve("GOAL_BROKER_SIGNING_KEY")\n',
        'RENAMED = "GOAL_BROKER_" + "SIGNING_KEY_FILE"\nALIAS = RENAMED\nstore.load(ALIAS)\n',
        "from key_ids import KEY as renamed\nstore.resolve(renamed)\n",
        "import key_ids as config\nstore.load_shared(config.KEY)\n",
        "from reexport import exported\nstore.resolve(exported)\n",
        'from os import getenv as read\nread("GOAL_BROKER_SIGNING_KEY")\n',
        'import os\nos.environ["GOAL_BROKER_SIGNING_KEY"]\n',
        'from pathlib import Path\np = Path("/run/secrets") / "GOAL_BROKER_SIGNING_KEY"\np.read_bytes()\n',
        'open("/run/secrets/GOAL_BROKER_SIGNING_KEY_PATH")\n',
        'from pathlib import Path as P\nP("/run/secrets/GOAL_BROKER_SIGNING_KEY").read_bytes()\n',
        'from pathlib import Path\np = Path("GOAL_BROKER_SIGNING_KEY")\np.joinpath("suffix").read_text()\n',
        'prefix = "BROKER_"\nKEY = f"GOAL_{prefix}SIGNING_KEY"\nopaque(KEY)\n',
        'KEY = "GOAL_BROKER_SIGNING_KEY"\nstore.magic(KEY)\n',
        'KEY = "GOAL_BROKER_SIGNING_KEY"\nstore.resolve(**{"key": KEY})\n',
        'KEY = "GOAL_BROKER_SIGNING_KEY"\nobj.key = KEY\n',
        'KEY = "GOAL_BROKER_SIGNING_KEY"\ndef opaque():\n return KEY\n',
        'KEY = "GOAL_BROKER_SIGNING_KEY"\nPath = store.resolve\nPath(KEY)\n',
        'KEY = "GOAL_BROKER_SIGNING_KEY"\nq = "SELECT secret_value FROM public.butler_secrets WHERE secret_key=$1"\ndb.fetchval(q, KEY)\n',
    )
    # Exercise the actual existing consumer, not merely a helper in isolation.
    # Redirect only its source-root locator to this harmless tracked repository.
    target.write_text(unsafe[0])
    with monkeypatch.context() as fixture_root:
        fixture_root.setitem(globals(), "__file__", str(tmp_path / "tests/daemon/owner.py"))
        with pytest.raises(AssertionError, match="private-key"):
            test_key_absent_stage_source_has_no_private_material_resolution_or_mount()
    for source in unsafe:
        target.write_text(source)
        with pytest.raises(AssertionError, match="private-key"):
            _assert_tracked_key_absence(tmp_path)
    target.write_text("# exact harmless restoration\n")
    _assert_tracked_key_absence(tmp_path)

    safe = (
        "def load(store, key=dynamic_key):\n return store.resolve(key)\nload(store)\n",
        "def load(store, *, key=dynamic_key):\n return store.resolve(key)\nload(store)\n",
        'KEY = "UNRELATED"\ndef load(store, key=KEY):\n return store.resolve(key)\nload(store)\n',
        '"""GOAL_BROKER_SIGNING_KEY documentation only."""\n',
        'KEY = "GOAL_BROKER_SIGNING_KEY"\nKEY in environment\nname.upper().startswith(KEY)\n',
        'KEY = "GOAL_BROKER_SIGNING_KEY"\ndef load(KEY):\n return store.resolve(KEY)\n',
        'KEY = "GOAL_BROKER_SIGNING_KEY"\ndef load():\n KEY = "UNRELATED"\n return store.resolve(KEY)\n',
        "async def load(store, dynamic_key):\n return await store.resolve(dynamic_key)\n",
        "from pathlib import Path\ndef file_signer(trusted_path):\n return Path(trusted_path).read_bytes()\n",
        'from pathlib import Path as P\nP("/run/secrets/GOAL_BROKER_SIGNING_KEY").resolve()\n',
        "from cycle_a import KEY\nstore.resolve(KEY)\n",
        'KEY = "GOAL_BROKER_SIGNING_KEY"\nq = "SELECT EXISTS (SELECT 1 FROM public.butler_secrets WHERE starts_with(UPPER(secret_key), $1::text))"\ndb.fetchval(q, KEY)\n',
    )
    for source in safe:
        target.write_text(source)
        _assert_tracked_key_absence(tmp_path)
    target.write_text("# exact harmless restoration\n")

    # No whole-control exception: changing its exact metadata query to a value
    # query must refuse even though all ordinary stage-name checks still exist.
    control.write_text(
        original_control.replace(
            "SELECT 1 FROM public.butler_secrets", "SELECT secret_value FROM public.butler_secrets"
        )
    )
    with pytest.raises(AssertionError, match="private-key"):
        _assert_tracked_key_absence(tmp_path)
    control.write_text(original_control)
    for suffix in (".tpl", ".yml", ".yaml"):
        helper = chart.with_suffix(suffix)
        helper.write_text("# GOAL_BROKER_SIGNING_KEY_FILE mount reference\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, timeout=10)
        with pytest.raises(AssertionError, match="private-key chart"):
            _assert_tracked_key_absence(tmp_path)
        helper.write_text("# restored harmless helper\n")
        _assert_tracked_key_absence(tmp_path)

    # Untracked/ignored material is outside this tracked-source ratchet.
    untracked = target.with_name("untracked.py")
    untracked.write_text(unsafe[0])
    _assert_tracked_key_absence(tmp_path)
    untracked.unlink()
    target.write_text("def invalid Python")
    with pytest.raises(AssertionError, match="invalid Python"):
        _assert_tracked_key_absence(tmp_path)
    target.unlink()
    with pytest.raises(AssertionError, match="unreadable"):
        _assert_tracked_key_absence(tmp_path)
    target.symlink_to(key_ids)
    with pytest.raises(AssertionError, match="symlink"):
        _assert_tracked_key_absence(tmp_path)
    target.unlink()
    target.write_text("# exact harmless restoration\n")
    with monkeypatch.context() as failure:

        def unavailable(*args, **kwargs):
            raise subprocess.TimeoutExpired("git", 10)

        failure.setattr(subprocess, "check_output", unavailable)
        with pytest.raises(AssertionError, match="unavailable tracked inventory"):
            _assert_tracked_key_absence(tmp_path)
    _assert_tracked_key_absence(tmp_path)
