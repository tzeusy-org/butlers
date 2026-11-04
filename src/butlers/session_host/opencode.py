"""One closed OpenCode1.17.7 goal configuration, not runtime eligibility proof.

Only a trusted profile selects this API-key mode. Token syntax establishes no
credential provenance or permission. Real parser, catalog and provider-wire
conformance remain external prerequisites for installing any eligible tuple.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .contracts import BrokerBinding, Refused

CONFIG_NAME = "opencode.jsonc"
CONFIG_PATH = "/invocation/" + CONFIG_NAME
CREDENTIAL_PATH = "/invocation/model-credential"
_ENVIRONMENT = (
    ("PATH", "/runtime"),
    ("HOME", "/tmp/home"),
    ("BUTLERS_MODEL_CREDENTIAL", CREDENTIAL_PATH),
    ("OPENCODE_CONFIG", CONFIG_PATH),
)


@dataclass(frozen=True)
class OpenCodeSettings:
    """Server-selected preset; no arbitrary options, paths, providers or env."""

    model: str

    def __post_init__(self) -> None:
        if (
            type(self.model) is not str
            or re.fullmatch(r"openai/[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", self.model) is None
        ):
            raise Refused("unsupported_opencode_settings")

    @property
    def command(self) -> tuple[str, ...]:
        return (
            "/runtime/opencode",
            "run",
            "--format",
            "json",
            "--agent",
            "build",
            "--model",
            self.model,
        )

    @property
    def environment(self) -> tuple[tuple[str, str], ...]:
        return _ENVIRONMENT

    def _config(self, endpoint: str, token: str) -> dict:
        # Insertion order is significant: the pinned permission implementation
        # uses the LAST matching rule. No ambient config is merged here.
        return {
            "model": self.model,
            "default_agent": "build",
            "permission": {"*": "deny", "broker_*": "allow"},
            "mcp": {
                "broker": {
                    "type": "remote",
                    "url": endpoint,
                    "enabled": True,
                    "oauth": False,
                    "headers": {"Authorization": "Bearer " + token},
                }
            },
            "provider": {"openai": {"options": {"apiKey": "{file:" + CREDENTIAL_PATH + "}"}}},
            "enabled_providers": ["openai"],
            "instructions": [],
            "plugin": [],
            "share": "disabled",
            "autoupdate": False,
        }

    @property
    def identity(self) -> dict:
        """Pin bytes and transport while naming exactly two dynamic slots.

        A reusable proof cannot pin an ephemeral invocation bearer. The renderer
        substitutes ONLY those two slots; model/provider/permissions stay fixed.
        This template identity is not evidence of actual CLI consumption.
        """
        return {
            "version": 1,
            "cli": "opencode-ai@1.17.7",
            "auth_mode": "openai-api-key",
            "template_utf8": self._bytes("$BROKER_ENDPOINT", "$BROKER_TOKEN").decode(),
            "dynamic_slots": ["mcp.broker.url", "mcp.broker.headers.Authorization bearer"],
            "readonly_file": CONFIG_PATH,
            "credential_file": CREDENTIAL_PATH,
            "environment": self.environment,
            "stdin": "independent-readonly-sealed:/invocation/prompt@offset0",
            "command": self.command,
        }

    def _bytes(self, endpoint: str, token: str) -> bytes:
        # One serializer for both proof-template identity and material bytes.
        return json.dumps(
            self._config(endpoint, token), separators=(",", ":"), ensure_ascii=True
        ).encode()

    def render(self, binding: BrokerBinding, credential: bytes) -> bytes:
        if type(binding) is not BrokerBinding or type(credential) is not bytes:
            raise Refused("unsupported_opencode_credential")
        try:
            text = credential.decode("utf-8")
        except UnicodeError:
            raise Refused("unsupported_opencode_credential") from None
        # This is only API-key text-format validation. The host must select the
        # correct credential mode; opaque token shape never attests authority.
        if (
            not 0 < len(credential) <= 65536
            or text != text.strip()
            or any(char in text for char in ("\0", "\r", "\n"))
            or text.startswith(("{", "["))
        ):
            raise Refused("unsupported_opencode_credential")
        return self._bytes(binding.endpoint, binding.token)
