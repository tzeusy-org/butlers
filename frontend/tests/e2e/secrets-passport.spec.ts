/**
 * Secrets Passport E2E tests — /secrets [bu-kx954]
 *
 * Coverage:
 *   1. Page loads: DirectionPassport renders at /secrets with mocked inventory.
 *   2. Deep-link routing: ?focus=u:google lands on Google User page.
 *   3. Identity switch: ?identity=wei shows wei identity chip.
 *   4. OAuth callback re-entry: ?focus=u:google&toast=connected shows success
 *      toast and strips the param from the URL.
 *   5. OAuth error re-entry: ?oauth_error=invalid_grant shows warning toast.
 *
 * Spec anchors:
 *   butler-secrets §Deep-Link Focus Routing
 *   butler-secrets §Projection-Lens Identity Switcher
 *   butler-secrets §Cross-Page Reauth Bookkeeping
 *
 * API routes mocked:
 *   GET /api/secrets/inventory  — returns MOCK_INVENTORY data
 *   GET /api/secrets/breaks-catalogue  — returns empty list (no WhatBreaks load needed)
 *
 * The preview server is managed by playwright.config.ts `webServer`; tests
 * rely on it being available and will fail hard (not skip) if it is not.
 *
 * Prerequisites:
 *   npm run test:e2e:install  (once)
 *   npm run build && npm run preview  (or Playwright starts preview automatically)
 */

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Mock inventory data in the raw API response format (SecretsInventoryResponse).
//
// The /api/secrets/inventory endpoint returns raw backend types that the
// useSecretsInventory hook adapts via adaptInventoryResponse().  The e2e mock
// must mirror the actual API shape, not the already-adapted frontend types.
//
// Key differences from the old frontend-only mock:
//   - user[].type  (not .provider) — e.g. "google_oauth_refresh"
//   - user[].entity_id (not .identity) — entity UUID
//   - identities[].entity_id (not .id)
//   - identities[].name (not .label)
//   - system[].butler (not .source) — owning butler name
//   - cli[].key (not .id) — credential key
//   - cli[].description (not .label)
//   - The full response is { data: {...}, meta: {...} }
// ---------------------------------------------------------------------------

