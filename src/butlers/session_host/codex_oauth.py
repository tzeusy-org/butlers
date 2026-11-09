"""Codex device auth on the session host: stage the volume's document, collect its refresh.

Dark (bu-kiuh8v.16, RFC 0038 D3). In OAuth mode (``CodexSettings(auth="oauth")``) a
launch's credential is not a secret. It is the SHA-256 of the document the control plane
pushed to this host's credential volume, under the fence of ``CodexAuthFencedHost``.

* :meth:`CodexOAuthVolume.stage` takes the host's one OAuth lease, reads the volume and
  refuses unless the volume still holds exactly that digest and a valid device-auth
  document. The document is then staged as the runtime's writable ``auth.json``.
* After the domain settles, nothing in the sandbox is alive to write. Then
  :meth:`CodexOAuthVolume.collect` reads what the runtime left there. A refreshed
  document for the same account replaces the volume by compare-and-set on the staged
  digest; anything else (garbage, another account, an API-key document) leaves the
  volume untouched. The control plane collects the volume afterwards, as before.
* One lease per volume: a second OAuth launch refuses ``codex_auth_busy`` until the first
  is collected. Two runs on one document would spend a single-use refresh token twice.
  The control plane serializes these launches too; this is the host's own guard. A
  domain whose settlement is unproven keeps the lease, so OAuth launches stay refused.

No document byte, digest or account reaches an exception, a log or a reply.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import re
import threading
from dataclasses import dataclass, field

from .contracts import Refused
from .credential_volume import CredentialVolume, valid_document

logger = logging.getLogger(__name__)

_DIGEST = re.compile(rb"[0-9a-f]{64}")
_ACCOUNT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
_TOKENS = ("id_token", "access_token", "refresh_token")
_TOP_LEVEL = frozenset({"OPENAI_API_KEY", "auth_mode", "tokens", "last_refresh"})
_TOKEN_FIELDS = frozenset({*_TOKENS, "account_id"})


def oauth_account(document: bytes) -> str | None:
    """The account a Codex device-auth document is for, or None if it is not one.

    One closed shape: ``tokens`` holds non-empty id, access and refresh tokens and the
    account id, which the id token's account claim repeats; there is no API key; nothing
    else but ``auth_mode`` (``chatgpt``) and ``last_refresh``.
    """
    if not valid_document(document):
        return None
    parsed = json.loads(document)
    tokens = parsed.get("tokens")
    if (
        not set(parsed) <= _TOP_LEVEL
        or parsed.get("OPENAI_API_KEY") is not None
        or parsed.get("auth_mode", "chatgpt") != "chatgpt"
        or not isinstance(parsed.get("last_refresh", ""), str)
        or type(tokens) is not dict
        or set(tokens) != _TOKEN_FIELDS
        or any(type(tokens[name]) is not str or not tokens[name] for name in _TOKENS)
        or type(tokens["account_id"]) is not str
        or _ACCOUNT.fullmatch(tokens["account_id"]) is None
        # A refresh rewrites the tokens and keeps ``account_id``, so the id token's own
        # account claim must agree with it, or the document is for someone else.
        or _id_token_account(tokens["id_token"]) != tokens["account_id"]
    ):
        return None
    return tokens["account_id"]


def _id_token_account(id_token: str) -> str | None:
    """The ChatGPT account an id token's claims name, read, not verified.

    The host cannot verify the issuer's signature offline; this only refuses a document
    whose parts disagree. The provider still authenticates every token it receives.
    """
    parts = id_token.split(".")
    if len(parts) != 3 or len(parts[1]) > 16384:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError, RecursionError, binascii.Error):
        return None
    auth = claims.get("https://api.openai.com/auth") if type(claims) is dict else None
    account = auth.get("chatgpt_account_id") if type(auth) is dict else None
    return account if type(account) is str else None


@dataclass(frozen=True)
class StagedOAuth:
    """What one OAuth launch was staged with; the document stays out of ``repr``."""

    document: bytes = field(repr=False)
    sha256: str = field(repr=False)
    account: str = field(repr=False)


class CodexOAuthVolume:
    """The host's one OAuth lease over its credential volume (trusted construction)."""

    def __init__(self, volume: CredentialVolume) -> None:
        if type(volume) is not CredentialVolume:
            raise Refused("credential_volume_unavailable")
        self._volume = volume
        self._lock = threading.Lock()
        self._held: StagedOAuth | None = None

    def stage(self, reference: bytes) -> StagedOAuth:
        """Lease the volume's document if it is still the pushed one."""
        if type(reference) is not bytes or _DIGEST.fullmatch(reference) is None:
            raise Refused("unsupported_codex_credential")
        with self._lock:
            if self._held is not None:
                raise Refused("codex_auth_busy")
            found = self._volume.read()
            if found is None or not hmac.compare_digest(found[1].encode(), reference):
                raise Refused("codex_auth_stale")
            document, digest = found
            account = oauth_account(document)
            if account is None:
                raise Refused("unsupported_codex_credential")
            staged = StagedOAuth(document, digest, account)
            self._held = staged
            return staged

    def collect(self, staged: StagedOAuth, written: bytes | None) -> str:
        """Install the runtime's refresh, or leave the volume as it is; then release.

        ``written`` is what the settled runtime left in its auth file (None if it could
        not be read). Returns ``unchanged``, ``installed``, ``conflict`` (the volume
        moved on, so a newer document wins) or ``rejected``.
        """
        with self._lock:
            if self._held is not staged:
                raise Refused("codex_auth_lease_unavailable")
            try:
                if written == staged.document:
                    return "unchanged"
                if written is None or oauth_account(written) != staged.account:
                    logger.warning("session host codex oauth: refresh rejected; volume unchanged")
                    return "rejected"
                if self._volume.install(written, expected_sha256=staged.sha256):
                    return "installed"
                logger.warning("session host codex oauth: volume moved on; refresh not installed")
                return "conflict"
            finally:
                self._held = None


__all__ = ["CodexOAuthVolume", "StagedOAuth", "oauth_account"]
