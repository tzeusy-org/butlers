"""CLI auth endpoints.

Provides a REST API for starting and polling CLI tool authentication
flows. Supports two modes:

- **device_code**: Interactive device-code authorization (OpenCode, Codex).
- **api_key**: Simple API key storage and validation (OpenCode Go).

After a successful auth flow, credentials are persisted to the shared
credential store so they survive container restarts (no PV needed).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from butlers.api.models.cli_auth import (
    CLIAuthApiKeyRequest,
    CLIAuthApiKeyResponse,
    CLIAuthHealthState,
    CLIAuthProvider,
    CLIAuthSessionResponse,
    CLIAuthSessionState,
    CLIAuthStartResponse,
    CLIAuthTestResponse,
)
from butlers.cli_auth.health import AuthHealthState, probe_all, probe_provider
from butlers.cli_auth.persistence import persist_token
from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef
from butlers.cli_auth.session import (
    CLIAuthSession,
    get_session,
    store_session,
)
from butlers.core.runtimes.opencode import canonical_to_execution_model
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cli-auth", tags=["cli-auth"])

# ---------------------------------------------------------------------------
# DB dependency (same pattern as oauth.py)
# ---------------------------------------------------------------------------


def _get_db_manager() -> Any:
    """Stub replaced at startup by wire_db_dependencies()."""
    return None


def _make_credential_store(db_manager: Any) -> CredentialStore | None:
    if db_manager is None:
        return None
    try:
        pool = db_manager.credential_shared_pool()
    except Exception:
        return None
    # The dashboard owns the system-global CLI auth record. Even in a flat
    # topology, name that authority explicitly rather than relying on local
    # CredentialStore resolution order.
    return CredentialStore(pool, system_global_pool=pool)


def _build_on_success(db_manager: Any):
    """Build an on_success callback that persists the token to DB.

    For the ``codex`` provider an additional pre-warm step runs after
    ``persist_token`` completes.  The pre-warm calls ``codex login status``
    under the cross-process refresh lock so the newly-issued token is written
    to disk and all concurrent butler invocations within its TTL skip the
    server-side refresh race.
    """
    store = _make_credential_store(db_manager)
    if store is None:
        return None

    async def _on_success(provider: CLIAuthProviderDef) -> None:
        persisted = await persist_token(
            provider,
            store,
            codex_authority=store if provider.name == "codex" else None,
        )

        if provider.name == "codex":
            if not persisted:
                # Do not let a device-auth session report success when its
                # local file could not be committed to the selected global
                # authority. The session maps this safe False result to a
                # failed state without exposing provider or credential detail.
                logger.warning("CLI auth on_success: Codex authority persistence failed safely")
                return False
            # Warm the freshly-issued token under the cross-process lock so
            # all butler daemons see a valid access_token on disk before any
            # of them start spawning Codex CLI invocations.
            try:
                import shutil as _shutil

                from butlers.core.runtimes._codex_auth_sync import (
                    codex_auth_file_matches_authority,
                    finalize_codex_auth_rotation,
                    reconcile_codex_auth,
                )
                from butlers.core.runtimes.codex import (
                    CodexAdapter,
                    _resolve_canonical_home,
                    run_codex_pre_warm,
                )

                codex_binary = _shutil.which("codex")
                real_home = _resolve_canonical_home(None)
                if codex_binary and real_home:
                    codex_dir = real_home / ".codex"
                    token_path = codex_dir / "auth.json"
                    # Establish the authority baseline before prewarm.  Its
                    # exact bytes are the only safe CAS expectation for this
                    # callback's finalization: a later dashboard refresh must
                    # win over this older prewarm's local rotation.
                    baseline = await reconcile_codex_auth(
                        token_path,
                        store,
                        butler_name="dashboard",
                    )
                    if (
                        not baseline.authority_known
                        or baseline.expected_store_value is None
                        or not codex_auth_file_matches_authority(
                            token_path,
                            baseline.expected_store_value,
                        )
                    ):
                        logger.warning(
                            "CLI auth on_success: system-global Codex authority unavailable; "
                            "skipping login-status subprocess"
                        )
                        return

                    prewarm_baseline = baseline

                    async def _authority_preflight() -> bool:
                        nonlocal prewarm_baseline
                        prewarm_baseline = await reconcile_codex_auth(
                            token_path,
                            store,
                            butler_name="dashboard",
                        )
                        return (
                            prewarm_baseline.authority_known
                            and prewarm_baseline.expected_store_value is not None
                            and codex_auth_file_matches_authority(
                                token_path,
                                prewarm_baseline.expected_store_value,
                            )
                        )

                    try:
                        prewarm_completed = await run_codex_pre_warm(
                            codex_dir,
                            codex_binary,
                            authority_preflight=_authority_preflight,
                        )
                    finally:
                        await finalize_codex_auth_rotation(
                            token_path,
                            store,
                            expected_store_value=prewarm_baseline.expected_store_value,
                            authority_known=prewarm_baseline.authority_known,
                            butler_name="dashboard",
                        )
                    # Mark pre-warm done for this process so the first spawn
                    # takes the fast path.  A pre-warm exception skips this
                    # line after the final reconciliation and is handled by
                    # the outer best-effort boundary.
                    if prewarm_completed:
                        CodexAdapter._prewarm_done.add(str(codex_dir))
            except Exception:
                logger.warning("CLI auth on_success: codex pre-warm failed (non-fatal)")
            return True

    return _on_success


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/providers",
    response_model=list[CLIAuthProvider],
    summary="List CLI auth providers and their status",
)
async def list_providers(
    db_manager: Any = Depends(_get_db_manager),
) -> list[CLIAuthProvider]:
    """List all registered CLI auth providers with current auth status.

    Runs live health probes against each provider's status command to
    determine whether tokens are actually valid (not just present on disk).
    """
    store = _make_credential_store(db_manager)
    health_results = await probe_all(credential_store=store, codex_authority=store)

    result = []
    for p in PROVIDERS.values():
        # Show api_key providers even if binary is not installed
        if not p.is_available() and p.auth_mode != "api_key":
            continue
        health = health_results.get(p.name)
        if p.name == "codex":
            # The local file is merely the runtime projection of the selected
            # authority. A stale file must not make the dashboard claim Codex
            # is authenticated when the strict authority probe failed.
            authenticated = health is not None and health.state == AuthHealthState.authenticated
        elif p.auth_mode == "device_code":
            authenticated = p.is_authenticated()
        else:
            authenticated = health is not None and health.state == AuthHealthState.authenticated
        result.append(
            CLIAuthProvider(
                name=p.name,
                display_name=p.display_name,
                runtime=p.runtime,
                auth_mode=p.auth_mode,
                authenticated=authenticated,
                health=CLIAuthHealthState(health.state) if health else None,
                health_detail=health.detail if health else None,
                token_path=str(p.token_path) if p.token_path else None,
                env_var=p.env_var or None,
            )
        )
    return result


@router.post(
    "/{provider}/start",
    response_model=CLIAuthStartResponse,
    summary="Start a CLI auth device-code flow",
)
async def start_auth(
    provider: str,
    db_manager: Any = Depends(_get_db_manager),
) -> CLIAuthStartResponse:
    """Spawn a CLI login subprocess and return the device code for authorization.

    The session runs in the background; poll GET /sessions/{session_id}
    for state updates. On success, the token is automatically persisted
    to the shared credential store.
    """
    provider_def = PROVIDERS.get(provider)
    if provider_def is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    if not provider_def.is_available():
        raise HTTPException(
            status_code=503,
            detail=f"CLI binary '{provider_def.binary()}' not found on PATH.",
        )

    on_success = _build_on_success(db_manager)
    if provider_def.name == "codex" and on_success is None:
        # Starting device auth writes a local canonical auth file. Without the
        # explicitly selected global authority its result could only remain a
        # stale local credential, so refuse before spawning the CLI instead of
        # discovering the failure after the owner has completed the flow.
        logger.warning("CLI auth: Codex device auth refused without system-global authority")
        raise HTTPException(
            status_code=503,
            detail="System-global Codex credential authority unavailable.",
        )

    session_id = secrets.token_urlsafe(16)
    session = CLIAuthSession(
        id=session_id,
        provider=provider_def,
        on_success=on_success,
    )
    store_session(session)

    await session.start()

    # Wait briefly for the device code to appear in stdout
    await session.wait(timeout=10.0)

    return CLIAuthStartResponse(
        session_id=session.id,
        state=CLIAuthSessionState(session.state),
        auth_url=session.auth_url,
        device_code=session.device_code,
        message=session.message,
    )


@router.get(
    "/sessions/{session_id}",
    response_model=CLIAuthSessionResponse,
    summary="Poll CLI auth session status",
)
async def get_session_status(session_id: str) -> CLIAuthSessionResponse:
    """Check the current state of a CLI auth session."""
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")

    return CLIAuthSessionResponse(
        session_id=session.id,
        state=CLIAuthSessionState(session.state),
        auth_url=session.auth_url,
        device_code=session.device_code,
        message=session.message,
        provider=session.provider.name,
    )


@router.delete(
    "/sessions/{session_id}",
    summary="Cancel a CLI auth session",
)
async def cancel_session(session_id: str) -> dict[str, str]:
    """Terminate a running CLI auth session."""
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")

    await session.kill()
    return {"status": "cancelled", "session_id": session_id}


# ---------------------------------------------------------------------------
# API-key provider endpoints
# ---------------------------------------------------------------------------


@router.put(
    "/{provider}/api-key",
    response_model=CLIAuthApiKeyResponse,
    summary="Store an API key for an api_key-mode provider",
)
async def save_api_key(
    provider: str,
    body: CLIAuthApiKeyRequest,
    db_manager: Any = Depends(_get_db_manager),
) -> CLIAuthApiKeyResponse:
    """Save an API key: write to the CLI's auth file and persist to DB."""
    import json

    provider_def = PROVIDERS.get(provider)
    if provider_def is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    if provider_def.auth_mode != "api_key":
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' uses {provider_def.auth_mode} mode, not api_key.",
        )

    # Write the key into the CLI's auth.json so the binary can use it
    if provider_def.token_path is not None:
        try:
            provider_def.token_path.parent.mkdir(parents=True, exist_ok=True)
            # Merge into existing auth.json (other providers may have entries)
            existing: dict = {}
            if provider_def.token_path.exists():
                try:
                    existing = json.loads(provider_def.token_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            # OpenCode Go stores API keys as {"type": "api", "key": "..."}
            existing["opencode-go"] = {"type": "api", "key": body.api_key}
            provider_def.token_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
            provider_def.token_path.chmod(0o600)
            logger.info("CLI auth: wrote API key to %s", provider_def.token_path)
        except OSError:
            logger.exception("CLI auth: failed to write auth file for %s", provider_def.name)
            raise HTTPException(status_code=500, detail="Failed to write auth file.")

    # Also persist to DB for K8s restarts
    store = _make_credential_store(db_manager)
    if store is not None:
        # Persist the entire auth.json (contains all providers' creds)
        if provider_def.token_path is not None and provider_def.token_path.exists():
            from butlers.cli_auth.persistence import persist_token

            await persist_token(
                provider_def,
                store,
                codex_authority=store if provider_def.name == "codex" else None,
            )

    logger.info("CLI auth: stored API key for %s", provider_def.name)
    return CLIAuthApiKeyResponse(
        provider=provider_def.name,
        stored=True,
        message=f"API key saved for {provider_def.display_name}.",
    )


@router.delete(
    "/{provider}/api-key",
    summary="Delete a stored API key for an api_key-mode provider",
)
async def delete_api_key(
    provider: str,
    db_manager: Any = Depends(_get_db_manager),
) -> dict[str, str]:
    """Remove an API key from the auth file and credential store."""
    import json

    provider_def = PROVIDERS.get(provider)
    if provider_def is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    if provider_def.auth_mode != "api_key":
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' uses {provider_def.auth_mode} mode, not api_key.",
        )

    # Remove from the CLI's auth.json
    if provider_def.token_path is not None and provider_def.token_path.exists():
        try:
            existing = json.loads(provider_def.token_path.read_text(encoding="utf-8"))
            if "opencode-go" in existing:
                del existing["opencode-go"]
                provider_def.token_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        except (json.JSONDecodeError, OSError):
            logger.exception("CLI auth: failed to update auth file for %s", provider_def.name)

    # Remove from DB
    store = _make_credential_store(db_manager)
    if store is not None:
        key = f"cli-auth/{provider_def.name}"
        await store.delete(key)

    logger.info("CLI auth: deleted API key for %s", provider_def.name)
    return {"status": "deleted", "provider": provider_def.name}


