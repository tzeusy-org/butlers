"""Exact source-owned function identity check before trusted custody enrollment.

The fixed privileged prover separately validates owners, role hierarchy, ACLs
and hold RLS. Matching this source manifest is not SQL execution evidence or
proof that the remaining schema/producer/topology controls are implemented.
"""

from __future__ import annotations

import json
from importlib.resources import files

from butlers.core.custody_source import CustodyError


def verify_installed_functions(proof: dict) -> None:
    """Refuse missing/extra/drifted function bodies and ABIs before enrollment."""
    try:
        expected = json.loads(
            files("butlers.core").joinpath("custody-installed-interface.json").read_text()
        )
        actual = proof["function_manifest"]
        if type(actual) is not list or any(type(item) is not dict for item in actual):
            raise CustodyError("unavailable")
        signatures = [item["signature"] for item in actual]
        if any(type(signature) is not str for signature in signatures):
            raise CustodyError("unavailable")
        if (
            len(set(signatures)) != len(signatures)
            or type(proof["version"]) is not int
            or type(proof["functions"]) is not int
            or proof["functions"] != len(actual)
            or proof["version"] != expected["version"]
            or proof["core_revision"] != expected["core_revision"]
            or sorted(actual, key=lambda item: item["signature"]) != expected["functions"]
        ):
            raise CustodyError("unavailable")
    except (OSError, ValueError, KeyError, TypeError):
        raise CustodyError("unavailable") from None
