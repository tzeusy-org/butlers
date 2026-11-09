"""Private exact-profile admission for a trusted host constructor.

This verifies the identity/integrity of independently reviewed conformance
artifacts; parsing an artifact does not execute or establish conformance.
No production tuple or proof is installed here. Control requests cannot supply
profiles, proof bytes, expected hashes, executable paths or adapter settings.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from .bubblewrap import BubblewrapDomain, ImageFile
from .codex import API_KEY, CodexSettings
from .codex_oauth import CodexOAuthVolume
from .contracts import Refused
from .opencode import OpenCodeSettings

# RFC 0038 D3: no session-host command may carry a permission or sandbox bypass.
# Matching is case-insensitive and reads `--flag=value` and `--flag value` alike.
# Fragments match anywhere in an argument, so `-s danger-full-access` and
# `-c sandbox_mode="danger-full-access"` both refuse. Profile argv is a trusted
# fixed tuple and the prompt travels on stdin, so a false refusal can only come
# from a profile author, never from a request.
#
# Not covered: short switches whose meaning differs between runtimes, such as
# Gemini's `-y` (yolo). Refusing every `-y` would be wrong for other CLIs, so a
# Gemini profile needs its own closed preset before it can be admitted.
_BYPASS_FRAGMENTS = ("dangerously", "bypasspermissions", "danger-full-access")
_BYPASS_SWITCHES = frozenset({"--yolo"})
_BYPASS_VALUES = {
    "--sandbox": frozenset({"false", "0", "no", "off", "none"}),
    "--approval-mode": frozenset({"yolo"}),
    "--permission-mode": frozenset({"bypasspermissions"}),
}


def carries_bypass(command: tuple[str, ...] | list[str]) -> bool:
    lowered = [argument.lower() for argument in command]
    for index, argument in enumerate(lowered):
        if any(fragment in argument for fragment in _BYPASS_FRAGMENTS):
            return True
        flag, joined, value = argument.partition("=")
        if flag in _BYPASS_SWITCHES:
            return True
        if not joined and index + 1 < len(lowered):
            value = lowered[index + 1]
        if value in _BYPASS_VALUES.get(flag, ()):
            return True
    return False


_CHECKS = frozenset(
    {
        "filesystem_isolation",
        "process_isolation",
        "restricted_egress",
        "builtin_shell_disabled",
        "builtin_filesystem_disabled",
        "builtin_web_disabled",
        "local_mcp_disabled",
        "provider_search_disabled",
        "provider_fetch_disabled",
        "provider_remote_mcp_disabled",
        "provider_code_disabled",
        "broker_only_mcp",
        "catalog_conformance",
    }
)


def _digest(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _settings(
    runtime: str, model: str, codex_auth: str = API_KEY
) -> OpenCodeSettings | CodexSettings | None:
    if runtime == "codex":
        return CodexSettings(model, auth=codex_auth)
    if codex_auth != API_KEY:
        raise Refused("invalid_runtime_profile")  # only Codex has an OAuth mode
    if runtime == "opencode":
        return OpenCodeSettings(model)
    return None


def _domain(
    image: tuple[ImageFile, ...],
    command: tuple[str, ...],
    runtime: str,
    model: str,
    codex_auth: str = API_KEY,
    codex_oauth: CodexOAuthVolume | None = None,
) -> BubblewrapDomain:
    settings = _settings(runtime, model, codex_auth)
    codex = settings if isinstance(settings, CodexSettings) else None
    return BubblewrapDomain(
        image=image,
        command=command,
        opencode=settings if isinstance(settings, OpenCodeSettings) else None,
        codex=codex,
        codex_oauth=codex_oauth if codex is not None and codex.writable_auth else None,
    )


@dataclass(frozen=True)
class Profile:
    """Trusted inert image and exact argv, never a control-frame configuration.

    Adapter-specific settings belong in the immutable command/image inventory
    and the closed native settings preset selected by this trusted runtime.
    The conformance artifact must prove their deny-all behavior for this tuple;
    flags or names alone establish no restriction. The existing material owner
    generates the invocation's sole-broker MCP file, credential and prompt.
    """

    runtime: str
    model: str
    image: tuple[ImageFile, ...]
    command: tuple[str, ...]
    # Codex only: ``oauth`` stages the host's device-auth document (a separate proof).
    codex_auth: str = API_KEY

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not str
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,199}", value) is None
                for value in (self.runtime, self.model)
            )
            or type(self.image) is not tuple
            or not self.image
            or any(
                type(entry) is not ImageFile or not _digest(entry.sha256) for entry in self.image
            )
            or type(self.command) is not tuple
            # "Codex" would skip the closed codex preset and take any argv.
            or self.runtime != self.runtime.lower()
        ):
            raise Refused("invalid_runtime_profile")
        # Reuse the actual backend's inert profile validation. It opens nothing and
        # starts nothing; immutable inputs are rechecked/snapshotted at use. The OAuth
        # lease is the factory's, so only the profile, not a domain, is checked here.
        settings = _settings(self.runtime, self.model, self.codex_auth)
        BubblewrapDomain.check_profile(
            self.image,
            self.command,
            opencode=settings if isinstance(settings, OpenCodeSettings) else None,
            codex=settings if isinstance(settings, CodexSettings) else None,
        )
        if carries_bypass(self.command):
            raise Refused("invalid_runtime_profile")

    @property
    def subject(self) -> dict[str, str]:
        settings = _settings(self.runtime, self.model, self.codex_auth)
        return {
            "runtime": self.runtime,
            "model": self.model,
            "image_sha256": _hash(
                sorted((entry.destination, entry.sha256) for entry in self.image)
            ),
            # Pin arguments and the exact selected material/pointer contract.
            # Native adapters add their templates and stdin/env roles; changing
            # any of those requires a freshly reviewed report subject.
            "settings_sha256": _hash(
                {
                    "command": self.command,
                    "material_contract": settings.identity if settings else "broker-only-v1",
                }
            ),
        }


@dataclass(frozen=True, init=False)
class ReviewedProfile:
    """An exact host profile paired with a constructor-pinned reviewed report.

    The expected digest must come from trusted deployment/review configuration,
    never the report itself or a request. A matching digest authenticates only
    that chosen artifact's bytes. The external reviewer still has to reconcile
    real execution, image/settings identity and each adversarial conformance
    check before approving that digest. Synthetic fixtures grant no real tuple.
    """

    profile: Profile
    report_sha256: str

    def __init__(self, profile: Profile, *, report: bytes, expected_sha256: str) -> None:
        if (
            type(profile) is not Profile
            or type(report) is not bytes
            or not 0 < len(report) <= 16384
            or not _digest(expected_sha256)
            or hashlib.sha256(report).hexdigest() != expected_sha256
        ):
            raise Refused("runtime_proof_refused")
        try:

            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result

            def nonfinite(_value):
                raise ValueError

            data = json.loads(report, object_pairs_hook=pairs, parse_constant=nonfinite)
            if (
                type(data) is not dict
                or set(data)
                != {"version", "subject", "checks", "execution_sha256", "artifact_sha256"}
                or type(data["version"]) is not int
                or data["version"] != 1
                or type(data["subject"]) is not dict
                or data["subject"] != profile.subject
                or type(data["checks"]) is not dict
                or set(data["checks"]) != _CHECKS
                or any(
                    type(value) is not str or value != "pass" for value in data["checks"].values()
                )
                or not _digest(data["execution_sha256"])
                or not _digest(data["artifact_sha256"])
            ):
                raise ValueError
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise Refused("runtime_proof_refused") from None
        object.__setattr__(self, "profile", profile)
        object.__setattr__(self, "report_sha256", expected_sha256)


class ProfileFactory:
    """The existing LocalControl factory seam, with no fallback or default tuple."""

    def __init__(
        self,
        profiles: tuple[ReviewedProfile, ...] = (),
        *,
        codex_oauth: CodexOAuthVolume | None = None,
    ) -> None:
        """``codex_oauth`` is the host's lease over its volume; OAuth profiles need it."""
        if type(profiles) is not tuple or any(type(p) is not ReviewedProfile for p in profiles):
            raise Refused("invalid_runtime_profiles")
        keys = [(p.profile.runtime, p.profile.model) for p in profiles]
        if len(keys) != len(set(keys)):
            raise Refused("invalid_runtime_profiles")
        if codex_oauth is not None and type(codex_oauth) is not CodexOAuthVolume:
            raise Refused("invalid_runtime_profiles")
        if codex_oauth is None and any(p.profile.codex_auth != API_KEY for p in profiles):
            raise Refused("invalid_runtime_profiles")
        self._profiles = profiles
        self._codex_oauth = codex_oauth

    def __call__(self, runtime: str, model: str) -> BubblewrapDomain:
        for reviewed in self._profiles:
            profile = reviewed.profile
            if (runtime, model) == (profile.runtime, profile.model):
                return _domain(
                    profile.image,
                    profile.command,
                    profile.runtime,
                    profile.model,
                    profile.codex_auth,
                    self._codex_oauth,
                )
        raise Refused("runtime_ineligible")