async def _persist_test_outcome(
    db_manager: Any,
    provider_name: str,
    *,
    ok: bool,
    detail: str | None,
    latency_ms: int,
    credential_store: CredentialStore | None = None,
    expected_store_value: str | None = None,
    fence_to_codex_authority: bool = False,
) -> None:
    """Persist a CLI credential test outcome so it survives page refresh.

    Mirrors what the user/system probe endpoints record (the passport's
    "probe · last test" panel and needs-hand state read exclusively from
    these stores — a test that only returns its result in the HTTP response
    is invisible after refresh):

    1. One row in ``public.secret_probe_log`` (scope ``cli``, key
       ``cli-auth/<provider>``) — feeds the "last test" display.
    2. Test-state cache columns on the ``butler_secrets`` row — feeds the
       inventory state / needs-hand bucket.  Codex uses a value-fenced writer
       because its asynchronous probe can finish after a dashboard refresh.
    3. A ``verified``/``failed`` audit stamp — feeds the stamps timeline.

    For Codex, those three writes share the value-fenced credential-row
    transaction.  A later dashboard replacement therefore waits for this
    probe's complete historical record, then atomically resets the current
    credential's health state; a stale probe cannot appear after the
    replacement as if it tested the new credential.

    Every step is best-effort: a persistence failure must never mask the
    test result itself.
    """
    key = f"cli-auth/{provider_name}"
    store = credential_store or _make_credential_store(db_manager)
    if store is None:
        return
    message = detail[:512] if detail else None

    if fence_to_codex_authority:
        # A Codex probe may have begun before an owner refresh.  Never attach
        # its result to an absent, unreadable, or replaced authority.  The
        # caller separately verifies that the canonical auth file matched this
        # snapshot immediately before and after the probe command.
        if expected_store_value is None:
            logger.info("CLI auth test: skipped unfenced Codex outcome for %s", key)
            return
        try:
            recorded = await _persist_codex_test_outcome_if_current(
                store,
                key=key,
                ok=ok,
                detail=detail,
                message=message,
                latency_ms=latency_ms,
                expected_store_value=expected_store_value,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "CLI auth test: fenced Codex outcome persistence failed for %s",
                key,
            )
            return
        if not recorded:
            logger.info("CLI auth test: skipped stale Codex outcome for %s", key)
            return
        return

    try:
        await store.pool.execute(
            """
            INSERT INTO public.secret_probe_log
                (credential_scope, credential_key, ok, code, message, latency_ms)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            "cli",
            key,
            ok,
            None,
            message,
            latency_ms,
        )
    except Exception:  # noqa: BLE001
        logger.warning("CLI auth test: probe_log write failed for %s", key)

    if not fence_to_codex_authority:
        try:
            await store.record_test_result(key, ok, message=None if ok else message)
        except Exception:  # noqa: BLE001
            logger.warning("CLI auth test: test-state cache write failed for %s", key)

    try:
        # Lazy import: secrets_v2 lazily imports from this module, so a
        # module-level import here would risk a cycle.
        from butlers.api.routers.secrets_v2 import _write_cli_audit

        note = f"Probe ok: {detail}" if ok else f"Probe failed: {detail or 'unknown error'}"
        await _write_cli_audit(
            store.pool,
            action="verified" if ok else "failed",
            credential_id=key,
            note=note,
        )
    except Exception:  # noqa: BLE001
        logger.warning("CLI auth test: audit write failed for %s", key)


async def _persist_codex_test_outcome_if_current(
    store: CredentialStore,
    *,
    key: str,
    ok: bool,
    detail: str | None,
    message: str | None,
    latency_ms: int,
    expected_store_value: str,
) -> bool:
    """Persist one Codex probe only while its credential bytes still win.

    The fenced health update locks the current credential row.  Keeping the
    probe-log and audit inserts in that same transaction prevents a Passport
    token save from landing between those durable records and making an old
    failure look newer than the replacement.  The caller passes the exact
    bytes that the canonical ``auth.json`` matched immediately before and
    after the external probe; this helper never logs those bytes.
    """
    pool = store.require_system_global_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            recorded = await store.record_codex_cli_auth_test_result_if_unchanged_on_connection(
                conn,
                ok=ok,
                message=None if ok else message,
                expected_value=expected_store_value,
            )
            if not recorded:
                return False

            await conn.execute(
                """
                INSERT INTO public.secret_probe_log
                    (credential_scope, credential_key, ok, code, message, latency_ms)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                "cli",
                key,
                ok,
                None,
                message,
                latency_ms,
            )

            # Lazy import: secrets_v2 lazily imports from this module, so a
            # module-level import here would risk a cycle.  Passing the
            # acquired connection makes the audit row commit with the health
            # result and probe log rather than after an authority replacement.
            from butlers.api.routers.secrets_v2 import _write_cli_audit

            note = f"Probe ok: {detail}" if ok else f"Probe failed: {detail or 'unknown error'}"
            await _write_cli_audit(
                conn,
                action="verified" if ok else "failed",
                credential_id=key,
                note=note,
            )

    return True


