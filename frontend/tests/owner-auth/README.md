# Isolated owner-authentication browser proof

This suite drives the shipped frontend and Chromium's native WebAuthn API against
real owner-auth services and isolated PostgreSQL. It does not use the ordinary
E2E API mock, an owner vault, a deployed instance, or Tailscale Serve.

Prerequisites: a built `frontend/dist`, the installed Playwright Chromium, and
`openssl` on PATH. The feature integration harness starts
`tests/api/owner_auth_browser_server.py:create_test_app` with its guarded
`test_owner_auth_browser` database, fresh keyless state, and canonical identity
`https://butlers.example.test` / `butlers.example.test`. Its administrative host
helper is `tests/api/owner_auth_browser_host.py`.

From the repository root, run the complete disposable harness:

```sh
uv run --no-sync python scripts/test_owner_auth_browser.py
```

It creates a fresh container, provisions a separate restricted runtime login,
starts the real authentication service without the production lifespan, verifies
both passkey mode and a host-reconciled configured-key mode, and
passes only synthetic database credentials to its children. All resources are
removed after the test. It does not inherit an ambient database URL.

For an already-running synthetic harness, from `frontend/` with its isolated
configuration environment:

```sh
OWNER_AUTH_TEST_ISOLATED=1 \
OWNER_AUTH_TEST_API_URL=http://127.0.0.1:18181 \
OWNER_AUTH_TEST_HOST_COMMAND='["/absolute/checkout/.venv/bin/python","/absolute/checkout/tests/api/owner_auth_browser_host.py"]' \
npm run test:owner-auth -- --grep "native passkey"
```

The API URL must have an explicit loopback HTTP port. The helper refuses other
database names. The Node fixture generates a one-day synthetic certificate in a
temporary directory, opens only an ephemeral loopback TLS listener, and removes
both on exit. Chromium's host resolver maps the canonical test hostname and port
443 to that listener; the browser still sees an actual HTTPS secure context with
the exact canonical origin. The test proxy strips incoming forwarding headers
and supplies the fixture's trusted metadata. No privileged listener, DNS change,
proxy activation, or real certificate is involved.

The test covers registration after host approval, synced-credential login in a
new browser, native cancellation, reload, logout, expiry, global revocation,
replacement recovery, retired-credential denial, cookie flags and CSRF denial.
Actual WebSocket and SSE connections plus a pending React query remain open
before expiry; without reloading, expiry closes the streams, aborts the query,
and unmounts the protected shell. Browser-storage value checks include live
synthetic cookie/CSRF/credential sentinels and a positive capture control.
Domain-page Vitest/ordinary E2E fixtures explicitly model an already-established
synthetic owner session; they are not authentication evidence.

Auth tests disable trace, screenshots, video, and Playwright's automatic
accessibility error snapshot. Failures retain only a closed phase label; CLI
errors never print arguments. Synthetic private authenticator keys and ceremony
values remain in memory. Browser support and provider sync here are synthetic
proof, not evidence that the owner's Bitwarden vault was accessed or verified.
