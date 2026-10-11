"""Recompute conservative executable source lineage; declarations are never evidence.

The ordinary identical-source protocol does not use this module. Paired proofs
bind two complete source universes, align unique semantic source points, and
execute independently generated mutations on each universe. No source point can
be supplied by an attestation, a line-number guess, or an unrelated survivor.
"""

from __future__ import annotations

import ast
import copy

from check_condensation_ledger import EvidenceError, canonical, fields, public_path


def request(value: dict) -> None:
    fields(value, {"mode", "scope", "functions"})
    if value["mode"] != "paired":
        raise EvidenceError("unsupported-source-lineage")
    scope = value["scope"]
    if (
        not isinstance(scope, list)
        or not scope
        or any(
            not isinstance(p, str) or not p.endswith(".py") or any(c in p for c in "*?[]")
            for p in scope
        )
        or len(set(scope)) != len(scope)
    ):
        raise EvidenceError("invalid-before-lineage-scope")
    for p in scope:
        public_path(p)
    if not isinstance(value["functions"], list):
        raise EvidenceError("invalid-function-lineage")
    old, new = set(), set()
    for row in value["functions"]:
        fields(row, {"before", "current"})
        for side in ("before", "current"):
            fields(row[side], {"path", "function"})
            public_path(row[side]["path"])
            if not isinstance(row[side]["function"], str) or not row[side]["function"]:
                raise EvidenceError("invalid-function-lineage")
        a, b = ((r["path"], r["function"]) for r in (row["before"], row["current"]))
        if a in old or b in new or a[0] not in scope:
            raise EvidenceError("ambiguous-function-lineage")
        old.add(a)
        new.add(b)


def _lexical_bindings(node: ast.AST) -> set[str]:
    """Declared names in this frame, excluding child lexical bodies/targets.

    This identifies a lexical home, not definite assignment or runtime health.
    Actual collection, execution, branches and mutation admission remain required.
    """
    known = set()

    class Bindings(ast.NodeVisitor):
        def visit_Name(self, item):
            if isinstance(item.ctx, (ast.Store, ast.Del)):
                known.add(item.id)

        def visit_alias(self, item):
            known.add(item.asname or item.name.split(".")[0])

        def visit_ExceptHandler(self, item):
            if item.name:
                known.add(item.name)
            self.generic_visit(item)

        def visit_FunctionDef(self, item):
            known.add(item.name)

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Lambda(self, item):
            return

        visit_ListComp = visit_Lambda
        visit_SetComp = visit_Lambda
        visit_DictComp = visit_Lambda
        visit_GeneratorExp = visit_Lambda

        def visit_Global(self, item):
            # A declaration without an independently visible source binding
            # cannot manufacture a closed lexical home.
            return

        visit_Nonlocal = visit_Global

    visitor = Bindings()
    for statement in node.body:
        visitor.visit(statement)
    return known


def _closed_comprehension(node: ast.AST, names: dict[str, str], known: set[str]) -> None:
    """Retain literal captures; never alpha-rename a comprehension frame."""
    if _namespace_observed(node) or any(
        isinstance(n, (ast.NamedExpr, ast.Lambda, ast.Await, ast.Yield, ast.YieldFrom))
        or (
            n is not node
            and isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
        )
        for n in ast.walk(node)
    ):
        raise EvidenceError("unsupported-lexical-source-binding")
    bound = set(known) | set(names)
    local = set()
    for generator in node.generators:
        if generator.is_async:
            raise EvidenceError("unsupported-lexical-source-binding")
        targets = list(ast.walk(generator.target))
        if any(not isinstance(n, (ast.Name, ast.Tuple, ast.List, ast.Store)) for n in targets):
            raise EvidenceError("unsupported-lexical-source-binding")
        declared = {n.id for n in targets if isinstance(n, ast.Name)}
        # Shadowing or repeated targets need their own equivalence protocol.
        if declared & (bound | local):
            raise EvidenceError("unsupported-lexical-source-binding")
        if any(
            isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in bound | local
            for n in ast.walk(generator.iter)
        ):
            raise EvidenceError("unsupported-lexical-source-binding")
        local |= declared
        for condition in generator.ifs:
            if any(
                isinstance(n, ast.Name)
                and isinstance(n.ctx, ast.Load)
                and n.id not in bound | local
                for n in ast.walk(condition)
            ):
                raise EvidenceError("unsupported-lexical-source-binding")
    values = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
    if any(
        isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in bound | local
        for value in values
        for n in ast.walk(value)
    ):
        raise EvidenceError("unsupported-lexical-source-binding")