async def _prepare_codex_test_authority(
    provider_def: CLIAuthProviderDef,
    store: CredentialStore | None,
) -> tuple[str | None, Path | None]:
    """Reconcile and capture the canonical bytes a Codex probe will consume.

    A Passport token save updates the shared credential row immediately, but
    ``codex login status`` reads the local canonical file.  Reconcile first so
    the probe tests the dashboard authority, then bind the result to those
    exact bytes.  Failures preserve the HTTP probe response while withholding
    durable health state rather than guessing which credential was tested.
    """
    token_path = provider_def.token_path
    if store is None or token_path is None:
        return None, token_path

    try:
        from butlers.core.runtimes._codex_auth_sync import (
            codex_auth_file_matches_authority,
            reconcile_codex_auth,
        )

        baseline = await reconcile_codex_auth(token_path, store, butler_name="dashboard")
        expected_store_value = baseline.expected_store_value
        if (
            not baseline.authority_known
            or expected_store_value is None
            or not codex_auth_file_matches_authority(token_path, expected_store_value)
        ):
            logger.info("CLI auth test: Codex authority unavailable for %s", token_path)
            return None, token_path
        return expected_store_value, token_path
    except Exception:  # noqa: BLE001
        logger.warning("CLI auth test: Codex reconciliation failed before probe")
        return None, token_path


