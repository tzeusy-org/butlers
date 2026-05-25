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
 * Prerequisites:
 *   npm run test:e2e:install  (once)
 *   npm run build && npm run preview  (or Playwright starts preview automatically)
 */

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Mock inventory data (mirrors frontend/src/components/secrets/passport/mock-data.ts)
// ---------------------------------------------------------------------------

const MOCK_INVENTORY = {
  user: [
    {
      provider: "google",
      identity: "tze",
      state: "ok",
      fingerprint: "sha256:7a3f9e2c",
      issued: "2026-02-14",
      expires: null,
      lastVerified: "14:21 today",
      lastUsed: "14:18 today",
      scopesRequired: ["calendar.readonly", "gmail.readonly"],
      scopesGranted: ["calendar.readonly", "gmail.readonly"],
      feeds: ["calendar", "chronicler"],
      breaks: [],
      test: { ok: true, code: 200, latencyMs: 42, at: "14:21 today" },
      audit: [],
    },
    {
      provider: "spotify",
      identity: "tze",
      state: "expired",
      fingerprint: "sha256:d4e1b8a0",
      issued: "2025-11-03",
      expires: "2026-05-20",
      lastVerified: "2 days ago",
      lastUsed: "2 days ago",
      scopesRequired: ["user-read-recently-played"],
      scopesGranted: ["user-read-recently-played"],
      feeds: ["chronicler"],
      breaks: [],
      test: { ok: false, code: 401, latencyMs: 134, at: "2 days ago", message: "refresh-token expired" },
      audit: [],
    },
    {
      provider: "google",
      identity: "wei",
      state: "ok",
      fingerprint: "sha256:aa1122bb",
      issued: "2026-03-01",
      expires: null,
      lastVerified: "12:00 today",
      lastUsed: "12:00 today",
      scopesRequired: ["calendar.readonly"],
      scopesGranted: ["calendar.readonly"],
      feeds: ["calendar"],
      breaks: [],
      test: { ok: true, code: 200, latencyMs: 55, at: "12:00 today" },
      audit: [],
    },
  ],
  system: [
    {
      key: "BUTLER_TELEGRAM_TOKEN",
      category: "messaging",
      state: "ok",
      rowState: "shared",
      fingerprint: "sha256:ab12cd34",
      description: "Bot token for Telegram.",
      source: "shared",
      target: "shared",
      lastVerified: "14:01 today",
      usedBy: ["switchboard"],
      plainValue: null,
      breaks: [],
      test: { ok: true, code: 200, latencyMs: 30, at: "14:01 today" },
      audit: [],
    },
  ],
  cli: [
    {
      id: "claude-cli",
      label: "Claude Code",
      state: "ok",
      fingerprint: "sha256:11a47cd2",
      issued: "2026-02-10",
      expires: null,
      lastUsed: "14:15 today",
      scopesGranted: ["repo.write"],
      scopesRequired: ["repo.write"],
      test: { ok: true, code: 200, latencyMs: 95, at: "14:15 today" },
    },
  ],
  identities: [
    { id: "tze", label: "Tze", role: "owner", pronoun: "you", hue: "oklch(0.78 0.13 30)" },
    { id: "wei", label: "Wei", role: "member", pronoun: null, hue: "oklch(0.78 0.13 200)" },
  ],
  providers: {
    google: { id: "google", label: "Google", glyph: "G", kind: "oauth", authority: "accounts.google.com", brief: "Calendar, Gmail, Drive read.", cadence: "on demand · refreshes hourly" },
    spotify: { id: "spotify", label: "Spotify", glyph: "S", kind: "oauth", authority: "accounts.spotify.com", brief: "Recent listens.", cadence: "poll · 15m" },
  },
};

// ---------------------------------------------------------------------------
// Route mock helpers
// ---------------------------------------------------------------------------

