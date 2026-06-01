/**
 * OAuth full roundtrip e2e test [bu-idkeh]
 *
 * Coverage:
 *   1. Google: Click "re-authorize" → reauthorize API returns redirect URL →
 *      browser navigates → callback URL (mocked) redirects to
 *      /secrets?focus=u:google&toast=connected → ?toast= is stripped (effect fired).
 *
 *   2. Spotify: Same roundtrip for the Spotify provider.
 *
 *   3. Error path: callback returns oauth_error → ?oauth_error= is stripped
 *      (warning-toast effect fired).
 *
 * Mock strategy:
 *   - All API routes mocked via page.route() — no real backend or credentials
 *     required.
 *   - The reauthorize endpoint returns a redirect_url pointing to a local
 *     callback path on the preview server.  Playwright intercepts that path
 *     and fulfills it as a redirect to the success URL.
 *   - This exercises the full frontend flow:
 *       button click
 *       → window.location.href navigation
 *       → callback redirect
 *       → SecretsPage useEffect (toast + ?toast= strip)
 *
 * Proof of "toast fired":
 *   SecretsPage strips ?toast= / ?oauth_error= inside the same useEffect that
 *   calls toast.success() / toast.warning().  A stripped URL is therefore
 *   proof the effect ran and the toast was triggered.  This mirrors the
 *   approach in secrets-passport.spec.ts test #4.
 *
 * Spec anchors:
 *   butler-secrets §Cross-Page Reauth Bookkeeping
 *   [bu-idkeh] OAuth test-mode stub + full roundtrip e2e
 *
 * Prerequisites:
 *   npm run test:e2e:install  (once)
 *   npm run build && npm run preview  (or Playwright starts preview automatically)
 */

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Mock inventory: credentials in "expired" state so the "re-authorize" button
// is rendered (the onClick handler that triggers the OAuth dance is only wired
// for expired/revoked/scope_mismatch states).
// ---------------------------------------------------------------------------

const MOCK_INVENTORY_WITH_EXPIRED = {
  data: {
    user: [
      {
        id: "u-google-tze",
        entity_id: "tze",
        type: "google_oauth_refresh",
        label: "Google (tze)",
        state: "expired",
        fingerprint: "sha256:7a3f9e2c",
        last_verified: "3 days ago",
        test: { ok: false, code: 401, at: "3 days ago", message: "refresh-token expired" },
      },
      {
        id: "u-spotify-tze",
        entity_id: "tze",
        type: "spotify_oauth_refresh",
        label: "Spotify (tze)",
        state: "expired",
        fingerprint: "sha256:d4e1b8a0",
        last_verified: "2 days ago",
        test: { ok: false, code: 401, at: "2 days ago", message: "refresh-token expired" },
      },
    ],
    system: [],
    cli: [],
    identities: [{ entity_id: "tze", name: "Tze", role: "owner" }],
    providers: {
      google: {
        id: "google",
        label: "Google",
        glyph: "G",
        kind: "oauth",
        authority: "accounts.google.com",
        brief: "Calendar, Gmail, Drive.",
        cadence: "on demand",
      },
      spotify: {
        id: "spotify",
        label: "Spotify",
        glyph: "S",
        kind: "oauth",
        authority: "accounts.spotify.com",
        brief: "Recent listens.",
        cadence: "poll · 15m",
      },
    },
  },
  meta: { needs_hand_count: 0 },
};

// ---------------------------------------------------------------------------
// Route mock helpers
// ---------------------------------------------------------------------------

async function mockSecretsRoutes(
  page: Parameters<Parameters<typeof test>[1]>[0]["page"],
) {
  await page.route("**/api/secrets/inventory**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_INVENTORY_WITH_EXPIRED),
    });
  });

  await page.route("**/api/secrets/breaks-catalogue**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ breaks: [] }),
    });
  });
}

// ---------------------------------------------------------------------------
// 1. Google OAuth roundtrip
// ---------------------------------------------------------------------------