def _codex_test_authority_still_matches(
    token_path: Path | None, expected_store_value: str | None
) -> bool:
    """Return whether a Codex probe's canonical auth file stayed unchanged."""
    if token_path is None or expected_store_value is None:
        return False
    try:
        from butlers.core.runtimes._codex_auth_sync import codex_auth_file_matches_authority

        return codex_auth_file_matches_authority(token_path, expected_store_value)
    except Exception:  # noqa: BLE001
        logger.warning("CLI auth test: could not verify Codex auth after probe")
        return False


async def _finalize_codex_test_rotation(
    token_path: Path | None,
    store: CredentialStore | None,
    *,
    expected_store_value: str | None,
) -> None:
    """Persist a status-probe auth rotation without attaching its health result.

    ``codex login status`` is allowed to refresh the canonical auth document.
    Its probe result describes the credential observed during that command, not
    necessarily the rotated successor, so only the operation-bound CAS is
    durable here. A concurrent Passport replacement wins that CAS and is
    reconciled by the shared finalizer; neither outcome gets a probe log,
    audit record, or health state from the older command.
    """
    if token_path is None or store is None or expected_store_value is None:
        return

    try:
        from butlers.core.runtimes._codex_auth_sync import finalize_codex_auth_rotation

        await finalize_codex_auth_rotation(
            token_path,
            store,
            expected_store_value=expected_store_value,
            authority_known=True,
            butler_name="dashboard",
        )
    except Exception:  # noqa: BLE001
        # This is best-effort durability only. Do not include exception detail:
        # database drivers can retain credential-bearing bind context.
        logger.warning("CLI auth test: Codex post-probe auth finalization failed")


