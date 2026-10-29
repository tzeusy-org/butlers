"""Parse only the two consumed response contracts; never import or execute backend code."""

import ast
import json
import sys
from pathlib import Path

CONTRACTS = {
    "PermissionSetResponse": ("/{butler}/{perm}", "/api/permissions"),
    "SpendCeiling": ("/ceiling", "/api/spend"),
}
PRIMITIVES = {"str": "string", "datetime": "string", "float": "number", "bool": "boolean"}


def response_schema_base(source):
    """Admit only the shipped serialization-schema flag, never inherited fields."""
    tree = ast.parse(source)
    expected = ast.parse(
        "from pydantic import BaseModel, ConfigDict\n"
        "class ResponseSchemaModel(BaseModel):\n"
        "    model_config = ConfigDict(json_schema_serialization_defaults_required=True)\n"
    )
    # Documentation does not alter the exact executable class/import population.
    for owner in (tree, *(n for n in tree.body if isinstance(n, ast.ClassDef))):
        owner.body = [
            n
            for n in owner.body
            if not (
                isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)
            )
        ]
    if ast.dump(tree) != ast.dump(expected):
        raise ValueError("Unsupported serialization-only response base")


def project(source, route, model, base_source=None):
    if model not in CONTRACTS or route != CONTRACTS[model][0]:
        raise ValueError("Unsupported response contract")
    tree = ast.parse(source)
    nodes = list(ast.walk(tree))
    parents = {child: node for node in nodes for child in ast.iter_child_nodes(node)}

    def bindings(name):
        matches = []
        for node in nodes:
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
                bound = node.id
            elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                bound = node.name
            elif isinstance(node, ast.alias):
                bound = node.asname or node.name.split(".")[0]
            elif isinstance(node, ast.arg):
                bound = node.arg
            elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
                bound = node.name
            elif isinstance(node, ast.MatchMapping):
                bound = node.rest
            else:
                continue
            if bound == name:
                matches.append(node)
        return matches

    def imported(name, module):
        matches = bindings(name)
        if len(matches) != 1 or not isinstance(matches[0], ast.alias):
            raise ValueError(f"Unsupported shadowed authority: {name}")
        alias = matches[0]
        parent = parents[alias]
        if (
            not isinstance(parent, ast.ImportFrom)
            or parent not in tree.body
            or parent.module != module
            or parent.level != 0
            or alias.asname
        ):
            raise ValueError(f"Unsupported imported authority: {name}")
        return parent

    protected = {
        model,
        "BaseModel",
        "ResponseSchemaModel",
        "ApiResponse",
        "APIRouter",
        "router",
        *PRIMITIVES,
    }
    for node in nodes:
        if isinstance(node, ast.alias) and node.name == "*":
            raise ValueError("Unsupported wildcard authority")
        if isinstance(node, ast.Global | ast.Nonlocal) and protected.intersection(node.names):
            raise ValueError("Unsupported dynamic authority")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"exec", "eval", "globals", "locals", "vars", "setattr", "delattr"}:
                raise ValueError("Unsupported dynamic authority")
        if isinstance(node, ast.Attribute | ast.Subscript) and isinstance(
            node.ctx, ast.Store | ast.Del
        ):
            target = node
            while isinstance(target, ast.Attribute | ast.Subscript):
                target = target.value
            if isinstance(target, ast.Name) and target.id in protected:
                raise ValueError("Unsupported mutated authority")
    base_import = imported("BaseModel", "pydantic")
    response_import = imported("ApiResponse", "butlers.api.models")
    router_import = imported("APIRouter", "fastapi")
    for primitive in {"str", "float", "bool"}:
        if bindings(primitive):
            raise ValueError("Unsupported shadowed primitive")

    matches = bindings(model)
    if len(matches) != 1 or not isinstance(matches[0], ast.ClassDef):
        raise ValueError(f"Unsupported model declaration: {model}")
    cls = matches[0]
    if (
        cls not in tree.body
        or cls.decorator_list
        or cls.keywords
        or getattr(cls, "type_params", [])
    ):
        raise ValueError(f"Unsupported model declaration: {model}")
    if (
        len(cls.bases) != 1
        or not isinstance(cls.bases[0], ast.Name)
        or cls.bases[0].id not in {"BaseModel", "ResponseSchemaModel"}
    ):
        raise ValueError(f"Unsupported model inheritance: {model}")
    if cls.bases[0].id == "ResponseSchemaModel":
        base_import = imported("ResponseSchemaModel", "butlers.api.models.base")
        if base_source is None:
            base_source = (
                Path(__file__).resolve().parents[2] / "src/butlers/api/models/base.py"
            ).read_text()
        response_schema_base(base_source)
    if base_import.lineno >= cls.lineno:
        raise ValueError("Unsupported model authority ordering")
    fields = {}
    for member in cls.body:
        if (
            isinstance(member, ast.Expr)
            and isinstance(member.value, ast.Constant)
            and isinstance(member.value.value, str)
        ):
            continue
        if not isinstance(member, ast.AnnAssign) or not isinstance(member.target, ast.Name):
            raise ValueError(f"Unsupported model member: {model}")
        if member.value is not None or not isinstance(member.annotation, ast.Name):
            raise ValueError(f"Unsupported field/default/union in {model}")
        name, kind = member.target.id, member.annotation.id
        if not member.simple or name in fields or kind not in PRIMITIVES:
            raise ValueError(f"Unsupported field in {model}")
        fields[name] = PRIMITIVES[kind]
    if not fields:
        raise ValueError(f"Unsupported empty model: {model}")
    if "datetime" in (n.annotation.id for n in cls.body if isinstance(n, ast.AnnAssign)):
        if imported("datetime", "datetime").lineno >= cls.lineno:
            raise ValueError("Unsupported datetime authority ordering")

    routers = bindings("router")
    if len(routers) != 1 or not isinstance(routers[0], ast.Name):
        raise ValueError("Unsupported router binding")
    declaration = parents[routers[0]]
    if (
        not isinstance(declaration, ast.Assign)
        or declaration not in tree.body
        or len(declaration.targets) != 1
    ):
        raise ValueError("Unsupported router binding")
    if router_import.lineno >= declaration.lineno:
        raise ValueError("Unsupported router authority ordering")
    factory = declaration.value
    if (
        not isinstance(factory, ast.Call)
        or not isinstance(factory.func, ast.Name)
        or factory.func.id != "APIRouter"
    ):
        raise ValueError("Unsupported router binding")
    if factory.args or {k.arg for k in factory.keywords} != {"prefix", "tags"}:
        raise ValueError("Unsupported router binding")
    tags = next(k.value for k in factory.keywords if k.arg == "tags")
    if not isinstance(tags, ast.List) or not all(
        isinstance(v, ast.Constant) and isinstance(v.value, str) for v in tags.elts
    ):
        raise ValueError("Unsupported router binding")
    prefixes = [k.value for k in factory.keywords if k.arg == "prefix"]
    if (
        len(prefixes) != 1
        or not isinstance(prefixes[0], ast.Constant)
        or prefixes[0].value != CONTRACTS[model][1]
    ):
        raise ValueError("Unsupported receiver binding")
    routes = []
    for node in nodes:
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "router":
            continue
        owner = parents[node]
        if (
            owner not in tree.body
            or not isinstance(owner, ast.FunctionDef | ast.AsyncFunctionDef)
            or node not in owner.decorator_list
        ):
            raise ValueError("Unsupported executable route binding")
        if (
            not node.args
            or not isinstance(node.args[0], ast.Constant)
            or not isinstance(node.args[0].value, str)
        ):
            raise ValueError("Unsupported dynamic route binding")
        if (
            node.func.attr == "put"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == route
        ):
            routes.append((node, owner))
    if len(routes) != 1:
        raise ValueError("Missing unique executable response_model binding")
    call, owner = routes[0]
    if response_import.lineno >= owner.lineno or declaration.lineno >= owner.lineno:
        raise ValueError("Unsupported response authority ordering")
    expected = ast.parse(f"ApiResponse[{model}]", mode="eval").body
    if len(call.args) != 1 or len(call.keywords) != 1 or call.keywords[0].arg != "response_model":
        raise ValueError("Unsupported response_model binding")
    if (
        ast.dump(call.keywords[0].value) != ast.dump(expected)
        or len(owner.decorator_list) != 1
        or cls.lineno >= owner.lineno
    ):
        raise ValueError("Unsupported response_model binding")
    # Reject authority escapes instead of interpreting arbitrary Python aliases.
    for node in nodes:
        if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
            continue
        parent = parents[node]
        if node.id in {"BaseModel", "ResponseSchemaModel"}:
            if not isinstance(parent, ast.ClassDef) or node not in parent.bases:
                raise ValueError("Unsupported BaseModel alias or indirect authority")
        elif node.id == "APIRouter":
            if node is not factory.func:
                raise ValueError("Unsupported APIRouter alias or indirect authority")
        elif node.id == "ApiResponse":
            if not (
                isinstance(parent, ast.Subscript)
                and parent.value is node
                or isinstance(parent, ast.Call)
                and parent.func is node
            ):
                raise ValueError("Unsupported ApiResponse alias or indirect authority")
        elif node.id == "router":
            if not (
                isinstance(parent, ast.Attribute)
                and isinstance(parents[parent], ast.Call)
                and parents[parent].func is parent
            ):
                raise ValueError("Unsupported router alias or indirect authority")
    # Do not let the class escape through another alias or mutation helper.
    for node in nodes:
        if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load) or node.id != model:
            continue
        parent = parents[node]
        if isinstance(parent, ast.Call) and parent.func is node:
            continue
        if isinstance(parent, ast.Subscript) and parent.slice is node:
            enclosing = parents[parent]
            if parent is owner.returns or parent is call.keywords[0].value:
                continue
            if isinstance(enclosing, ast.Call) and enclosing.func is parent:
                continue
        raise ValueError("Unsupported model alias or indirect authority")
    return " ".join(f"{name}: {kind};" for name, kind in fields.items())


if __name__ == "__main__":
    try:
        request = json.load(sys.stdin)
        print(
            json.dumps(
                project(
                    request["source"],
                    request["route"],
                    request["model"],
                    request.get("base_source"),
                )
            )
        )
    except (ValueError, TypeError, KeyError, SyntaxError) as exc:
        print(f"Unsupported backend response contract: {exc}", file=sys.stderr)
        sys.exit(1)