def _normalized(node: ast.AST, names: dict[str, str], known: set[str] | None = None) -> str:
    class Parameters(ast.NodeTransformer):
        def visit_Name(self, node):
            return ast.copy_location(ast.Name(id=names.get(node.id, node.id), ctx=node.ctx), node)

        def visit_alias(self, node):
            binding = node.asname or node.name.split(".")[0]
            if binding in names:
                node.asname = names[binding]
            return node

        def visit_ExceptHandler(self, node):
            node.name = names.get(node.name, node.name)
            return self.generic_visit(node)

        def visit_MatchAs(self, node):
            node.name = names.get(node.name, node.name)
            return self.generic_visit(node)

        visit_MatchStar = visit_MatchAs

        def visit_MatchMapping(self, node):
            node.rest = names.get(node.rest, node.rest)
            return self.generic_visit(node)

        def visit_Lambda(self, node):
            # A new lexical scope may shadow or capture these bindings. This
            # protocol has no independent alpha-equivalence proof for it.
            if any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node)):
                raise EvidenceError("unsupported-lexical-source-binding")
            return node

        def visit_ListComp(self, node):
            if any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node)):
                _closed_comprehension(node, names, known or set())
            # Even supported captures retain their literal lexical identity.
            # This does not grant alpha-equivalence to operators or callables.
            return node

        visit_SetComp = visit_ListComp
        visit_DictComp = visit_ListComp
        visit_GeneratorExp = visit_ListComp

        def visit_FunctionDef(self, node):
            # Nested definitions are visited under their own parameter scope by
            # source_points, never rewritten using an outer parameter map.
            return node

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

    return ast.dump(Parameters().visit(copy.deepcopy(node)), include_attributes=False)


def _namespace_observed(node):
    """A referenced observer can be aliased before its eventual invocation.

    Preserve lexical spellings throughout its module rather than guessing which
    function a local/imported/global alias or frame observer will inspect.
    """
    observers = {
        "locals",
        "vars",
        "globals",
        "eval",
        "exec",
        "_getframe",
        "currentframe",
        "f_locals",
        "f_globals",
        "co_varnames",
    }
    return any(
        (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in observers)
        or (isinstance(n, ast.Attribute) and n.attr in observers)
        or (isinstance(n, ast.alias) and n.name in observers)
        for n in ast.walk(node)
    )


def _transparent_binding_body(node, parameters):
    """Prove only linear loads, fresh local bindings and direct returns.

    Everything else is unproved: even syntax without an explicit Call may invoke
    user code through an operator, descriptor, index, coercion or control protocol.
    This is a positive supported grammar, not a callable/type/name exemption.
    """
    known = set(parameters)

    def value(item):
        return (
            item is None
            or isinstance(item, ast.Constant)
            or (isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load) and item.id in known)
        )

    for statement in node.body:
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue
        if isinstance(statement, ast.Return) and value(statement.value):
            continue
        if isinstance(statement, ast.Assign) and value(statement.value):
            if not all(isinstance(target, ast.Name) for target in statement.targets):
                return False
            for target in statement.targets:
                # Releasing an existing reference can invoke user __del__ with
                # access to this frame, even when the new value is a literal.
                # Each target must be fresh, including within a chained bind.
                if target.id in known:
                    return False
                known.add(target.id)
            continue
        return False
    return True


def _function_signature(node, names, enclosing):
    arguments = copy.deepcopy(node.args)
    for arg in [
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
        *[n for n in (arguments.vararg, arguments.kwarg) if n is not None],
    ]:
        # Only the declarations belong to the new function's parameter scope.
        # Defaults, annotations and decorators resolve outside that scope;
        # type-parameter annotation scopes likewise are not its body bindings.
        arg.arg = names[arg.arg]
    return canonical(
        {
            "kind": type(node).__name__,
            "args": _normalized(arguments, {}),
            "keyword_names": [n.arg for n in (*node.args.args, *node.args.kwonlyargs)],
            "decorators": [_normalized(n, {}) for n in node.decorator_list],
            "returns": None if node.returns is None else _normalized(node.returns, {}),
            "type_parameters": [_normalized(n, {}) for n in getattr(node, "type_params", [])],
            # Closure/definition-time resolution is not proved by positional
            # alpha-renaming. Preserve the enclosing lexical parameter binding.
            "enclosing_parameters": enclosing,
            "observed_parameter_names": (list(names) if _namespace_observed(node) else []),
        }
    )