@router.post(
    "/{provider}/test",
    response_model=CLIAuthTestResponse,
    summary="Test an API key by running the provider's test command",
)
async def test_api_key(
    provider: str,
    db_manager: Any = Depends(_get_db_manager),
) -> CLIAuthTestResponse:
    """Validate a provider's stored credential.

    For ``api_key`` providers this runs the provider's configured test command.
    For ``device_code`` providers (e.g. Codex) there is no API key to test
    against — instead we run the live health probe, which executes the
    provider's status command and (for Codex) validates the stored token
    against the backend. The frontend "probe" button calls this endpoint for
    every auth mode, so device_code providers must return a result rather than
    a 400.

    The outcome is persisted (probe log, test-state cache, audit stamp) so the
    passport's "last test" survives a page refresh — see
    ``_persist_test_outcome``.
    """
    provider_def = PROVIDERS.get(provider)
    if provider_def is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    store = _make_credential_store(db_manager)
    expected_store_value: str | None = None
    token_path: Path | None = None
    if provider_def.name == "codex":
        expected_store_value, token_path = await _prepare_codex_test_authority(provider_def, store)

    started = time.monotonic()
    result = await _run_provider_test(provider_def, db_manager, credential_store=store)
    latency_ms = round((time.monotonic() - started) * 1000)
    if provider_def.name == "codex" and not _codex_test_authority_still_matches(
        token_path,
        expected_store_value,
    ):
        # A Codex status probe can itself rotate auth. Finalize that successor
        # against the exact pre-probe authority snapshot, then deliberately
        # withhold this probe outcome because it may describe the old bytes.
        await _finalize_codex_test_rotation(
            token_path,
            store,
            expected_store_value=expected_store_value,
        )
        expected_store_value = None
    await _persist_test_outcome(
        db_manager,
        provider_def.name,
        ok=result.success,
        detail=result.detail,
        latency_ms=latency_ms,
        credential_store=store,
        expected_store_value=expected_store_value,
        fence_to_codex_authority=provider_def.name == "codex",
    )
    return result


