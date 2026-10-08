#!/usr/bin/env python3
"""Generate the fixed custody startup manifest from its owned bootstrap source.

This reads source only. It never connects to PostgreSQL, installs an interface,
imports project runtime code, or discovers an arbitrary schema. SQL execution
and actual installer/schema/role proof belong to the owning migrated controls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_OUTPUT = _ROOT / "src/butlers/core/custody-installed-interface.json"
_MARKER = "-- No role, LOGIN, membership, credential or privileged host service is added."
_FUNCTION = re.compile(
    r"CREATE OR REPLACE FUNCTION (custody_admission|public|dashboard_auth)\.([a-z_]+)"
    r"\((.*?)\)\s*RETURNS\s+(.+?)\s+LANGUAGE\s+(sql|plpgsql)(.*?)AS\s+(\$[a-z_]*\$)",
    re.S | re.I,
)
_TYPES = {"timestamptz": "timestamp with time zone"}


def build_manifest(source: str) -> dict:
    if source.count(_MARKER) != 1:
        raise ValueError("custody source marker must occur exactly once")
    source = source.split(_MARKER, 1)[1]
    functions = []
    for match in _FUNCTION.finditer(source):
        schema, name, arguments, returns, language, attributes, delimiter = match.groups()
        if schema != "custody_admission" and not name.startswith("custody_"):
            continue
        argument_names, types, defaults = [], [], []
        for argument in arguments.split(",") if arguments.strip() else []:
            definition = re.fullmatch(
                r"\s*([a-z_]+)\s+(jsonb|text\[\]|text|uuid|bigint|integer)(?:\s+DEFAULT\s+(.+))?\s*",
                argument,
                re.S,
            )
            if definition is None:
                raise ValueError("unsupported custody source argument")
            parameter_name, parameter_type, default = definition.groups()
            argument_names.append(parameter_name)
            types.append(parameter_type)
            if default is not None:
                default = " ".join(default.split())
                defaults.append("NULL::" + parameter_type if default == "NULL" else default)
        if not re.fullmatch(
            r"\s*(?:IMMUTABLE|STABLE|VOLATILE)?\s*(?:STRICT)?\s*(?:SECURITY DEFINER)?"
            r"\s*SET search_path\s*=\s*pg_catalog\s*,\s*pg_temp\s*",
            attributes,
            re.I,
        ):
            raise ValueError("unsupported custody function attributes")
        body_end = source.find(delimiter, match.end())
        if body_end == -1:
            raise ValueError("unterminated custody function body")
        body = source[match.end() : body_end]
        signature = f"{schema}.{name}({','.join(types)})"
        functions.append(
            {
                "signature": signature,
                "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "argument_names": argument_names,
                "return_type": _TYPES.get(returns.strip(), returns.strip()),
                "language": language.lower(),
                "security_definer": "SECURITY DEFINER" in attributes.upper(),
                "strict": "STRICT" in attributes.upper(),
                "leakproof": False,
                "parallel": "u",
                "returns_set": False,
                "argument_modes": None,
                "volatility": (
                    "i"
                    if "IMMUTABLE" in attributes.upper()
                    else "s"
                    if "STABLE" in attributes.upper()
                    else "v"
                ),
                "defaults": ", ".join(defaults) if defaults else None,
                "configuration": ["search_path=pg_catalog, pg_temp"],
            }
        )
    signatures = {function["signature"] for function in functions}
    if not functions or len(signatures) != len(functions):
        raise ValueError("missing or duplicate custody function signatures")
    return {
        "version": 1,
        "core_revision": "core_260",
        "functions": sorted(functions, key=lambda function: function["signature"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    manifest = build_manifest((_ROOT / "scripts/init-db.sql").read_text())
    encoded = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    if arguments.check:
        if not _OUTPUT.is_file() or _OUTPUT.read_text() != encoded:
            print("Custody source interface manifest is stale")
            return 1
        print("Custody source interface manifest is current")
        return 0
    _OUTPUT.write_text(encoded)
    print(f"Generated {len(manifest['functions'])} fixed custody function records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
