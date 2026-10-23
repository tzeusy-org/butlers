"""Host-side D18 tool-less profile; deterministic callers own their business contract.

No goal token, broker configuration, tools, subagents or local MCP enter this
profile. It is not an engineering-executor profile. Eligibility still requires
independent exact-runtime/model evidence; this module does not produce evidence
or retarget the separately owned D18 callers/relay.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from butlers.session_host.launch import (
    _NAME,
    LaunchResult,
    PreparedLaunch,
    Refused,
    StagedFile,
    TupleProof,
    _bounded,
)

_FUNCTION_CONFIG = {
    "permission": {"*": "deny"},
    "tools": {"*": False},
    "mcp": {},
    "plugin": [],
    "enabled_providers": ["openai"],
    "autoupdate": False,
    "share": "disabled",
    "snapshot": False,
    "default_agent": "model_function",
    "agent": {
        "model_function": {
            "mode": "primary",
            "steps": 1,
            "permission": {"*": "deny"},
            "tools": {"*": False},
        },
    },
    "instructions": ["/home/runtime/system.md"],
}
FUNCTION_PROFILE = hashlib.sha256(
    json.dumps(
        {
            "runtime": "opencode",
            "profile": "model-function",
            "config": _FUNCTION_CONFIG,
            "filesystem_profile": "immutable-home-cache-tmp-v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()
_PURPOSES = frozenset(
    {"connector_discretion", "catalog_verification", "transcription", "vision", "benchmark"}
)


@dataclass(frozen=True)
class ModelFunctionRequest:
    invocation_id: UUID
    purpose: str
    runtime: str
    model: str
    provider: str
    prompt: str = field(repr=False)
    system_prompt: str = field(repr=False)
    credential: bytes = field(repr=False)
    timeout_s: float

    def __post_init__(self):
        if (
            type(self.invocation_id) is not UUID
            or type(self.purpose) is not str
            or self.purpose not in _PURPOSES
            or self.runtime != "opencode"
            or self.provider != "openai"
            or type(self.model) is not str
            or not _NAME.fullmatch(self.model)
            or not self.model.startswith("openai/")
            or self.model == "openai/"
        ):
            raise Refused("invalid_model_function")
        _bounded(self.prompt, 128 * 1024)
        _bounded(self.system_prompt, 32 * 1024)
        if (
            type(self.timeout_s) not in (int, float)
            or not math.isfinite(self.timeout_s)
            or not 0 < self.timeout_s <= 60
        ):
            raise Refused("invalid_model_function")
        self.api_key()

    def api_key(self):
        try:
            if type(self.credential) is not bytes or not 0 < len(self.credential) <= 65536:
                raise ValueError
            from butlers.session_host.control import _unique_object

            value = json.loads(self.credential, object_pairs_hook=_unique_object)
            if type(value) is not dict or set(value) != {"OPENAI_API_KEY"}:
                raise ValueError
            key = value["OPENAI_API_KEY"]
            if type(key) is not str or not key or any(ord(c) < 32 for c in key):
                raise ValueError
            return key
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise Refused("invalid_model_credential") from None

    def to_wire(self):
        return {
            "type": "model_function",
            "invocation_id": str(self.invocation_id),
            "purpose": self.purpose,
            "runtime": self.runtime,
            "model": self.model,
            "provider": self.provider,
            "prompt": self.prompt,
            "system_prompt": self.system_prompt,
            "credential": base64.b64encode(self.credential).decode("ascii"),
            "timeout_s": self.timeout_s,
        }

    @classmethod
    def from_wire(cls, value):
        keys = {
            "type",
            "invocation_id",
            "purpose",
            "runtime",
            "model",
            "provider",
            "prompt",
            "system_prompt",
            "credential",
            "timeout_s",
        }
        if type(value) is not dict or set(value) != keys or value["type"] != "model_function":
            raise Refused("invalid_model_function")
        try:
            values = {k: v for k, v in value.items() if k != "type"}
            values["invocation_id"] = UUID(values["invocation_id"])
            if str(values["invocation_id"]) != value["invocation_id"]:
                raise ValueError
            values["credential"] = base64.b64decode(value["credential"], validate=True)
            return cls(**values)
        except (ValueError, TypeError, AttributeError):
            raise Refused("invalid_model_function") from None


class ModelFunctionHost:
    def __init__(self, *, binary: Path | None, proofs: tuple[TupleProof, ...], sandbox):
        self._binary, self._proofs, self._sandbox = binary, proofs, sandbox

    async def launch(self, request: ModelFunctionRequest) -> LaunchResult:
        if type(request) is not ModelFunctionRequest:
            raise Refused("invalid_model_function")
        try:
            if self._binary is None or not self._binary.is_absolute():
                raise ValueError
            digest = hashlib.sha256(self._binary.read_bytes()).hexdigest()
            if not any(
                type(p) is TupleProof
                and (p.runtime, p.model, p.provider, p.executable_sha256, p.profile_sha256)
                == (request.runtime, request.model, request.provider, digest, FUNCTION_PROFILE)
                for p in self._proofs
            ):
                raise ValueError
        except (ValueError, OSError):
            raise Refused("runtime_tuple_unproven") from None
        # The staged HOME has no other project/user config, plugin or MCP directory.
        config = {**_FUNCTION_CONFIG, "model": request.model}
        prepared = PreparedLaunch(
            (
                str(self._binary),
                "run",
                "--format",
                "json",
                "--agent",
                "model_function",
                "--model",
                request.model,
                "--",
                request.prompt,
            ),
            (
                StagedFile(
                    Path(".local/share/opencode/auth.json"),
                    json.dumps({"openai": {"type": "api", "key": request.api_key()}}).encode(),
                ),
                StagedFile(Path(".config/opencode/opencode.json"), json.dumps(config).encode()),
                StagedFile(Path("system.md"), request.system_prompt.encode()),
            ),
            runtime="opencode",
        )
        output = await self._sandbox.run(prepared, timeout_s=request.timeout_s)
        if type(output) is not bytes or len(output) > 1024 * 1024:
            raise Refused("invalid_runtime_output")
        text = []
        try:
            for line in output.splitlines():
                if len(line) > 65536:
                    raise ValueError
                event = json.loads(line)
                if type(event) is not dict:
                    raise ValueError
                part = event.get("part")
                if event.get("type") in {"tool_use", "tool_call"} or (
                    type(part) is dict and part.get("type") in {"tool_use", "tool_call"}
                ):
                    raise ValueError
                if event.get("type") == "text" and type(part) is dict:
                    text.append(_bounded(part.get("text"), 65536))
        except (ValueError, TypeError, UnicodeError):
            raise Refused("invalid_runtime_output") from None
        return LaunchResult("".join(text))
