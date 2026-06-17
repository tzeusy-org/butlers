## 1. Remove the legacy raw-secret reveal endpoint (Finding A)

- [ ] 1.1 Delete the `reveal_secret` handler and its `GET /{name}/secrets/{key}/reveal` route from `src/butlers/api/routers/secrets.py:158-176` (the legacy butler-scoped router), including the now-dead "Reveal endpoint" section comment.
- [ ] 1.2 Sweep the legacy `/api/butlers/{name}/secrets/...` router for any other route that returns plaintext secret material in a response body; confirm none remain (only metadata/`is_set`/masked surfaces are permitted).
- [ ] 1.3 Remove or update any frontend caller / API client that referenced the removed reveal path so it no longer issues `GET /api/butlers/{name}/secrets/{key}/reveal`.

## 2. Route-introspection contract test (Finding A invariant)

- [ ] 2.1 Add a contract test that enumerates the constructed app's mounted route table (FastAPI/Starlette routes) and asserts NO route matches the legacy raw-secret-reveal pattern `GET /api/butlers/{name}/secrets/{key}/reveal`.
- [ ] 2.2 Extend the test to assert no route on the legacy `/api/butlers/{name}/secrets/...` router returns plaintext secret material (assert against the mounted route table, not a single hard-coded string, so reintroduction is caught).
- [ ] 2.3 Add an integration assertion that `GET /api/butlers/{name}/secrets/{key}/reveal` returns HTTP 404 for representative inputs.

## 3. Defense-in-depth API-key auth posture (Finding B)

- [ ] 3.1 Confirm `ApiKeyMiddleware` (`src/butlers/api/middleware.py`) and `create_app()` (`src/butlers/api/app.py:245-256`) keep current behavior: enforce on `/api/*` except `/api/health` and `/health` when `DASHBOARD_API_KEY` is set; no-op pass-through when unset; constant-time compare. Do NOT add fail-closed startup.
- [ ] 3.2 Add/keep tests covering: 401 on missing/invalid `X-API-Key` when key is set; pass-through with correct key; health paths bypass; no-op + successful startup when key unset.

## 4. Honest auth-status health indicator (Finding B + C)

- [ ] 4.1 Add a backend health/auth-status field that reports posture booleans only: `api_key_auth_enabled` (DASHBOARD_API_KEY set?) and `export_secret_insecure_default` (export secret falling back to the insecure default?). Reveal no secret values.
- [ ] 4.2 Wire the indicator into the dashboard health surface and add a UI affordance so the operator can see auth-enabled/disabled and insecure-export-default posture honestly.
- [ ] 4.3 Add tests asserting the indicator reports disabled/enabled correctly and reports the insecure-export-default state correctly, and that no secret value is ever included in the payload.

## 5. Export signer hardening (Finding C)

- [ ] 5.1 Change `_sign_token` (`src/butlers/api/routers/data_ops.py:101-104`) so it does NOT fall back to the literal `"dev-secret"`; require an explicitly configured `DASHBOARD_EXPORT_SECRET` (or an explicit dev context) and refuse to mint/verify export tokens otherwise.
- [ ] 5.2 Update the startup check in `app.py:117-120` to reflect the new contract (surface the insecure-default posture rather than warn-and-proceed with a forgeable default), feeding the auth-status indicator from task 4.1.
- [ ] 5.3 Add tests: export refuses to operate when the export secret is unset outside dev; signs/verifies normally with an explicitly configured secret; no token signed with `"dev-secret"` is ever issued.

## 6. Doctrine alignment + quality gates

- [ ] 6.1 Confirm changes align with `about/heart-and-soul/security.md` (no credentials in dashboard payloads; localhost+Tailscale primary boundary) and `about/craft-and-care/security-and-secrets.md` (no raw-secret logging; no casual env-var fallbacks).
- [ ] 6.2 Run quality gates: `uv run ruff check`, `uv run ruff format --check`, and the targeted pytest scope for `tests/` covering secrets/middleware/data_ops/health, then the relevant frontend lint/test for any UI changes.
- [ ] 6.3 Run `openspec validate harden-secrets-and-dashboard-honesty --strict` and confirm the change is apply-ready.