async def _run_provider_test(
    provider_def: CLIAuthProviderDef,
    db_manager: Any,
    *,
    credential_store: CredentialStore | None = None,
) -> CLIAuthTestResponse:
    """Run the live credential check for a provider and return the outcome."""
    if provider_def.auth_mode != "api_key":
        # device_code (and any non-api_key) provider: probe live auth health.
        store = credential_store or _make_credential_store(db_manager)
        health = await probe_provider(
            provider_def,
            store,
            codex_authority=store if provider_def.name == "codex" else None,
        )
        return CLIAuthTestResponse(
            provider=provider_def.name,
            success=health.state == AuthHealthState.authenticated,
            detail=health.detail or health.state.value,
        )

    if not provider_def.test_command:
        raise HTTPException(
            status_code=400, detail=f"No test command configured for {provider_def.name}."
        )

    # The API key is in the CLI's auth.json — just run the test command
    from butlers.cli_auth.session import _strip_ansi

    try:
        test_command = list(provider_def.test_command)
        if provider_def.name == "opencode-go":
            model_index = test_command.index("--model")
            execution_model = canonical_to_execution_model(test_command[model_index + 1])
            if execution_model is not None:
                test_command[model_index + 1] = execution_model
        proc = await asyncio.create_subprocess_exec(
            *test_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        raw_stdout, raw_stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        stdout_text = _strip_ansi(raw_stdout.decode(errors="replace")).strip()
        stderr_text = _strip_ansi(raw_stderr.decode(errors="replace")).strip()
        # Prefer stdout for pattern matching; include stderr in detail for diagnostics
        output = stdout_text[:200]
        if not output and stderr_text:
            output = stderr_text[:200]

        if proc.returncode == 0:
            if provider_def.test_ok_pattern and provider_def.test_ok_pattern.search(output):
                return CLIAuthTestResponse(
                    provider=provider_def.name,
                    success=True,
                    detail=output or "Test passed.",
                )
            elif not provider_def.test_ok_pattern:
                return CLIAuthTestResponse(
                    provider=provider_def.name,
                    success=True,
                    detail=output or "Command succeeded (exit 0).",
                )
            else:
                return CLIAuthTestResponse(
                    provider=provider_def.name,
                    success=False,
                    detail=output or "Command succeeded but output didn't match expected pattern.",
                )

        return CLIAuthTestResponse(
            provider=provider_def.name,
            success=False,
            detail=output or f"Exit code {proc.returncode}.",
        )
    except TimeoutError:
        return CLIAuthTestResponse(
            provider=provider_def.name,
            success=False,
            detail="Test command timed out.",
        )
    except Exception:
        logger.exception("CLI auth test failed for %s", provider_def.name)
        return CLIAuthTestResponse(
            provider=provider_def.name,
            success=False,
            detail="Test command failed to execute.",
        )