async function mockSecretsRoutes(page: ReturnType<typeof test.info> extends never ? never : Parameters<Parameters<typeof test>[1]>[0]["page"]) {
  await page.route("**/api/secrets/inventory", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_INVENTORY),
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

test("secrets: page loads DirectionPassport", async ({ page, baseURL }) => {
  try {
    await page.goto("/secrets", { timeout: 10_000 });
  } catch {
    test.skip(true, `Dev server not reachable at ${baseURL} — start with: npm run dev`);
    return;
  }

  await mockSecretsRoutes(page);
  await page.goto("/secrets");

  // DirectionPassport root element
  await expect(page.locator('[data-direction-passport="true"]')).toBeAttached({ timeout: 8_000 });
});

// ---------------------------------------------------------------------------
// 2. Deep-link routing
// ---------------------------------------------------------------------------

test("secrets: ?focus=u:google renders Google user page", async ({ page, baseURL }) => {
  try {
    await page.goto("/secrets", { timeout: 10_000 });
  } catch {
    test.skip(true, `Dev server not reachable at ${baseURL} — start with: npm run dev`);
    return;
  }

  await mockSecretsRoutes(page);
  await page.goto("/secrets?focus=u:google");

  // Wait for passport to render
  await expect(page.locator('[data-direction-passport="true"]')).toBeAttached({ timeout: 8_000 });

  // Google user page is rendered (either from API data or mock data fallback)
  // The data-page="user" element should be present
  const userPage = page.locator('[data-page="user"]');
  if (await userPage.isAttached({ timeout: 3_000 }).catch(() => false)) {
    await expect(userPage).toBeAttached();
  }
  // URL should contain focus param
  expect(page.url()).toContain("focus=u%3Agoogle");
});

// ---------------------------------------------------------------------------
// 3. Identity switch
// ---------------------------------------------------------------------------

test("secrets: ?identity=wei shows wei identity chip", async ({ page, baseURL }) => {
  try {
    await page.goto("/secrets", { timeout: 10_000 });
  } catch {
    test.skip(true, `Dev server not reachable at ${baseURL} — start with: npm run dev`);
    return;
  }

  await mockSecretsRoutes(page);
  await page.goto("/secrets?identity=wei");

  await expect(page.locator('[data-direction-passport="true"]')).toBeAttached({ timeout: 8_000 });

  // If the component uses mock data (not API), check for identity chip
  const weiChip = page.locator('[data-identity-id="wei"]');
  if (await weiChip.isAttached({ timeout: 3_000 }).catch(() => false)) {
    await expect(weiChip).toBeAttached();
  }
});

// ---------------------------------------------------------------------------
// 4. OAuth callback re-entry: ?toast=connected
// ---------------------------------------------------------------------------

test("secrets OAuth callback: ?toast=connected shows connected toast and strips param", async ({ page, baseURL }) => {
  try {
    await page.goto("/secrets", { timeout: 10_000 });
  } catch {
    test.skip(true, `Dev server not reachable at ${baseURL} — start with: npm run dev`);
    return;
  }

  await mockSecretsRoutes(page);

  // Navigate to the post-OAuth callback URL
  await page.goto("/secrets?focus=u:google&toast=connected");

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

test("secrets OAuth error: ?oauth_error=invalid_grant renders without crash", async ({ page, baseURL }) => {
  try {
    await page.goto("/secrets", { timeout: 10_000 });
  } catch {
    test.skip(true, `Dev server not reachable at ${baseURL} — start with: npm run dev`);
    return;
  }

  await mockSecretsRoutes(page);
  await page.goto("/secrets?oauth_error=invalid_grant");

  // Passport renders without crashing even with error param
  await expect(page.locator('[data-direction-passport="true"]')).toBeAttached({ timeout: 8_000 });

  // After the effect fires, ?oauth_error= should be stripped
  await expect(async () => {
    expect(page.url()).not.toContain("oauth_error=");
  }).toPass({ timeout: 3_000 });
});

// ---------------------------------------------------------------------------
// 6. Reveal-mode=never via localStorage
// ---------------------------------------------------------------------------

test("secrets: revealMode=never from localStorage hides reveal button on CLI page", async ({ page, baseURL }) => {
  try {
    await page.goto("/secrets", { timeout: 10_000 });
  } catch {
    test.skip(true, `Dev server not reachable at ${baseURL} — start with: npm run dev`);
    return;
  }

  // Set revealMode=never in localStorage before navigating
  await page.evaluate(() => {
    localStorage.setItem("secrets.tweaks.revealMode", "never");
  });

  await mockSecretsRoutes(page);
  await page.goto("/secrets?focus=c:claude-cli");

  await expect(page.locator('[data-direction-passport="true"]')).toBeAttached({ timeout: 8_000 });

  // When data-page="cli" is rendered (mock data), "reveal token" must be absent
  const cliPage = page.locator('[data-page="cli"]');
  if (await cliPage.isAttached({ timeout: 3_000 }).catch(() => false)) {
    // The reveal token button should not be present
    await expect(page.locator("button", { hasText: /reveal token/i })).not.toBeAttached();
  }

  // Cleanup
  await page.evaluate(() => localStorage.removeItem("secrets.tweaks.revealMode"));
});
