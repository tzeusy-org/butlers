## Why

A code review of the dashboard administrative gateway surfaced three credential-handling honesty gaps that contradict the project's security doctrine: a legacy endpoint that returns raw secret material over an unguarded GET, a defense-in-depth API-key layer whose enabled/disabled posture is invisible to the operator, and an export-token signer that silently falls back to a known-public `"dev-secret"`. Each violates the single-owner threat model (protect the owner's credentials) and the determinism contract (the daemon must be honest and predictable). These are remediable now with a route-introspection invariant, an honest auth-status indicator, and removal of the insecure default.

## What Changes

- **BREAKING** Remove the legacy raw-secret reveal route `GET /api/butlers/{name}/secrets/{key}/reveal` (`secrets.py:158-176`) which returns `{"key", "value"}` with the plaintext secret. Value reveal, where supported, is governed exclusively by the `secrets_v2`/passport surface per the `butler-secrets` spec.
- Add a universal, negative invariant: **no mounted API route may return raw secret material via the legacy `/api/butlers/{name}/secrets/...` router**, enforced by a route-introspection contract test that enumerates mounted routes and FAILS if any legacy raw-secret-reveal GET is mounted.
- Specify `ApiKeyMiddleware` as a **defense-in-depth, opt-in** control: when `DASHBOARD_API_KEY` is set it SHALL enforce on all `/api/*` routes except health; when unset it remains a no-op. Startup is NOT fail-closed — network isolation (localhost binding + Tailscale) is the primary control.
- Add an **auth-status health indicator** surfaced to the operator: whether API-key auth is enabled/disabled, and whether the export secret is using the insecure default.
- Harden the data-ops export signer: outside an explicit dev context the `"dev-secret"` fallback SHALL NOT be used; the export surface SHALL refuse to operate with the known-insecure default (`data_ops.py:101-104`, `app.py:117-120`).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `dashboard-admin-gateway`: add a no-raw-secret-reveal route invariant (with introspection contract test) and remove the legacy reveal endpoint from the contract; specify opt-in defense-in-depth API-key auth (not fail-closed) plus an honest auth-status health indicator; require the export signer to refuse the insecure `"dev-secret"` default outside dev.

## Impact

- **Specs:** `openspec/specs/dashboard-admin-gateway/spec.md` (delta). Stays consistent with `openspec/specs/butler-secrets/spec.md`, which already deprecated the eye-toggle reveal surface and governs explicit reveal under the passport page contract.
- **Code (implementation, out of scope here but driven by tasks):** `src/butlers/api/routers/secrets.py` (remove reveal route), `src/butlers/api/routers/data_ops.py` + `src/butlers/api/app.py` (export-secret hardening), `src/butlers/api/middleware.py` (auth-status surfacing), a new dashboard health/auth-status field and its consuming UI.
- **Doctrine:** aligns the gateway with `about/heart-and-soul/security.md` (credentials never in dashboard payloads; localhost+Tailscale primary boundary) and `about/craft-and-care/security-and-secrets.md` (no raw-secret logging; no casual env-var fallbacks).
- **Behavioral break:** any caller of `GET /api/butlers/{name}/secrets/{key}/reveal` will receive 404 after removal; no replacement is provided on the legacy router (reveal lives on the passport surface).