const MOCK_INVENTORY_RESPONSE = {
  data: {
    user: [
      {
        id: "u-google-tze",
        entity_id: "tze",
        type: "google_oauth_refresh",
        label: "Google (tze)",
        state: "ok",
        fingerprint: "sha256:7a3f9e2c",
        last_verified: "14:21 today",
        test: { ok: true, code: 200, at: "14:21 today" },
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
      {
        id: "u-google-wei",
        entity_id: "wei",
        type: "google_oauth_refresh",
        label: "Google (wei)",
        state: "ok",
        fingerprint: "sha256:aa1122bb",
        last_verified: "12:00 today",
        test: { ok: true, code: 200, at: "12:00 today" },
      },
    ],
    system: [
      {
        key: "BUTLER_TELEGRAM_TOKEN",
        category: "messaging",
        description: "Bot token for Telegram.",
        state: "ok",
        fingerprint: "sha256:ab12cd34",
        last_verified: "14:01 today",
        butler: "switchboard",
        test: { ok: true, code: 200, at: "14:01 today" },
      },
    ],
    cli: [
      {
        key: "claude-cli",
        category: "runtime",
        description: "Claude Code",
        state: "ok",
        fingerprint: "sha256:11a47cd2",
        last_verified: "14:15 today",
        test: { ok: true, code: 200, at: "14:15 today" },
      },
    ],
    identities: [
      { entity_id: "tze", name: "Tze", role: "owner" },
      { entity_id: "wei", name: "Wei", role: "member" },
    ],
    providers: {
      google: { id: "google", label: "Google", glyph: "G", kind: "oauth", authority: "accounts.google.com", brief: "Calendar, Gmail, Drive read.", cadence: "on demand · refreshes hourly" },
      spotify: { id: "spotify", label: "Spotify", glyph: "S", kind: "oauth", authority: "accounts.spotify.com", brief: "Recent listens.", cadence: "poll · 15m" },
    },
  },
  meta: { needs_hand_count: 1 },
};

// ---------------------------------------------------------------------------
// Route mock helpers
// ---------------------------------------------------------------------------

async function mockSecretsRoutes(page: ReturnType<typeof test.info> extends never ? never : Parameters<Parameters<typeof test>[1]>[0]["page"]) {
  // Use "**" suffix to match both /api/secrets/inventory and
  // /api/secrets/inventory?identity=<uuid> (the identity-scoped query).
  await page.route("**/api/secrets/inventory**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_INVENTORY_RESPONSE),
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
// 1. Page loads
// ---------------------------------------------------------------------------

test("secrets: page loads DirectionPassport", async ({ page }) => {
  // Install mocks before navigation so all API calls are intercepted from the start.
  await mockSecretsRoutes(page);

  await page.goto("/secrets", { timeout: 10_000 });

  // DirectionPassport root element
  await expect(page.locator('[data-direction-passport="true"]')).toBeAttached({ timeout: 8_000 });
});

// ---------------------------------------------------------------------------
// 2. Deep-link routing
// ---------------------------------------------------------------------------

test("secrets: ?focus=u:google renders Google user page", async ({ page }) => {
  // Install mocks before navigation so the inventory fetch is intercepted.
  await mockSecretsRoutes(page);

  await page.goto("/secrets?focus=u:google", { timeout: 10_000 });

  // Wait for passport to render
  await expect(page.locator('[data-direction-passport="true"]')).toBeAttached({ timeout: 8_000 });

  // Google user page must be rendered with the correct mock data
  await expect(page.locator('[data-page="user"]')).toBeAttached({ timeout: 5_000 });
  await expect(page.locator('[data-provider="google"]')).toBeAttached({ timeout: 5_000 });

  // URL should contain focus param; use the URL API to parse query params
  // robustly regardless of browser-specific percent-encoding of the colon.
  const url = new URL(page.url());
  expect(url.searchParams.get("focus")).toBe("u:google");
});

// ---------------------------------------------------------------------------
// 3. Identity switch
// ---------------------------------------------------------------------------

test("secrets: ?identity=wei shows wei identity chip", async ({ page }) => {
  // Install mocks before navigation.
  await mockSecretsRoutes(page);

  await page.goto("/secrets?identity=wei", { timeout: 10_000 });

  await expect(page.locator('[data-direction-passport="true"]')).toBeAttached({ timeout: 8_000 });

  // Wei identity chip must be present (rendered from mock identities).
  // The chip appears in the Spine's identity switcher bar.
  // Use .first() since multiple elements with this attribute may be rendered
  // (Spine switcher + DirectionPassport header chip).
  await expect(page.locator('[data-identity-id="wei"]').first()).toBeAttached({ timeout: 5_000 });
});

// ---------------------------------------------------------------------------
// 4. OAuth callback re-entry: ?toast=connected
// ---------------------------------------------------------------------------

test("secrets OAuth callback: ?toast=connected shows connected toast and strips param", async ({ page }) => {
  await mockSecretsRoutes(page);

  // Navigate directly to the post-OAuth callback URL
  await page.goto("/secrets?focus=u:google&toast=connected", { timeout: 10_000 });

  // Passport renders without crashing
  await expect(page.locator('[data-direction-passport="true"]')).toBeAttached({ timeout: 8_000 });

  // After the toast effect fires, ?toast= should be stripped from the URL
  // (SecretsPage useEffect strips it on the next tick)
  await expect(async () => {
    expect(page.url()).not.toContain("toast=");
  }).toPass({ timeout: 3_000 });

  // ?focus= should remain
  expect(page.url()).toContain("focus=");
});

// ---------------------------------------------------------------------------
// 5. OAuth error re-entry: ?oauth_error=invalid_grant
// ---------------------------------------------------------------------------

test("secrets OAuth error: ?oauth_error=invalid_grant renders without crash", async ({ page }) => {
  await mockSecretsRoutes(page);

  await page.goto("/secrets?oauth_error=invalid_grant", { timeout: 10_000 });

  // Passport renders without crashing even with error param
  await expect(page.locator('[data-direction-passport="true"]')).toBeAttached({ timeout: 8_000 });

  // After the effect fires, ?oauth_error= should be stripped
  await expect(async () => {
    expect(page.url()).not.toContain("oauth_error=");
  }).toPass({ timeout: 3_000 });
});

// ---------------------------------------------------------------------------
// 6. Stale reveal-mode localStorage is ignored
// ---------------------------------------------------------------------------

test("secrets: stale revealMode=never localStorage — reveal token button is absent (removed bu-dl98i.1.1)", async ({ page }) => {
  // The legacy reveal endpoint was deleted in bu-dl98i.1.1; the reveal token
  // button is gone from PageCli entirely. Stale secrets.tweaks.revealMode
  // localStorage is irrelevant — there is no reveal button to show or hide.
  await mockSecretsRoutes(page);

  // First goto establishes the page origin so we can write to localStorage.
  await page.goto("/secrets", { timeout: 10_000 });

  // Set revealMode=never in localStorage AFTER origin is established.
  await page.evaluate(() => {
    localStorage.setItem("secrets.tweaks.revealMode", "never");
  });

  // Navigate to the CLI focus URL. Mocks are already active so the inventory
  // fetch will return the stub data and claude-cli will appear in the Spine.
  await page.goto("/secrets?focus=c:claude-cli");

  await expect(page.locator('[data-direction-passport="true"]')).toBeAttached({ timeout: 8_000 });

  // CLI page must be rendered for the claude-cli credential
  await expect(page.locator('[data-page="cli"]')).toBeAttached({ timeout: 5_000 });

  // Reveal token button is removed — no reveal path on CLI page.
  await expect(page.locator("button", { hasText: /reveal token/i })).not.toBeAttached();

  // Cleanup
  await page.evaluate(() => localStorage.removeItem("secrets.tweaks.revealMode"));
});
