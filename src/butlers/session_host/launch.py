"""Closed launch consumer with server-held adapter eligibility.

This module never reads ambient credentials or a domain/database pool. A trusted
control caller supplies one staged model authority; it cannot supply argv, env,
proofs, or the broker endpoint. Results remain untrusted model text.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import UUID


class Refused(ValueError):
    """Value-free closed refusal; never include request or credential content."""


_FEATURES = ("shell_tool", "unified_exec", "multi_agent", "goals", "view_image")
_SETTINGS = (
    'web_search="disabled"',
    "tools.experimental_request_user_input={enabled=false}",
)
_PROFILE = hashlib.sha256(
    json.dumps(
        {
            "runtime": "codex",
            "features_off": _FEATURES,
            "settings": _SETTINGS,
            "sandbox": "read-only",
            "approval": "never",
            "mcp": "broker-only",
            "filesystem_profile": "immutable-home-cache-tmp-v1",
        },
        sort_keys=True,
    ).encode()
).hexdigest()
_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_./:-]{0,199}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _bounded(value, maximum):
    try:
        if type(value) is not str or len(value.encode("utf-8")) > maximum or "\0" in value:
            raise ValueError
    except (ValueError, UnicodeError):
        raise Refused("invalid_launch") from None
    return value


@dataclass(frozen=True)
class LaunchRequest:
    invocation_id: UUID
    runtime: str
    model: str
    provider: str
    prompt: str = field(repr=False)
    system_prompt: str = field(repr=False)
    credential: bytes = field(repr=False)
    broker_token: str = field(repr=False)
    timeout_s: float

    def __post_init__(self):
        if type(self.invocation_id) is not UUID:
            raise Refused("invalid_launch")
        for value in (self.runtime, self.model, self.provider):
            if type(value) is not str or not _NAME.fullmatch(value):
                raise Refused("invalid_launch")
        _bounded(self.prompt, 128 * 1024)
        _bounded(self.system_prompt, 32 * 1024)
        _bounded(self.broker_token, 8192)
        if not self.broker_token or any(ord(c) < 32 for c in self.broker_token):
            raise Refused("invalid_launch")
        if (
            type(self.timeout_s) not in (int, float)
            or not math.isfinite(self.timeout_s)
            or not 0 < self.timeout_s <= 300
        ):
            raise Refused("invalid_launch")
        try:
            if type(self.credential) is not bytes or not 0 < len(self.credential) <= 65536:
                raise ValueError
            content = json.loads(self.credential)
            # OAuth staging will have its own exact registered credential
            # shape; arbitrary files/env maps never become model authority.
            if type(content) is not dict or set(content) != {"OPENAI_API_KEY"}:
                raise ValueError
            key = content["OPENAI_API_KEY"]
            if type(key) is not str or not key or any(ord(c) < 32 for c in key):
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise Refused("invalid_model_credential") from None

    def to_wire(self):
        return {
            "invocation_id": str(self.invocation_id),
            "runtime": self.runtime,
            "model": self.model,
            "provider": self.provider,
            "prompt": self.prompt,
            "system_prompt": self.system_prompt,
            "credential": base64.b64encode(self.credential).decode("ascii"),
            "broker_token": self.broker_token,
            "timeout_s": self.timeout_s,
        }

    @classmethod
    def from_wire(cls, value):
        expected = {
            "invocation_id",
            "runtime",
            "model",
            "provider",
            "prompt",
            "system_prompt",
            "credential",
            "broker_token",
            "timeout_s",
        }
        if type(value) is not dict or set(value) != expected:
            raise Refused("invalid_launch")
        try:
            values = dict(value)
            values["invocation_id"] = UUID(value["invocation_id"])
            if str(values["invocation_id"]) != value["invocation_id"]:
                raise ValueError
            values["credential"] = base64.b64decode(value["credential"], validate=True)
            return cls(**values)
        except (ValueError, TypeError, AttributeError):
            raise Refused("invalid_launch") from None


@dataclass(frozen=True)
class TupleProof:
    """Host-loaded evidence identity, never accepted in a launch request.

    Constructing this value in a software test is not conformance evidence. A
    production factory must load trusted immutable, independently checked
    artifact bytes; no eligible tuple or factory is enabled by default.
    """

    runtime: str
    model: str
    provider: str
    executable_sha256: str
    evidence_sha256: str
    profile_sha256: str = _PROFILE

    def __post_init__(self):
        for digest in (self.executable_sha256, self.evidence_sha256, self.profile_sha256):
            if type(digest) is not str or not _HASH.fullmatch(digest):
                raise Refused("runtime_tuple_unproven")


@dataclass(frozen=True)
class StagedFile:
    relative_path: Path
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class PreparedLaunch:
    command: tuple[str, ...] = field(repr=False)
    authorities: tuple[StagedFile, ...] = field(repr=False)
    runtime: str = "codex"


@dataclass(frozen=True)
class LaunchResult:
    text: str


class Sandbox(Protocol):
    """Return bytes only AFTER whole descendant-domain death is proven."""

    async def run(self, prepared: PreparedLaunch, *, timeout_s: float) -> bytes: ...


class SessionHost:
    """Consume already-authenticated control requests without caller authority.

    Required construction arguments make the missing platform/proof factory
    explicit. No direct-subprocess, inherited-env or unproven-adapter fallback.
    """

    def __init__(
        self, *, binary: Path, broker_url: str, proofs: tuple[TupleProof, ...], sandbox: Sandbox
    ):
        from urllib.parse import urlsplit

        target = urlsplit(broker_url)
        if (
            target.scheme not in ("http", "https")
            or not target.hostname
            or target.username
            or target.password
            or target.query
            or target.fragment
            or target.path != "/mcp"
        ):
            raise Refused("invalid_broker_endpoint")
        if not binary.is_absolute():
            raise Refused("runtime_tuple_unproven")
        self._binary, self._broker_url = binary, broker_url
        self._proofs, self._sandbox = tuple(proofs), sandbox

    def _prepare(self, request: LaunchRequest) -> PreparedLaunch:
        try:
            binary_digest = hashlib.sha256(self._binary.read_bytes()).hexdigest()
        except OSError:
            raise Refused("runtime_tuple_unproven") from None
        # This profile configures Codex's native OpenAI endpoint. A proof for
        # another provider must not silently select the default provider.
        if (
            request.runtime != "codex"
            or request.provider != "openai"
            or not any(
                type(proof) is TupleProof
                and proof.runtime == request.runtime
                and proof.model == request.model
                and proof.provider == request.provider
                and proof.executable_sha256 == binary_digest
                and proof.profile_sha256 == _PROFILE
                for proof in self._proofs
            )
        ):
            raise Refused("runtime_tuple_unproven")
        args = [str(self._binary), "--ask-for-approval", "never"]
        for feature in _FEATURES:
            args.extend(("--disable", feature))
        for setting in _SETTINGS:
            args.extend(("-c", setting))
        args.extend(
            (
                "exec",
                "--json",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--model",
                request.model,
                "--",
                request.prompt,
            )
        )
        config = (
            'model_instructions_file = "/home/runtime/system.md"\n'
            "[mcp_servers.broker]\n"
            f"url = {json.dumps(self._broker_url)}\n"
            "[mcp_servers.broker.http_headers]\n"
            f"Authorization = {json.dumps('Bearer ' + request.broker_token)}\n"
        )
        return PreparedLaunch(
            tuple(args),
            (
                StagedFile(Path(".codex/auth.json"), request.credential),
                StagedFile(Path(".codex/config.toml"), config.encode()),
                StagedFile(Path("system.md"), request.system_prompt.encode()),
            ),
        )

    async def launch(self, request: LaunchRequest) -> LaunchResult:
        if type(request) is not LaunchRequest:
            raise Refused("invalid_launch")
        prepared = self._prepare(request)
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
                item = event.get("item")
                if (
                    event.get("type") == "item.completed"
                    and type(item) is dict
                    and item.get("type") == "agent_message"
                ):
                    text.append(_bounded(item.get("text"), 65536))
        except (ValueError, TypeError, UnicodeError):
            raise Refused("invalid_runtime_output") from None
        return LaunchResult("".join(text))
