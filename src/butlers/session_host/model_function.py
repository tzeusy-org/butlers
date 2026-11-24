"""Closed D18 OpenCode settings and result validation, independent of goal authority.

Recovered from the historical bu-kiuh8v preset against the current settings
contract. This is not a launcher or an eligibility proof. A D18 host must supply
invocation-local sealed files, no ambient configuration, and the existing narrow
model credential. It must not stage a goal's MCP file or bearer. No current
control operation, goal profile or deterministic caller selects this preset.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from .contracts import Refused
from .opencode import CONFIG_PATH, CREDENTIAL_PATH, OpenCodeSettings, api_key_text


@dataclass(frozen=True)
class OpenCodeModelFunctionSettings:
    """One tool-less OpenAI API-key preset for the pinned OpenCode version.

    Separate from ``OpenCodeSettings``: a model function has no broker binding,
    and its subject cannot reuse a goal configuration's conformance evidence.
    Provider options, agents, tools and runtime arguments are not caller inputs.
    """

    model: str

    def __post_init__(self) -> None:
        OpenCodeSettings(self.model)  # same bounded, explicit OpenAI model family

    @property
    def command(self) -> tuple[str, ...]:
        return (
            "/runtime/opencode",
            "run",
            "--format",
            "json",
            "--agent",
            "model_function",
            # Otherwise the CLI starts a second, implicit title-model call.
            "--title",
            "Model function",
            "--model",
            self.model,
        )

    @property
    def environment(self) -> tuple[tuple[str, str], ...]:
        # Only invocation-local file pointers; no broker, goal token or ambient HOME.
        return OpenCodeSettings(self.model).environment

    def _bytes(self) -> bytes:
        config = {
            "model": self.model,
            "default_agent": "model_function",
            "permission": {"*": "deny"},
            "tools": {"*": False},
            "mcp": {},
            "agent": {
                "model_function": {
                    "mode": "primary",
                    "steps": 1,
                    "permission": {"*": "deny"},
                    "tools": {"*": False},
                }
            },
            "provider": {"openai": {"options": {"apiKey": "{file:" + CREDENTIAL_PATH + "}"}}},
            "enabled_providers": ["openai"],
            "instructions": [],
            "plugin": [],
            "share": "disabled",
            "autoupdate": False,
            "snapshot": False,
            "compaction": {"auto": False},
        }
        return json.dumps(config, separators=(",", ":"), ensure_ascii=True).encode()

    def render(self, credential: bytes) -> bytes:
        """Validate the staged API-key format without embedding it in configuration."""
        api_key_text(credential, refusal="unsupported_opencode_credential")
        return self._bytes()

    @property
    def identity(self) -> dict:
        """Exact separate conformance subject; it confers no admission authority."""
        return {
            "version": 1,
            "profile": "model-function-v1",
            "cli": "opencode-ai@1.17.7",
            "auth_mode": "openai-api-key",
            "template_utf8": self._bytes().decode(),
            "dynamic_slots": [],
            "readonly_file": CONFIG_PATH,
            "credential_file": CREDENTIAL_PATH,
            "environment": self.environment,
            "stdin": "independent-readonly-sealed:/invocation/prompt@offset0",
            "command": self.command,
        }


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _finite(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError
    return number


def _constant(_value):
    raise ValueError


def _json(value):
    return json.loads(
        value, object_pairs_hook=_unique, parse_float=_finite, parse_constant=_constant
    )


def structured_result(output: bytes) -> dict:
    """Accept a bounded single-step JSON-object answer, refusing tool/error events.

    The deterministic caller still validates its own typed output schema. This
    check cannot undo a tool effect and is not evidence that tools were disabled;
    actual native/provider tool-inventory conformance remains independently required.
    Refusals contain no runtime output, prompt or credential bytes.
    """
    try:
        if type(output) is not bytes or not 0 < len(output) <= 1024 * 1024:
            raise ValueError
        text = []
        text_bytes = 0
        state = "initial"
        for line in output.splitlines():
            if not 0 < len(line) <= 128 * 1024:
                raise ValueError
            event = _json(line)
            if type(event) is not dict or type(event.get("part")) is not dict:
                raise ValueError
            part = event["part"]
            if event.get("type") == "step_start" and part.get("type") == "step-start":
                if state != "initial":
                    raise ValueError
                state = "text"
            elif event.get("type") == "text" and part.get("type") == "text":
                # The pinned CLI can publish its completed text part after the
                # step-finish event. Still require exactly one start/finish pair.
                if state not in ("text", "finished") or type(part.get("text")) is not str:
                    raise ValueError
                text_bytes += len(part["text"].encode())
                if text_bytes > 65536:
                    raise ValueError
                text.append(part["text"])
            elif event.get("type") == "step_finish" and part.get("type") == "step-finish":
                if state != "text" or part.get("reason") != "stop":
                    raise ValueError
                state = "finished"
            else:
                raise ValueError
        if state != "finished":
            raise ValueError
        result = _json("".join(text))
        if type(result) is not dict:
            raise ValueError
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Refused("invalid_model_function_output") from None