def source_points(text: str, path: str, aliases: dict[tuple, tuple]) -> dict:
    """Unique source operations, guarded nesting and positional parameter identity.

    Source coordinates select an actual AST point, never establish equivalence.
    Changed old operations/guards/defaults cannot be declared equivalent. A pure
    parameter rename is resolved by its actual positional/keyword binding.
    """
    tree = ast.parse(text)
    namespace_observed = _namespace_observed(tree)
    lines, spans, exits, signatures, bindings = {}, {}, {}, {}, {}

    def visit(node, owner, names, controls, known):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if isinstance(node, ast.Lambda):
                # A lambda has no stable declared owner; refuse if it supplies an arc.
                return
            owner = owner + "." + node.name if owner else node.name
            enclosing = names
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            args += [n for n in (node.args.vararg, node.args.kwarg) if n is not None]
            # These are comparison tokens, never executable Python names. A
            # global literally named parameter_0 must not alias a local slot.
            names = {
                n.arg: n.arg if namespace_observed else "$parameter_" + str(i)
                for i, n in enumerate(args)
            }
            known = known | _lexical_bindings(node) | set(names)
            identity = aliases.get((path, owner), (path, owner))
            bindings[identity] = {
                "names": list(names),
                "transparent": _transparent_binding_body(node, names),
            }
            signature = _function_signature(node, names, enclosing)
            signatures[identity] = signature
            exits[-node.lineno] = (identity, "exit", signature)
            controls = []
            marker = (identity, "entry", signature)
        elif isinstance(node, ast.ClassDef):
            owner = owner + "." + node.name if owner else node.name
            marker = (
                (path, owner),
                "class",
                canonical(
                    {
                        "bases": [_normalized(n, {}) for n in node.bases],
                        "keywords": [_normalized(n, {}) for n in node.keywords],
                        "decorators": [_normalized(n, {}) for n in node.decorator_list],
                        "enclosing_parameters": names,
                    }
                ),
            )
            names = {}
        else:
            identity = aliases.get((path, owner or "<module>"), (path, owner or "<module>"))
            if isinstance(node, (ast.If, ast.While)):
                operation = _normalized(node.test, names, known)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                operation = canonical(
                    [_normalized(node.target, names, known), _normalized(node.iter, names, known)]
                )
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                operation = canonical([_normalized(n, names, known) for n in node.items])
            elif isinstance(node, ast.Try):
                operation = canonical(
                    [_normalized(n.type, names, known) if n.type else None for n in node.handlers]
                )
            elif isinstance(node, ast.ExceptHandler):
                operation = canonical(
                    [_normalized(node.type, names, known) if node.type else None, node.name]
                )
            else:
                operation = _normalized(node, names, known)
            marker = (identity, type(node).__name__, canonical([controls, operation]))
        if isinstance(node, (ast.stmt, ast.ExceptHandler)):
            lines.setdefault(node.lineno, []).append(marker)
        if hasattr(node, "lineno"):
            spans[(node.lineno, node.col_offset)] = canonical(
                [
                    marker,
                    _normalized(node, names, known) if not isinstance(node, ast.stmt) else marker,
                ]
            )
        for field, children in ast.iter_fields(node):
            children = children if isinstance(children, list) else [children]
            for child in children:
                if not isinstance(child, ast.AST):
                    continue
                nested = controls
                if isinstance(
                    node,
                    (
                        ast.If,
                        ast.While,
                        ast.For,
                        ast.AsyncFor,
                        ast.With,
                        ast.AsyncWith,
                        ast.Try,
                        ast.ExceptHandler,
                    ),
                ):
                    nested = [*controls, (marker, field)]
                child_names = names
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and field != "body":
                    child_names = {}
                visit(child, owner, child_names, nested, known)

    # A module exit is stable only within its source-qualified module identity.
    exits[-1] = ((path, "<module>"), "exit", "module")
    known = _lexical_bindings(tree)
    for node in tree.body:
        visit(node, "", {}, [], known)
    unique = {line: canonical(markers[0]) for line, markers in lines.items() if len(markers) == 1}
    unique.update({line: canonical(marker) for line, marker in exits.items()})
    return {"lines": unique, "spans": spans, "signatures": signatures, "bindings": bindings}


