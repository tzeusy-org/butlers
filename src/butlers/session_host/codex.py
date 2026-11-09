"""One closed Codex 0.159.2 goal configuration, not runtime eligibility.

A config file containing the right keys proves nothing. What the settings below
enforce was measured against the real binary by removing each one; see
``tests/session_host/test_codex_adapter_proof.py``. Settings split three ways:

* Enforcing: removing it alone puts a forbidden tool in front of the model or
  breaks the broker. The native mutation tests hold these.
* Layered: shell has two independent switches. Neither alone opens it, both do.
* Unmeasured: removing it changes nothing this proof can see for this tuple and
  API-key auth. Kept as defense in depth, and claiming nothing. Some may guard
  outbound calls, which an empty network namespace cannot distinguish.

No permission-bypass flag is ever passed. The private model catalog is the only
way found to remove ``apply_patch``: pinned 0.159.2 registers its handler from
model metadata whatever the feature flags say. It also takes the tuple off the
``code_mode_only`` and Responses-lite paths, where the wire ``tools`` array is
empty while the real tool list travels inside ``input``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .codex_oauth import oauth_account
from .contracts import BrokerBinding, Refused
from .opencode import api_key_text

_HOME = "/tmp/home"
CODEX_HOME = _HOME + "/.codex"
CONFIG_NAME = "codex-config.toml"
CATALOG_NAME = "codex-catalog.json"
AUTH_NAME = "codex-auth.json"
CATALOG_PATH = CODEX_HOME + "/catalog.json"
NATIVE_FILES = (
    (CONFIG_NAME, CODEX_HOME + "/config.toml"),
    (CATALOG_NAME, CATALOG_PATH),
    (AUTH_NAME, CODEX_HOME + "/auth.json"),
)
_ENVIRONMENT = (
    ("PATH", "/runtime"),
    ("HOME", _HOME),
    ("CODEX_HOME", CODEX_HOME),
)
# --strict-config turns a misspelled or retired deny key into a launch failure.
# Without it Codex ignores unknown keys, and a deny key could silently do nothing.
_COMMAND = (
    "/runtime/codex",
    "exec",
    "--json",
    "--strict-config",
    "--skip-git-repo-check",
    "--ephemeral",
    "-",
)
_DISABLED_FEATURES = (
    # Enforcing.
    "multi_agent",
    "goals",
    "view_image",
    # Layered with the catalog's shell_type.
    "shell_tool",
    "unified_exec",
    # Unmeasured.
    "apps",
    "plugins",
    "image_generation",
    "skill_search",
    "sleep_tool",
    "tool_suggest",
    "hooks",
    "memories",
    "browser_use",
    "computer_use",
)


API_KEY = "api_key"
OAUTH = "oauth"
AUTH_PATH = CODEX_HOME + "/auth.json"


@dataclass(frozen=True)
class CodexSettings:
    """Server-selected preset; no arbitrary options, paths, providers or env.

    ``auth`` selects the credential mode, by trusted construction only:

    * ``api_key`` (default): the staged credential is an OpenAI API key, and the
      auth file is a sealed read-only copy.
    * ``oauth``: the staged credential is the session host's Codex device-auth
      document (``codex_oauth``). Its auth file is the one writable native file, so
      the runtime can persist a refreshed token; the host validates and collects
      it after settlement. Its identity differs, so it needs its own native proof.
    """

    model: str
    auth: str = API_KEY

    def __post_init__(self) -> None:
        if (
            type(self.model) is not str
            or re.fullmatch(r"gpt-[A-Za-z0-9][A-Za-z0-9._-]{0,99}", self.model) is None
            or type(self.auth) is not str
            or self.auth not in (API_KEY, OAUTH)
        ):
            raise Refused("unsupported_codex_settings")

    @property
    def writable_auth(self) -> bool:
        """Only OAuth mode stages a runtime-writable auth file."""
        return self.auth == OAUTH

    @property
    def command(self) -> tuple[str, ...]:
        return _COMMAND

    @property
    def environment(self) -> tuple[tuple[str, str], ...]:
        return _ENVIRONMENT

    def _config(self, endpoint: str, token: str) -> bytes:
        # json.dumps yields a valid TOML basic string for this validated ASCII.
        lines = [
            # Enforcing.
            f"model_catalog_json = {json.dumps(CATALOG_PATH)}",
            'web_search = "disabled"',
            # Unmeasured, except sandbox_mode: the native proof shows the read-only
            # sandbox refusing a patch that a bypass flag would let through.
            f"model = {json.dumps(self.model)}",
            'approval_policy = "never"',
            'sandbox_mode = "read-only"',
            f"forced_login_method = {json.dumps('chatgpt' if self.writable_auth else 'api')}",
            'cli_auth_credentials_store = "file"',
            "check_for_update_on_startup = false",
            "",
            "[history]",
            'persistence = "none"',
            "",
            "[analytics]",
            "enabled = false",
            "",
            "[features]",
            *(f"{name} = false" for name in _DISABLED_FEATURES),
            "",
            "[tools]",
            # Enforcing.
            "experimental_request_user_input = { enabled = false }",
            "",
            "[skills]",
            "include_instructions = false",
            "",
            "[skills.bundled]",
            "enabled = false",
            "",
            "[mcp_servers.broker]",
            f"url = {json.dumps(endpoint)}",
            f"http_headers = {{ Authorization = {json.dumps('Bearer ' + token)} }}",
            # Without a bypass flag, approval_policy=never refuses every MCP call.
            # Approving the one broker server is not a bypass: the broker owns
            # grant checks, and this is the only server the invocation can name.
            'default_tools_approval_mode = "approve"',
            "enabled = true",
            # Enforcing: an unreachable broker fails the launch rather than leaving
            # a toolless session running.
            "required = true",
            "",
        ]
        return "\n".join(lines).encode()

    def _catalog(self) -> bytes:
        # Deliberately not the bundled entry. Each omitted field is enforcing:
        # apply_patch_tool_type, tool_mode, supports_search_tool,
        # multi_agent_version. So is the empty experimental_supported_tools. Empty
        # instructions: the runner supplies the governing prompt.
        entry = {
            "slug": self.model,
            "display_name": self.model,
            "description": "butlers closed goal profile",
            "default_reasoning_level": "low",
            "supported_reasoning_levels": [{"effort": "low", "description": "low"}],
            "shell_type": "disabled",
            "visibility": "list",
            "supported_in_api": True,
            "priority": 1,
            "base_instructions": "",
            "supports_reasoning_summaries": False,
            "support_verbosity": False,
            "truncation_policy": {"mode": "tokens", "limit": 10000},
            "supports_parallel_tool_calls": False,
            "context_window": 100000,
            "experimental_supported_tools": [],
        }
        return json.dumps({"models": [entry]}, separators=(",", ":"), sort_keys=True).encode()

    def render(self, binding: BrokerBinding, credential: bytes) -> dict[str, bytes]:
        """Return each staged file's bytes; the auth file is the staged credential.

        In OAuth mode ``credential`` is the device-auth document, staged byte for
        byte so its digest is the one the volume holds.
        """
        if type(binding) is not BrokerBinding:
            raise Refused("unsupported_codex_credential")
        if self.writable_auth:
            if type(credential) is not bytes or oauth_account(credential) is None:
                raise Refused("unsupported_codex_credential")
            auth = credential
        else:
            key = api_key_text(credential, refusal="unsupported_codex_credential")
            auth = json.dumps(
                {"auth_mode": "apikey", "OPENAI_API_KEY": key}, separators=(",", ":")
            ).encode()
        return {
            CONFIG_NAME: self._config(binding.endpoint, binding.token),
            CATALOG_NAME: self._catalog(),
            AUTH_NAME: auth,
        }

    @property
    def identity(self) -> dict:
        """Pin the exact bytes, leaving only the two broker slots dynamic.

        The API-key identity is unchanged byte for byte; OAuth adds its writable file.
        """
        identity = {
            "version": 1,
            "cli": "@openai/codex@0.159.2",
            "auth_mode": "openai-api-key",
            "config_utf8": self._config("$BROKER_ENDPOINT", "$BROKER_TOKEN").decode(),
            "catalog_utf8": self._catalog().decode(),
            "dynamic_slots": ["mcp_servers.broker.url", "mcp_servers.broker.http_headers bearer"],
            "native_files": NATIVE_FILES,
            "environment": self.environment,
            "stdin": "independent-readonly-sealed:/invocation/prompt@offset0",
            "command": self.command,
        }
        if self.writable_auth:
            identity["auth_mode"] = "chatgpt-device-auth"
            identity["writable_native_files"] = [AUTH_PATH]
        return identity
