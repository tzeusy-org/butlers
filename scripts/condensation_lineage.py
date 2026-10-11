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


def _normalized(node: ast.AST, names: dict[str, str]) -> str:
    class Parameters(ast.NodeTransformer):
        def visit_Name(self, node):
            return ast.copy_location(ast.Name(id=names.get(node.id, node.id), ctx=node.ctx), node)

        def visit_arg(self, node):
            node.arg = names.get(node.arg, node.arg)
            return self.generic_visit(node)

    return ast.dump(Parameters().visit(copy.deepcopy(node)), include_attributes=False)


def source_points(text: str, path: str, aliases: dict[tuple, tuple]) -> dict:
    """Unique source operations, guarded nesting and positional parameter identity.

    Source coordinates select an actual AST point, never establish equivalence.
    Changed old operations/guards/defaults cannot be declared equivalent. A pure
    parameter rename is resolved by its actual positional/keyword binding.
    """
    tree = ast.parse(text)
    lines, spans, exits, signatures = {}, {}, {}, {}

    def visit(node, owner, names, controls):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if isinstance(node, ast.Lambda):
                # A lambda has no stable declared owner; refuse if it supplies an arc.
                return
            owner = owner + "." + node.name if owner else node.name
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            args += [n for n in (node.args.vararg, node.args.kwarg) if n is not None]
            names = {n.arg: "parameter_" + str(i) for i, n in enumerate(args)}
            identity = aliases.get((path, owner), (path, owner))
            signature = canonical(
                {
                    "kind": type(node).__name__,
                    "args": _normalized(node.args, names),
                    "keyword_names": [n.arg for n in (*node.args.args, *node.args.kwonlyargs)],
                    "decorators": [_normalized(n, names) for n in node.decorator_list],
                    "returns": None if node.returns is None else _normalized(node.returns, names),
                    "type_parameters": [
                        _normalized(n, names) for n in getattr(node, "type_params", [])
                    ],
                }
            )
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
                        "bases": [_normalized(n, names) for n in node.bases],
                        "keywords": [_normalized(n, names) for n in node.keywords],
                        "decorators": [_normalized(n, names) for n in node.decorator_list],
                    }
                ),
            )
        else:
            identity = aliases.get((path, owner or "<module>"), (path, owner or "<module>"))
            if isinstance(node, (ast.If, ast.While)):
                operation = _normalized(node.test, names)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                operation = canonical(
                    [_normalized(node.target, names), _normalized(node.iter, names)]
                )
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                operation = canonical([_normalized(n, names) for n in node.items])
            elif isinstance(node, ast.Try):
                operation = canonical(
                    [_normalized(n.type, names) if n.type else None for n in node.handlers]
                )
            elif isinstance(node, ast.ExceptHandler):
                operation = canonical(
                    [_normalized(node.type, names) if node.type else None, node.name]
                )
            else:
                operation = _normalized(node, names)
            marker = (identity, type(node).__name__, canonical([controls, operation]))
        if isinstance(node, (ast.stmt, ast.ExceptHandler)):
            lines.setdefault(node.lineno, []).append(marker)
        if hasattr(node, "lineno"):
            spans[(node.lineno, node.col_offset)] = canonical(
                [
                    marker,
                    _normalized(node, names) if not isinstance(node, ast.stmt) else marker,
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
                visit(child, owner, names, nested)

    # A module exit is stable only within its source-qualified module identity.
    exits[-1] = ((path, "<module>"), "exit", "module")
    for node in tree.body:
        visit(node, "", {}, [])
    unique = {line: canonical(markers[0]) for line, markers in lines.items() if len(markers) == 1}
    unique.update({line: canonical(marker) for line, marker in exits.items()})
    return {"lines": unique, "spans": spans, "signatures": signatures}


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