def alignment(before: dict[str, str], current: dict[str, str], functions: list[dict]) -> dict:
    aliases = {
        (r["current"]["path"], r["current"]["function"]): (
            r["before"]["path"],
            r["before"]["function"],
        )
        for r in functions
    }
    points = {
        "removed": {p: source_points(t, p, {}) for p, t in before.items()},
        "survivors": {p: source_points(t, p, aliases) for p, t in current.items()},
    }
    for side in points.values():
        owners = [owner for profile in side.values() for owner in profile["signatures"]]
        if len(set(owners)) != len(owners):
            raise EvidenceError("ambiguous-source-function-identity")
    for row in functions:
        a, b = row["before"], row["current"]
        old = (
            points["removed"]
            .get(a["path"], {})
            .get("signatures", {})
            .get((a["path"], a["function"]))
        )
        new = (
            points["survivors"]
            .get(b["path"], {})
            .get("signatures", {})
            .get((a["path"], a["function"]))
        )
        if old is None or old != new:
            raise EvidenceError("changed-or-missing-function-lineage-signature")
    bindings = {
        side: {owner: body for p in profiles.values() for owner, body in p["bindings"].items()}
        for side, profiles in points.items()
    }
    for owner in bindings["removed"].keys() & bindings["survivors"].keys():
        old, new = (bindings[side][owner] for side in ("removed", "survivors"))
        if old["names"] != new["names"] and not (old["transparent"] and new["transparent"]):
            # A callable may inspect the caller's frame through an indirect or
            # imported alias. This protocol proves no such isolation; do not
            # invent a safe-call/name allowlist to authorize an alpha-renaming.
            raise EvidenceError("unsupported-indirect-namespace-binding")
    return points


def mutation_key(points: dict, row: dict) -> str:
    point = points.get(row["path"], {}).get("spans", {}).get((row["line"], row["column"]))
    if point is None:
        raise EvidenceError("unmapped-mutation-source-operation")
    return canonical([point, row["operator"]])


def paired_mutants(points: dict, before: list[dict], current: list[dict]) -> list[tuple]:
    old, new = {}, {}
    for rows, side, target in ((before, "removed", old), (current, "survivors", new)):
        for row in rows:
            key = mutation_key(points[side], row)
            if key in target:
                raise EvidenceError("ambiguous-paired-mutation")
            target[key] = row
    if not old or not set(old) <= set(new):
        raise EvidenceError("missing-paired-source-mutation")
    return [(old[k], new[k]) for k in sorted(old)]


def residue(before: dict, current: dict, mapping: dict, points: dict) -> list:
    """Per-owner branch subsumption through only newly introduced source points.

    The current literal carrier must execute every mapped endpoint. Additional
    operations may connect an old edge, but cannot skip or reorder another old
    operation. The per-owner graph contains only its named executing survivors.
    """

    def arcs(run, owners):
        return {
            tuple(a) for c in run["cases"] if c["node"] in owners for a in run["arcs"][c["key"]]
        }

    old_all = arcs(before, set(mapping))
    if not old_all or not arcs(current, set(current["selection"])):
        raise EvidenceError("empty-measured-branch-population")
    old_labels = {label for p in points["removed"].values() for label in p["lines"].values()}
    for side in points.values():
        labels = [label for profile in side.values() for label in profile["lines"].values()]
        if len(labels) != len(set(labels)):
            raise EvidenceError("ambiguous-branch-source-operation")
    lost = set()
    for owner, named in mapping.items():
        graph = {}
        for path, start, end in arcs(current, set(named)):
            line = points["survivors"].get(path, {}).get("lines", {})
            if start not in line or end not in line:
                raise EvidenceError("unmapped-current-branch-source-point")
            graph.setdefault(line[start], set()).add(line[end])
        for path, start, end in arcs(before, {owner}):
            line = points["removed"].get(path, {}).get("lines", {})
            if start not in line or end not in line:
                raise EvidenceError("unmapped-before-branch-source-point")
            a, b = line[start], line[end]
            todo, seen, found = list(graph.get(a, ())), set(), False
            while todo:
                point = todo.pop()
                if point == b:
                    found = True
                    break
                if point in seen or point in old_labels:
                    continue
                seen.add(point)
                todo.extend(graph.get(point, ()))
            if not found:
                lost.add((path, start, end))
    return [list(a) for a in sorted(lost)]


def generate_mutants(sources: dict[str, bytes]) -> list[dict]:
    # The one source-owned generator is stdlib-only and has no import-time IO.
    from condense_evidence import generate_mutants as generate

    return generate(sources)