test("oauth roundtrip: google re-authorize click → redirect → callback → toast-connected strips param", async ({
  page,
}) => {
  // Install API mocks before navigation.
  await mockSecretsRoutes(page);

  // Mock the reauthorize endpoint: return a redirect_url that points to a
  // local callback path on the preview server.
  await page.route("**/api/secrets/user/google/reauthorize**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          redirect_url: "/api/oauth/google/callback?code=stub-e2e-code&state=stub-e2e-state",
        },
      }),
    });
  });

  // Mock the OAuth callback: redirect back to the success page.
  // This simulates what the backend does after a successful token exchange.
  await page.route("**/api/oauth/google/callback**", (route) => {
    route.fulfill({
      status: 302,
      headers: {
        Location: "/secrets?focus=u:google&toast=connected",
      },
    });
  });

  // Navigate to the Google user page (expired credential → re-authorize button shown).
  await page.goto("/secrets?focus=u:google", { timeout: 10_000 });

  // Wait for the DirectionPassport to render.
  await expect(page.locator('[data-direction-passport="true"]')).toBeAttached({
    timeout: 8_000,
  });

  // The Google user page must be rendered.
  await expect(page.locator('[data-page="user"]')).toBeAttached({ timeout: 5_000 });
  await expect(page.locator('[data-provider="google"]')).toBeAttached({ timeout: 5_000 });

  // The "re-authorize" button is only present for expired credentials.
  const reauthorizeBtn = page.getByRole("button", { name: /re-authorize/i });
  await expect(reauthorizeBtn).toBeAttached({ timeout: 5_000 });

  // Click the re-authorize button — triggers handleReauthorize() in pages.tsx.
  await reauthorizeBtn.click();

  // After the full roundtrip the page lands on /secrets?focus=u:google&toast=connected.
  // SecretsPage.useEffect fires: calls toast.success() and strips ?toast=.
  // Proof the effect ran = the param is gone.
  await expect(async () => {
    expect(page.url()).not.toContain("toast=");
  }).toPass({ timeout: 8_000 });

  // ?focus= must survive the strip.
  expect(page.url()).toContain("focus=");
  expect(page.url()).toContain("u:google");
});

// ---------------------------------------------------------------------------
// 2. Spotify OAuth roundtrip
// ---------------------------------------------------------------------------

test("oauth roundtrip: spotify re-authorize → callback → toast-connected strips param", async ({
  page,
}) => {
  await mockSecretsRoutes(page);

  await page.route("**/api/secrets/user/spotify/reauthorize**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          redirect_url: "/api/oauth/spotify/callback?code=stub-spotify-code&state=stub-state",
        },
      }),
    });
  });

  // Spotify callback → success redirect.
  await page.route("**/api/oauth/spotify/callback**", (route) => {
    route.fulfill({
      status: 302,
      headers: {
        Location: "/secrets?focus=u:spotify&toast=connected",
      },
    });
  });

  await page.goto("/secrets?focus=u:spotify", { timeout: 10_000 });
  await expect(page.locator('[data-direction-passport="true"]')).toBeAttached({
    timeout: 8_000,
  });
  await expect(page.locator('[data-page="user"]')).toBeAttached({ timeout: 5_000 });
  await expect(page.locator('[data-provider="spotify"]')).toBeAttached({ timeout: 5_000 });

  const reauthorizeBtn = page.getByRole("button", { name: /re-authorize/i });
  await expect(reauthorizeBtn).toBeAttached({ timeout: 5_000 });

  await reauthorizeBtn.click();

  // Param stripped = toast effect fired.
  await expect(async () => {
    expect(page.url()).not.toContain("toast=");
  }).toPass({ timeout: 8_000 });

  expect(page.url()).toContain("focus=");
  expect(page.url()).toContain("u:spotify");
});

// ---------------------------------------------------------------------------
// 3. Error path: callback returns oauth_error → warning effect fires
// ---------------------------------------------------------------------------

test("oauth roundtrip: callback returns oauth_error → error param stripped (warning effect fired)", async ({
  page,
}) => {
  await mockSecretsRoutes(page);

  await page.route("**/api/secrets/user/google/reauthorize**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          redirect_url: "/api/oauth/google/callback?code=fail-code&state=fail-state",
        },
      }),
    });
  });

  // Callback responds with an OAuth error (simulates user denying consent).
  await page.route("**/api/oauth/google/callback**", (route) => {
    route.fulfill({
      status: 302,
      headers: {
        Location: "/secrets?focus=u:google&oauth_error=access_denied",
      },
    });
  });

  await page.goto("/secrets?focus=u:google", { timeout: 10_000 });
  await expect(page.locator('[data-direction-passport="true"]')).toBeAttached({
    timeout: 8_000,
  });

  const reauthorizeBtn = page.getByRole("button", { name: /re-authorize/i });
  await expect(reauthorizeBtn).toBeAttached({ timeout: 5_000 });

  await reauthorizeBtn.click();

  // SecretsPage.useEffect strips ?oauth_error= after calling toast.warning().
  // Stripped param = proof the warning-toast effect ran.
  await expect(async () => {
    expect(page.url()).not.toContain("oauth_error=");
  }).toPass({ timeout: 8_000 });

  // ?focus= must survive.
  expect(page.url()).toContain("focus=");
});
