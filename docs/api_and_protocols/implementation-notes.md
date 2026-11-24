# API and Security Implementation Notes

> **Purpose:** Hard-won traps for dashboard API routes, token handling and secret-file reads.
> **Audience:** Developers changing `src/butlers/api/`, OAuth refresh code or logging setup.
> **Prerequisites:** [Dashboard API](dashboard-api.md), [Response conventions](response-conventions.md).

Moved verbatim from `AGENTS.md` (API and security contracts).

- Actor attribution is server-derived, never caller-asserted. Any route that persists or audits an
  actor takes it from `authenticated_principal()` (`src/butlers/api/audit_emit.py`); a request
  field such as `actor: str = "dashboard"` is a forgery hole. To retire such a field on an
  `extra="forbid"` model without 422ing old clients, base the model on `IgnoresCallerAssertedActor`
  (same module). `tests/api/test_actor_attribution_sweep.py` guards every route; extend its
  allow-lists only with a recorded justification. `X-Butlers-Decision-Actor` on approvals is
  server-verified and is not this defect.
- Approval dispatch probes the selected MCP client with a read-only ping before invoking the
  stored action. A closed or broken session may be conditionally evicted and ping retried once,
  within the existing acquisition deadline. Eviction compares the observed client identity so a
  concurrent healthy replacement survives, and closing the failed client is bounded. The action
  itself is called once: closure after the ping returns the truthful dispatch failure and never
  replays the action automatically. A later explicit owner Retry can acquire a fresh client.
- Enumerating API routes: FastAPI mounts included routers lazily, so `create_app().routes` holds
  `_IncludedRouter` objects. Recurse into `route.original_router.routes`; the body model is
  `route.body_field.field_info.annotation`.
- A dashboard disconnect watcher inside nested `BaseHTTPMiddleware` needs AnyIO cancellation: a
  one-shot `Task.cancel()` then awaiting the watcher can deadlock response delivery.
  `IngestionReadBudgetRoute` uses a watcher-owned `CancelScope`.
- Spotify connector and module tools share `butlers.spotify_client.SpotifyClient`.
  Keep token-response validation and secured owner-row persistence in that client;
  connector lifecycle classification and metrics adapt typed, allowlisted failures.
  A resource 401 after refresh is degraded, rather than proof of token revocation.

- A 200 from a token endpoint is not a validated payload: `int(data.get("expires_in", 3600))`
  accepts strings, bools, floats and negatives. Validate before assigning, as
  `spotify_credentials.parse_spotify_token_response` does.
- Infrastructure identity-version provenance is opt-in: producers pass
  `Observation(identity_version=...)` and, on the first higher-version successor, the explicit
  `predecessor_fingerprint`. Every resolution reason lives in top-level
  `metadata.resolution_reason`; producers may not set `RESOLUTION_METADATA_KEYS`. Never infer
  lineage from fingerprints or rewrite historic rows.
- Read secret files with `os.open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)` then `fstat` +
  `S_ISREG`: without `O_NONBLOCK` a FIFO planted at the mount path blocks forever (a hung pytest
  run, not a failure). Mode `0400` is not isolation from a same-identity child process; say so
  wherever it is checked.
- `src/butlers/core/logging.py` pins `httpx` and `httpcore` to `WARNING`: at INFO they log full
  request URLs, which carry Telegram bot tokens and OAuth query parameters. Keep that pin on any new
  logging setup.
- `CredentialRedactionFilter` (Telegram `/bot<token>/`, `Bearer`, `?key=`-style query parameters)
  sits on every handler `configure_logging` installs, never on the root logger: a logger's own
  filters only see records logged directly to it, so a root filter never sees records propagated
  from `httpx` or `butlers.*`. It scrubs the message; the formatters' `redact_exception` processor
  applies the same rules to `exc_info` tracebacks (e.g. `httpx.HTTPStatusError` URLs) and stack
  text. Patterns outside those rules still pass through tracebacks, so errors that can carry
  unpublished or private text (QA publication pre-egress failures) log the exception type only.
