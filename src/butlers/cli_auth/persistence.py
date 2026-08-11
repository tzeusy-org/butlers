"""CLI auth token persistence — DB-backed storage for CLI credential files.

After a successful device-code auth flow, the CLI writes tokens to a file
on disk (e.g. ``~/.codex/auth.json``). This module copies those tokens into
the shared credential store so they survive container restarts and pod
rescheduling in Kubernetes.

On startup, tokens are restored from the DB to the filesystem paths the
CLIs expect.

Key convention: ``cli-auth/{provider_name}`` (e.g. ``cli-auth/codex``).
Category: ``cli-auth``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

_CATEGORY = "cli-auth"


def _db_key(provider: CLIAuthProviderDef) -> str:
    return f"cli-auth/{provider.name}"


def _is_valid_codex_auth_document(content: str) -> bool:
    """Return whether *content* is a non-empty Codex auth JSON object."""
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    return isinstance(parsed, dict) and bool(parsed)


class _StagedDeviceAuthDocumentError(ValueError):
    """Raised when untrusted device-auth output cannot be projected safely."""


def _reject_duplicate_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build a JSON object only when each key appears exactly once."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _StagedDeviceAuthDocumentError("device-auth output has duplicate JSON keys")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> object:
    """Reject NaN and infinities instead of letting Python normalize them."""
    raise _StagedDeviceAuthDocumentError(
        f"device-auth output has nonstandard JSON constant {value!r}"
    )


def _required_nonempty_string(entry: dict[str, object], field: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value:
        raise _StagedDeviceAuthDocumentError("device-auth output lacks OpenAI OAuth field")
    return value


def _parse_strict_device_auth_document(content: str) -> dict[str, object]:
    """Parse one child-produced JSON object without lossy JSON conveniences."""
    document = json.loads(
        content,
        object_pairs_hook=_reject_duplicate_json_object,
        parse_constant=_reject_nonstandard_json_constant,
    )
    if not isinstance(document, dict):
        raise _StagedDeviceAuthDocumentError("device-auth output is not a JSON object")
    return document


def _project_opencode_openai_device_auth_document(content: str) -> str | None:
    """Reconstruct the one OpenAI OAuth authority allowed to leave device auth.

    OpenCode stores providers together in ``auth.json``.  Its device-auth child
    is untrusted, so this flow must not persist a peer provider, unknown JSON,
    duplicate keys, or nonstandard numbers merely because it also contains an
    OpenAI-shaped entry.
    """
    try:
        document = _parse_strict_device_auth_document(content)
        if set(document) != {"openai"}:
            raise _StagedDeviceAuthDocumentError("device-auth output has an unexpected provider")

        entry = document["openai"]
        if not isinstance(entry, dict) or entry.get("type") != "oauth":
            raise _StagedDeviceAuthDocumentError("device-auth output lacks OpenAI OAuth")

        allowed_fields = {"type", "refresh", "access", "expires", "accountId", "enterpriseUrl"}
        if (
            not {"type", "refresh", "access", "expires"}.issubset(entry)
            or set(entry) - allowed_fields
        ):
            raise _StagedDeviceAuthDocumentError("device-auth output has unexpected OpenAI fields")

        expires = entry["expires"]
        if type(expires) is not int or expires < 0:
            raise _StagedDeviceAuthDocumentError("device-auth output has invalid OpenAI expiry")

        projected_entry: dict[str, object] = {
            "type": "oauth",
            "refresh": _required_nonempty_string(entry, "refresh"),
            "access": _required_nonempty_string(entry, "access"),
            "expires": expires,
        }
        for field in ("accountId", "enterpriseUrl"):
            if field in entry:
                value = entry[field]
                if not isinstance(value, str) or not value:
                    raise _StagedDeviceAuthDocumentError(
                        "device-auth output has invalid OpenAI metadata"
                    )
                projected_entry[field] = value
    except (
        _StagedDeviceAuthDocumentError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        return None

    return json.dumps(
        {"openai": projected_entry},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _project_codex_device_auth_document(content: str) -> str | None:
    """Reconstruct the pinned Codex ChatGPT device-auth document exactly.

    ``@openai/codex@0.144.1`` writes ChatGPT device-code output with these
    four top-level fields and the four scalar ``tokens`` fields below.  Reject
    every other auth mode or field before the child can change the global
    Codex authority.
    """
    try:
        document = _parse_strict_device_auth_document(content)
        if set(document) != {"auth_mode", "OPENAI_API_KEY", "tokens", "last_refresh"}:
            raise _StagedDeviceAuthDocumentError("device-auth output has unexpected Codex fields")
        if document["auth_mode"] != "chatgpt":
            raise _StagedDeviceAuthDocumentError("device-auth output is not Codex ChatGPT auth")

        api_key = document["OPENAI_API_KEY"]
        if api_key is not None and (not isinstance(api_key, str) or not api_key):
            raise _StagedDeviceAuthDocumentError("device-auth output has invalid Codex API key")

        last_refresh = document["last_refresh"]
        if not isinstance(last_refresh, str) or not last_refresh:
            raise _StagedDeviceAuthDocumentError(
                "device-auth output has invalid Codex refresh time"
            )

        tokens = document["tokens"]
        expected_token_fields = {"id_token", "access_token", "refresh_token", "account_id"}
        if not isinstance(tokens, dict) or set(tokens) != expected_token_fields:
            raise _StagedDeviceAuthDocumentError("device-auth output has unexpected Codex tokens")

        account_id = tokens["account_id"]
        if account_id is not None and (not isinstance(account_id, str) or not account_id):
            raise _StagedDeviceAuthDocumentError("device-auth output has invalid Codex account")

        projected_tokens = {
            "id_token": _required_nonempty_string(tokens, "id_token"),
            "access_token": _required_nonempty_string(tokens, "access_token"),
            "refresh_token": _required_nonempty_string(tokens, "refresh_token"),
            "account_id": account_id,
        }
    except (
        _StagedDeviceAuthDocumentError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        return None

    return json.dumps(
        {
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": api_key,
            "tokens": projected_tokens,
            "last_refresh": last_refresh,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _project_staged_device_auth_document(provider: CLIAuthProviderDef, content: str) -> str | None:
    """Project device-auth output to its exact provider-specific persistence form.

    This deliberately accepts only the two registered device-code providers.
    A new provider must add its exact staged-output schema here rather than
    receiving a permissive file-copy path by default.
    """
    if provider.name == "codex":
        return _project_codex_device_auth_document(content)
    if provider.name == "opencode-openai":
        return _project_opencode_openai_device_auth_document(content)
    return None


def _require_codex_authority(codex_authority: CredentialStore | None) -> CredentialStore:
    """Return the selected Codex authority or fail closed without a local fallback."""
    if codex_authority is None:
        raise RuntimeError("Codex system-global credential authority is unavailable.")
    # Validate selection before a provider-specific operation so callers cannot
    # accidentally pass an ordinary local-first store as an authority.
    codex_authority.require_system_global_pool()
    return codex_authority


async def load_persisted_token(
    provider: CLIAuthProviderDef,
    store: CredentialStore,
    *,
    codex_authority: CredentialStore | None = None,
) -> str | None:
    """Load a CLI credential from its provider-specific authority.

    Codex exclusively reads the selected system-global authority. Other CLI
    providers retain the existing local-first store lookup semantics.
    """
    key = _db_key(provider)
    if provider.name == "codex":
        return await _require_codex_authority(codex_authority).load_codex_cli_auth()
    return await store.load(key)


async def _store_persisted_token(
    provider: CLIAuthProviderDef,
    store: CredentialStore,
    content: str,
    *,
    codex_authority: CredentialStore | None = None,
) -> None:
    """Persist *content* through the provider-specific credential authority."""
    key = _db_key(provider)
    kwargs = {
        "category": _CATEGORY,
        "description": f"CLI auth token for {provider.display_name}",
        "is_sensitive": True,
    }
    if provider.name == "codex":
        await _require_codex_authority(codex_authority).store_codex_cli_auth(content)
        return
    await store.store(key, content, **kwargs)


async def capture_device_auth_authority_baseline(
    provider: CLIAuthProviderDef,
    store: CredentialStore,
    *,
    codex_authority: CredentialStore | None = None,
) -> str | None:
    """Capture the exact authority value a later device-auth CAS will fence.

    The snapshot belongs before child launch.  For OpenCode this deliberately
    reads the same shared-or-flat authority selected by
    :meth:`CredentialStore.store_shared_if_unchanged`, rather than using the
    ordinary local-first lookup that could observe a different row.
    """
    key = _db_key(provider)
    if provider.name == "codex":
        return await _require_codex_authority(codex_authority).load_codex_cli_auth()
    if store.shared_pool is not None:
        return await store.load_shared(key)
    return await store.load(key)


async def _store_staged_device_auth_if_unchanged(
    provider: CLIAuthProviderDef,
    store: CredentialStore,
    content: str,
    *,
    expected_authority_value: str | None,
    codex_authority: CredentialStore | None = None,
) -> bool:
    """CAS-write one already-projected device-auth document to its authority."""
    key = _db_key(provider)
    kwargs = {
        "category": _CATEGORY,
        "description": f"CLI auth token for {provider.display_name}",
        "is_sensitive": True,
    }
    if provider.name == "codex":
        return await _require_codex_authority(codex_authority).store_codex_cli_auth_if_unchanged(
            content,
            expected_value=expected_authority_value,
        )
    return await store.store_shared_if_unchanged(
        key,
        content,
        expected_value=expected_authority_value,
        **kwargs,
    )


def _project_opencode_go_runtime_entry(entry: object) -> dict[str, str] | None:
    """Return the exact OpenCode Go entry safe to retain in shared auth.json."""
    if not isinstance(entry, dict) or set(entry) != {"type", "key"}:
        return None
    if entry.get("type") != "api":
        return None
    key = entry.get("key")
    if not isinstance(key, str) or not key:
        return None
    return {"type": "api", "key": key}


def _reconcile_device_auth_runtime_file(
    provider: CLIAuthProviderDef,
    projected: str,
) -> bool:
    """Atomically project a confirmed device authority into its runtime file.

    This runs only after the authoritative compare-and-set succeeds.  OpenCode
    device auth owns the ``openai`` entry but shares its runtime file with the
    independently managed Go API entry, so retain that peer only when it can
    itself be reconstructed from the pinned scalar schema.  Unknown entries
    never ride along from a pre-existing file.
    """
    token_path = provider.token_path
    if token_path is None:
        logger.warning("CLI auth persist: provider has no canonical runtime projection path")
        return False

    try:
        if provider.name == "opencode-openai":
            staged_document = _parse_strict_device_auth_document(projected)
            runtime_document: dict[str, object] = {"openai": staged_document["openai"]}

            if token_path.exists():
                existing_document = _parse_strict_device_auth_document(
                    token_path.read_text(encoding="utf-8")
                )
                go_entry = _project_opencode_go_runtime_entry(existing_document.get("opencode-go"))
                if go_entry is not None:
                    runtime_document["opencode-go"] = go_entry

            runtime_content = json.dumps(
                runtime_document,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        elif provider.name == "codex":
            runtime_content = projected
        else:
            return False

        _write_token_file_atomically(token_path, runtime_content)
    except (
        OSError,
        _StagedDeviceAuthDocumentError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        logger.warning("CLI auth persist: canonical runtime projection failed safely")
        return False

    return True


async def persist_validated_staged_device_auth_bytes(
    provider: CLIAuthProviderDef,
    store: CredentialStore,
    content: bytes,
    *,
    expected_authority_value: str | None,
    codex_authority: CredentialStore | None = None,
) -> bool:
    """Persist bytes already validated through the trusted sandbox root FD.

    ``persist_token`` remains the legacy canonical-file reader until all
    callers are routed through the sandbox.  Device-auth sandbox callers must
    use this seam: it never receives a path and therefore cannot reopen a
    canonical credential file after child containment has been verified.
    """
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("CLI auth persist: staged device-auth bytes are not UTF-8")
        return False

    projected = _project_staged_device_auth_document(provider, decoded)
    if projected is None:
        logger.warning("CLI auth persist: refusing invalid staged device-auth document")
        return False
    if provider.token_path is None:
        # A successful authority CAS without a usable runtime projection would
        # leave the just-completed device flow unavailable until restart.
        logger.warning("CLI auth persist: provider has no canonical runtime projection path")
        return False

    try:
        stored = await _store_staged_device_auth_if_unchanged(
            provider,
            store,
            projected,
            expected_authority_value=expected_authority_value,
            codex_authority=codex_authority,
        )
    except Exception:
        # Never log an exception from a credential-store write: database bind
        # context can retain the raw staged document.  Every provider maps an
        # unavailable authority to the same value-free terminal failure.
        logger.warning("CLI auth persist: device-auth authority write failed safely")
        return False

    if not stored:
        logger.warning("CLI auth persist: device-auth authority changed before commit")
        return False

    if not _reconcile_device_auth_runtime_file(provider, projected):
        return False

    logger.info("CLI auth persist: stored validated staged token for %s", provider.name)
    return True


def _write_token_file_atomically(token_path: Path, content: str) -> None:
    """Replace a CLI auth file atomically with restrictive permissions.

    Readers either observe the old complete document or the newly fsynced
    complete document; they never observe a truncated write.  The helper does
    not log because its caller has the provider/butler context and because
    arbitrary filesystem errors can contain sensitive path or content detail.
    """
    token_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{token_path.name}.", dir=token_path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        file_handle = os.fdopen(fd, "w", encoding="utf-8")
        fd = -1  # ownership transferred to file_handle
        with file_handle:
            file_handle.write(content)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temp_path, token_path)
        # Keep the invariant even when a platform's replace behavior does not
        # retain the temporary file mode exactly as expected.
        os.chmod(token_path, 0o600)
    finally:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            # Preserve the original failure if there was one.  A later
            # reconciliation will safely replace a stale temporary artifact.
            pass


async def persist_token(
    provider: CLIAuthProviderDef,
    store: CredentialStore,
    *,
    codex_authority: CredentialStore | None = None,
) -> bool:
    """Read the CLI's token file from disk and store it in the DB.

    Called after a successful auth flow. Returns True if persisted.
    """
    token_path = provider.token_path
    if token_path is None:
        logger.warning("CLI auth persist: provider=%s has no token path", provider.name)
        return False

    if not token_path.exists():
        logger.warning(
            "CLI auth persist: token file %s does not exist for %s",
            token_path,
            provider.name,
        )
        return False

    try:
        content = token_path.read_text(encoding="utf-8")
    except OSError:
        logger.warning(
            "CLI auth persist: failed to read token file for provider=%s path=%s",
            provider.name,
            token_path,
        )
        return False

    if not content.strip():
        logger.warning("CLI auth persist: token file %s is empty", provider.token_path)
        return False
    if provider.name == "codex" and not _is_valid_codex_auth_document(content):
        logger.warning("CLI auth persist: refusing invalid Codex auth document")
        return False

    key = _db_key(provider)
    try:
        await _store_persisted_token(
            provider,
            store,
            content,
            codex_authority=codex_authority,
        )
    except Exception:
        # Deliberately never log the exception: DB bind context can retain the
        # serialized auth document. A missing authority must not fall back to
        # the local schema where this callback is running.
        if provider.name == "codex":
            logger.warning("CLI auth persist: Codex system-global authority unavailable")
            return False
        raise
    logger.info("CLI auth persist: stored token for %s (key=%s)", provider.name, key)
    return True


async def restore_tokens(
    store: CredentialStore,
    *,
    codex_authority: CredentialStore | None = None,
) -> dict[str, bool]:
    """Restore all CLI auth tokens from the DB to their filesystem paths.

    Called on application startup. Returns a dict of provider name → success.
    """
    results: dict[str, bool] = {}

    for provider in PROVIDERS.values():
        key = _db_key(provider)
        try:
            content = await load_persisted_token(
                provider,
                store,
                codex_authority=codex_authority,
            )
        except Exception:
            if provider.name == "codex":
                logger.warning(
                    "CLI auth restore: Codex system-global authority unavailable; "
                    "skipping local credential fallback"
                )
            else:
                logger.warning("CLI auth restore: failed to load key=%s from credential store", key)
            results[provider.name] = False
            continue

        if content is None:
            logger.debug("CLI auth restore: no stored token for %s", provider.name)
            results[provider.name] = False
            continue
        if provider.name == "codex" and not _is_valid_codex_auth_document(content):
            logger.warning("CLI auth restore: refusing invalid stored Codex auth document")
            results[provider.name] = False
            continue

        try:
            token_path = provider.token_path
            if token_path is None:
                logger.warning("CLI auth restore: provider=%s has no token path", provider.name)
                results[provider.name] = False
                continue

            token_path.parent.mkdir(parents=True, exist_ok=True)

            # Multiple *non-Codex* providers may share the same token_path
            # (e.g. opencode-openai and opencode-go both use auth.json).
            # Preserve that compatibility merge only for them. Codex has one
            # explicit authority and must replace, rather than inherit from,
            # a pre-existing local document.
            final_content = content
            if provider.name != "codex" and token_path.exists():
                try:
                    existing = json.loads(token_path.read_text(encoding="utf-8"))
                    restored = json.loads(content)
                    if isinstance(existing, dict) and isinstance(restored, dict):
                        existing.update(restored)
                        final_content = json.dumps(existing, indent=2)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass  # Not JSON — fall back to full overwrite

            _write_token_file_atomically(token_path, final_content)
            logger.info(
                "CLI auth restore: wrote token for %s to %s",
                provider.name,
                token_path,
            )
            results[provider.name] = True
        except OSError:
            logger.warning("CLI auth restore: failed to write token for provider=%s", provider.name)
            results[provider.name] = False

    return results


def _record_codex_baseline() -> None:
    """Record the codex ``auth.json`` baseline after a restore so the first
    post-startup invocation does not falsely detect a rotation. Best-effort."""
    try:
        from butlers.core.runtimes._codex_auth_sync import record_auth_baseline

        codex_provider = PROVIDERS.get("codex")
        if codex_provider is not None and codex_provider.token_path is not None:
            record_auth_baseline(codex_provider.token_path)
    except Exception:
        logger.debug("codex_auth_sync: baseline recording skipped")


async def restore_connector_cli_auth(
    credential_store: CredentialStore | None,
    *,
    codex_authority: CredentialStore | None,
    context: str,
) -> dict[str, bool]:
    """Restore CLI-auth tokens from the shared credential DB at connector startup.

    Standalone connector containers (whatsapp/telegram user clients, live-listener)
    spawn a ``DiscretionDispatcher`` -> ``CodexAdapter`` but, unlike the daemon
    (:mod:`butlers.lifecycle`) and the dashboard API (:mod:`butlers.api.app`),
    never restore the shared ``cli-auth/codex`` token to disk. Without it
    ``~/.codex/auth.json`` is absent, every discretion-tier codex call 401s, and
    weight-below-fail-open senders silently fail closed (IGNORE). This mirrors the
    daemon-startup restore (restore_tokens + codex baseline) onto a connector's own
    DB pool, honoring disposition A (shared daemon codex identity): the codex row
    lives under the single fixed key ``cli-auth/codex`` in ``butler_secrets`` and is
    read through an explicitly supplied system-global authority. A connector
    cursor/model pool is never inferred to be that authority. Non-Codex
    providers continue to use the caller's existing connector-local store.

    Non-fatal but deliberately LOUD: on any failure, or when no codex token is
    restored, this logs at WARNING so a connector never *silently* runs without codex
    auth (the exact bu-wzbu9 fail-closed-IGNORE bug) — surfaced alongside each
    connector's existing discretion-auth health hook. Returns the per-provider restore
    results (``{}`` on outright failure).
    """
    if credential_store is None:
        logger.warning(
            "%s: no connector credential store available to restore CLI auth; "
            "Codex discretion calls will fail closed until a credential DB is reachable",
            context,
        )
        return {}

    try:
        results = await restore_tokens(
            credential_store,
            codex_authority=codex_authority,
        )
    except Exception:
        logger.warning(
            "%s: CLI-auth restore from the credential DB failed; discretion-tier codex "
            "calls will 401 and fail closed until auth is restored",
            context,
        )
        return {}

    restored = sum(1 for v in results.values() if v)
    if restored:
        logger.info("%s: restored %d CLI auth token(s) from the credential DB", context, restored)

    if results.get("codex"):
        _record_codex_baseline()
    else:
        logger.warning(
            "%s: no codex CLI-auth token found in the credential DB — ~/.codex/auth.json is "
            "absent, so discretion-tier codex calls will 401 and low-weight senders fail closed "
            "(silent IGNORE). Ensure the daemon has authenticated codex (cli-auth/codex).",
            context,
        )

    return results
